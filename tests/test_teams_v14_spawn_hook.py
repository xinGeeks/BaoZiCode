"""v1.4 Pane Backend — team-tools spawn hook 测试。

覆盖 Phase 7 tasks:

- `team_dispatch` 末尾调 `backend_manager.spawn_if_offline(team, member)`
- `team_dispatch` 返回 content 含 `backend=<type>`
- `team_dispatch` 没注入 backend_manager → 不 spawn,跳过 spawn 调用
- `team_dispatch` backend_manager 抛错 → 不阻断 dispatch 主体
- `team_cancel(terminate=True)` 调 `backend_manager.kill(team, member)`
- `team_cancel(terminate=True)` 没注入 backend_manager → fallback 走
  裸 `os.kill(state.backend_pid, SIGTERM)`(v1-4-team-tools 阶段行为)
- `register_team_tools` 接 backend_manager kwarg;注入后 spawn hook 生效
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

import pytest

from baozicode.teams import (
    Mailbox,
    TeamStore,
    TeamsRegistry,
    register_team_tools,
)
from baozicode.teams.registry import TeamsRegistry as TR
from baozicode.teams.schema import Member, MemberState
from baozicode.teams.tools import (
    execute_team_cancel,
    execute_team_dispatch,
)
from baozicode.tools.base import ToolDefinition, ToolResult
from baozicode.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def teams_root(tmp_path: Path) -> Path:
    d = tmp_path / "teams"
    d.mkdir()
    return d


@pytest.fixture
def registry(teams_root: Path) -> TR:
    return TR(teams_root)


@pytest.fixture
def team_with_alice(registry: TR) -> TeamStore:
    store = registry.create_team("devops")
    store.add_member(
        Member(name="alice", role="dev", workdir=".worktrees/alice",
               backend="coroutine", requires_approval=False)
    )
    return store


@pytest.fixture
def member_dir(team_with_alice: TeamStore) -> Path:
    return team_with_alice.team_dir / "alice"


# ---------------------------------------------------------------------------
# Stub BackendManager / handle
# ---------------------------------------------------------------------------


class _StubHandle:
    """仿 BackendHandle —— 仅 backend_type / is_alive / kill。"""

    def __init__(self, backend_type: str = "coroutine", pid: int | None = None) -> None:
        self.backend_type = backend_type
        self.pid = pid

    def is_alive(self) -> bool:
        return True

    def kill(self, *, grace_seconds: float = 5.0) -> None:
        pass


class _StubBackendManager:
    """仿 BackendManager —— 记录 spawn / kill 调用。"""

    def __init__(self, *, spawn_raises: Exception | None = None) -> None:
        self.spawn_calls: list[tuple[str, Member]] = []
        self.kill_calls: list[tuple[str, str, str, float]] = []
        self.spawn_raises = spawn_raises

    async def spawn_if_offline(self, team: str, member: Member) -> _StubHandle:
        self.spawn_calls.append((team, member))
        if self.spawn_raises is not None:
            raise self.spawn_raises
        return _StubHandle(backend_type="coroutine", pid=99999)

    async def kill(
        self,
        team: str,
        member_name: str,
        *,
        reason: str = "",
        grace_seconds: float = 5.0,
    ) -> bool:
        self.kill_calls.append((team, member_name, reason, grace_seconds))
        return True


# ---------------------------------------------------------------------------
# team_dispatch spawn hook
# ---------------------------------------------------------------------------


class TestDispatchSpawnHook:
    """`team_dispatch` 末尾调 backend_manager.spawn_if_offline。"""

    async def test_dispatch_calls_spawn_if_offline(
        self, team_with_alice: TeamStore, registry: TR, member_dir: Path,
    ) -> None:
        """backend_manager 注入 → dispatch 末尾调 spawn。"""
        bm = _StubBackendManager()

        result = await execute_team_dispatch(
            {"team": "devops", "member": "alice", "body": "do thing"},
            teams_registry=registry,
            project_root=Path("/tmp"),
            backend_manager=bm,
        )

        assert result.is_error is False
        assert len(bm.spawn_calls) == 1
        team, member = bm.spawn_calls[0]
        assert team == "devops"
        assert member.name == "alice"

    async def test_dispatch_returns_backend_type(
        self, team_with_alice: TeamStore, registry: TR,
    ) -> None:
        """ToolResult content 含 `backend=<type>` 片段。"""
        bm = _StubBackendManager()

        result = await execute_team_dispatch(
            {"team": "devops", "member": "alice"},
            teams_registry=registry,
            project_root=Path("/tmp"),
            backend_manager=bm,
        )

        assert "backend=coroutine" in result.content

    async def test_dispatch_without_backend_manager_skips_spawn(
        self, team_with_alice: TeamStore, registry: TR,
    ) -> None:
        """backend_manager=None → 不 spawn,dispatch 主体照常完成。"""
        result = await execute_team_dispatch(
            {"team": "devops", "member": "alice", "body": "hi"},
            teams_registry=registry,
            project_root=Path("/tmp"),
            backend_manager=None,
        )

        assert result.is_error is False
        assert "backend=" not in result.content
        # inbox 仍被写入 — spawn 是 hook,主体逻辑不能断
        inbox_path = team_with_alice.team_dir / "alice"
        msgs = Mailbox.read_messages(inbox_path, "inbox")
        assert len(msgs) == 1
        assert msgs[0].body == "hi"

    async def test_dispatch_spawn_failure_does_not_block(
        self, team_with_alice: TeamStore, registry: TR,
    ) -> None:
        """backend_manager.spawn 抛错 → 不阻断 dispatch,主路径仍成功。"""
        bm = _StubBackendManager(spawn_raises=RuntimeError("spawn boom"))

        result = await execute_team_dispatch(
            {"team": "devops", "member": "alice", "body": "task"},
            teams_registry=registry,
            project_root=Path("/tmp"),
            backend_manager=bm,
        )

        assert result.is_error is False
        # 没有 backend= 片段(spawn 失败)
        assert "backend=" not in result.content


# ---------------------------------------------------------------------------
# team_cancel kill hook
# ---------------------------------------------------------------------------


class TestCancelKillHook:
    """`team_cancel(terminate=True)` 调 backend_manager.kill。"""

    async def test_cancel_terminate_uses_backend_manager_kill(
        self, team_with_alice: TeamStore, registry: TR, member_dir: Path,
    ) -> None:
        """terminate=True + backend_manager 注入 → 走 .kill(),不调 os.kill。"""
        # 1) 先标 state=running + backend_pid 占位
        Mailbox.write_state(
            member_dir,
            MemberState(status="running", backend_pid=os.getpid()),
        )
        bm = _StubBackendManager()

        result = await execute_team_cancel(
            {"team": "devops", "member": "alice", "terminate": True,
             "reason": "test"},
            teams_registry=registry,
            project_root=Path("/tmp"),
            backend_manager=bm,
        )

        assert result.is_error is False
        assert "terminated" in result.content
        # backend_manager.kill 被调一次
        assert len(bm.kill_calls) == 1
        team, member_name, reason, grace = bm.kill_calls[0]
        assert team == "devops"
        assert member_name == "alice"
        assert reason == "test"
        assert grace == 5.0

    async def test_cancel_terminate_falls_back_to_os_kill(
        self, team_with_alice: TeamStore, registry: TR, member_dir: Path,
        monkeypatch,
    ) -> None:
        """terminate=True + 无 backend_manager → fallback 走裸 os.kill。

        桩 `os.kill` 验证被调;SIGTERM 是 v1-4-team-tools 阶段行为。
        """
        # 标 state = running + backend_pid
        Mailbox.write_state(
            member_dir,
            MemberState(status="running", backend_pid=os.getpid()),
        )
        calls: list[tuple[int, int]] = []

        def fake_kill(pid: int, sig: int) -> None:
            calls.append((pid, sig))

        monkeypatch.setattr("os.kill", fake_kill)

        result = await execute_team_cancel(
            {"team": "devops", "member": "alice", "terminate": True,
             "reason": "no-bm"},
            teams_registry=registry,
            project_root=Path("/tmp"),
            backend_manager=None,
        )

        assert result.is_error is False
        # os.kill 被调一次,SIGTERM
        assert len(calls) == 1
        assert calls[0][1] == signal.SIGTERM
        # backend_manager.kill 没被调
        # (没有 bm 时 _StubBackendManager 也不存在,这里只验 os.kill 路径)

    async def test_cancel_non_terminate_does_not_kill(
        self, team_with_alice: TeamStore, registry: TR,
    ) -> None:
        """terminate=False(默认)→ 只写 cancel 消息 + touch_wake;不调 kill。"""
        bm = _StubBackendManager()

        result = await execute_team_cancel(
            {"team": "devops", "member": "alice", "reason": "soft"},
            teams_registry=registry,
            project_root=Path("/tmp"),
            backend_manager=bm,
        )

        assert result.is_error is False
        assert "canceled" in result.content
        # 软取消不调 kill
        assert bm.kill_calls == []


# ---------------------------------------------------------------------------
# register_team_tools 接 backend_manager
# ---------------------------------------------------------------------------


class TestRegisterTeamToolsWithBackendManager:
    """`register_team_tools` 把 backend_manager 透传给 executor 闭包。"""

    async def test_register_with_backend_manager_succeeds(
        self, team_with_alice: TeamStore, registry: TR,
    ) -> None:
        """backend_manager 注入后注册成功,不抛。"""
        tool_registry = ToolRegistry()
        bm = _StubBackendManager()

        defs = await register_team_tools(
            tool_registry,
            registry,
            Path("/tmp"),
            backend_manager=bm,
        )

        assert len(defs) == 6
        # team_dispatch / team_cancel 已注册
        assert tool_registry.get_tool("team_dispatch") is not None
        assert tool_registry.get_tool("team_cancel") is not None

    async def test_register_without_backend_manager_default(
        self, team_with_alice: TeamStore, registry: TR,
    ) -> None:
        """不传 backend_manager → 走 None 默认值,注册仍成功。"""
        tool_registry = ToolRegistry()

        defs = await register_team_tools(
            tool_registry, registry, Path("/tmp"),
        )

        assert len(defs) == 6
        assert tool_registry.get_tool("team_dispatch") is not None

    async def test_registered_executor_invokes_spawn_on_dispatch(
        self, team_with_alice: TeamStore, registry: TR,
    ) -> None:
        """注册后的 executor 闭包真的调 spawn(端到端验证闭包捕获了 bm)。"""
        tool_registry = ToolRegistry()
        bm = _StubBackendManager()

        await register_team_tools(
            tool_registry, registry, Path("/tmp"), backend_manager=bm,
        )

        # 拿 dispatch executor
        executor = tool_registry._executors.get("team_dispatch")  # type: ignore[attr-defined]
        assert executor is not None

        result = await executor(
            {"team": "devops", "member": "alice", "body": "x"},
        )

        assert result.is_error is False
        # spawn 被调
        assert len(bm.spawn_calls) == 1
        assert bm.spawn_calls[0][0] == "devops"
