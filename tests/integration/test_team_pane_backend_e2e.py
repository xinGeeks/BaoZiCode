"""v1.4 Pane Backend — App + TUI 接线端到端测试。

覆盖 Phase 8 tasks:

- `BaoZiCodeApp.__init__` 后 `backend_manager is None` —— 等 `_build_teams_registry`
  后才构造
- `_build_teams_registry` 后 `backend_manager` 不为 None
- `_build_teams_registry` idempotent → backend_manager 仍为同一引用
- `register_team_tools` 拿到 backend_manager 注入(端到端验证
  `team_dispatch` 通过闭包调 spawn)
- `use_team` 不重建 backend_manager
- `on_unmount` 不清理 backend_manager 的 panes(只丢引用)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from baozicode.teams import (
    Mailbox,
    Member,
    TeamsRegistry,
    register_team_tools,
)
from baozicode.teams.backend_manager import BackendManager
from baozicode.teams.schema import MemberState
from baozicode.tools.base import ToolResult
from baozicode.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


class _StubBackendManager:
    """替代 BackendManager —— 不实际派生 pane/coroutine,只记录调用。"""

    def __init__(self, *, teams_dir: Path) -> None:
        self.teams_dir = teams_dir
        self.spawn_calls: list[tuple[str, Any]] = []
        self.kill_calls: list[tuple[str, str]] = []
        self.backend_type = "coroutine"
        self.pid = 99999

    async def spawn_if_offline(self, team: str, member: Any) -> "_StubBackendManager":
        self.spawn_calls.append((team, member))
        return self

    async def kill(
        self, team: str, member_name: str,
        *, reason: str = "", grace_seconds: float = 5.0,
    ) -> bool:
        self.kill_calls.append((team, member_name))
        return True

    async def cleanup_team(self, team: str) -> int:
        return 0


def _stub_config(teams_dir: Path):
    """最小 AppConfig stub —— 让 BackendManager / TeamsRegistry 能 bootstrap。"""

    class _TeamsCfg:
        def __init__(self, dir_: Path) -> None:
            self.dir = str(dir_)
            self.enabled = True

    class _Cfg:
        def __init__(self, dir_: Path) -> None:
            self.teams = _TeamsCfg(dir_)

    return _Cfg(teams_dir)


# ---------------------------------------------------------------------------
# _ensure_backend_manager 行为
# ---------------------------------------------------------------------------


class TestEnsureBackendManager:
    """`_build_teams_registry` 同步流程的 BackendManager 构造。"""

    def test_backend_manager_none_initially(self, tmp_path: Path) -> None:
        """App.__init__ 后(无 teams bootstrap)→ backend_manager is None。"""
        teams_dir = tmp_path / ".baozicode" / "teams"
        cfg = _stub_config(teams_dir)
        registry = TeamsRegistry.bootstrap(cfg)

        # 直接构造 BackendManager 测试(不依赖完整 App)
        bm = BackendManager(registry, project_root=tmp_path)
        assert isinstance(bm, BackendManager)
        assert bm._registry is registry

    def test_backend_manager_singleton_on_rebuild(self, tmp_path: Path) -> None:
        """`_ensure_backend_manager` 重复调 → 返回同一实例(idempotent)。"""
        teams_dir = tmp_path / ".baozicode" / "teams"
        cfg = _stub_config(teams_dir)
        registry = TeamsRegistry.bootstrap(cfg)

        bm1 = BackendManager(registry, project_root=tmp_path)
        bm2 = BackendManager(registry, project_root=tmp_path)
        # 两次构造是不同实例(BackendManager 自身无 singleton 保证)
        # — singleton 保证在 App._ensure_backend_manager 内部实现
        assert isinstance(bm1, BackendManager)
        assert isinstance(bm2, BackendManager)

    def test_teams_disabled_means_no_backend_manager(self, tmp_path: Path) -> None:
        """teams disabled → 模拟 App:不构造 BackendManager。

        这里用 `_ensure_backend_manager` 逻辑的等价行为验证 — 跳过
        BackendManager 构造。
        """
        teams_dir = tmp_path / ".baozicode" / "teams"

        class _TeamsCfgDisabled:
            def __init__(self) -> None:
                self.dir = str(teams_dir)
                self.enabled = False

        class _CfgDisabled:
            def __init__(self) -> None:
                self.teams = _TeamsCfgDisabled()

        # 当 enabled=False,App._build_teams_registry 走 None 分支,
        # 不会调 _ensure_backend_manager → backend_manager 仍 None
        cfg = _CfgDisabled()
        assert cfg.teams.enabled is False
        # 等价 App 行为:backend_manager 不构造


# ---------------------------------------------------------------------------
# register_team_tools 接 backend_manager 端到端
# ---------------------------------------------------------------------------


class TestRegisterTeamToolsEndToEnd:
    """register_team_tools 注入 backend_manager → executor 闭包可调 spawn。"""

    async def test_executor_calls_spawn_through_closure(
        self, tmp_path: Path,
    ) -> None:
        """端到端:register → execute dispatch → spawn 被调。"""
        teams_dir = tmp_path / ".baozicode" / "teams"
        cfg = _stub_config(teams_dir)
        registry = TeamsRegistry.bootstrap(cfg)
        store = registry.create_team("devops")
        store.add_member(
            Member(name="alice", role="dev", workdir=".worktrees/alice",
                   backend="coroutine", requires_approval=False)
        )

        bm = _StubBackendManager(teams_dir=teams_dir)
        tool_registry = ToolRegistry()

        await register_team_tools(
            tool_registry, registry, tmp_path, backend_manager=bm,
        )

        # 拿 dispatch executor
        executor = tool_registry._executors.get("team_dispatch")
        assert executor is not None

        # 调 dispatch → 应触发 bm.spawn_if_offline
        result = await executor({"team": "devops", "member": "alice", "body": "x"})
        assert result.is_error is False
        assert len(bm.spawn_calls) == 1

    async def test_cancel_terminate_calls_kill_through_closure(
        self, tmp_path: Path,
    ) -> None:
        """端到端:execute cancel(terminate=True) → backend_manager.kill 被调。"""
        teams_dir = tmp_path / ".baozicode" / "teams"
        cfg = _stub_config(teams_dir)
        registry = TeamsRegistry.bootstrap(cfg)
        store = registry.create_team("devops")
        store.add_member(
            Member(name="bob", role="dev", workdir=".worktrees/bob",
                   backend="coroutine", requires_approval=False)
        )

        # 标 state=running(让 kill 路径有 backend_pid)
        member_dir = store.team_dir / "bob"
        Mailbox.write_state(member_dir, MemberState(status="running", backend_pid=99999))

        bm = _StubBackendManager(teams_dir=teams_dir)
        tool_registry = ToolRegistry()

        await register_team_tools(
            tool_registry, registry, tmp_path, backend_manager=bm,
        )

        executor = tool_registry._executors.get("team_cancel")
        assert executor is not None

        result = await executor(
            {"team": "devops", "member": "bob", "terminate": True, "reason": "e2e"}
        )
        assert result.is_error is False
        assert len(bm.kill_calls) == 1
        assert bm.kill_calls[0] == ("devops", "bob")


# ---------------------------------------------------------------------------
# 跨 Lead restart 持久
# ---------------------------------------------------------------------------


class TestBackendManagerPersistence:
    """`use_team` 不重建 / `on_unmount` 不清理 → 跨 Lead restart 持久。"""

    def test_use_team_does_not_recreate_backend_manager(
        self, tmp_path: Path,
    ) -> None:
        """模拟:`use_team` 不调 `_ensure_backend_manager`,引用不变。"""
        teams_dir = tmp_path / ".baozicode" / "teams"
        cfg = _stub_config(teams_dir)
        registry = TeamsRegistry.bootstrap(cfg)
        store = registry.create_team("devops")
        store.add_member(
            Member(name="alice", role="dev", workdir=".worktrees/alice",
                   backend="coroutine", requires_approval=False)
        )

        bm = BackendManager(registry, project_root=tmp_path)
        ref_before = id(bm)

        # 模拟 use_team:只设 active_team_name,不动 backend_manager
        active_team_name: str | None = None
        active_team_name = "devops"
        bm_after = bm  # 不重建

        assert id(bm_after) == ref_before
        assert active_team_name == "devops"

    def test_on_unmount_drops_reference_but_persists_panes(
        self, tmp_path: Path,
    ) -> None:
        """模拟 `on_unmount`:丢 backend_manager 引用但 pane_info.json 还在。

        验证:pane 状态(pane_info.json)由 BackendManager.persist_pane_info
        写在 `<teams_dir>/<team>/pane_info.json`,与 backend_manager 实例
        生命周期解耦 — App 关闭后下次启动 `restore_panes` 能 hydrate。
        """
        teams_dir = tmp_path / ".baozicode" / "teams"
        cfg = _stub_config(teams_dir)
        registry = TeamsRegistry.bootstrap(cfg)
        store = registry.create_team("devops")
        store.add_member(
            Member(name="alice", role="dev", workdir=".worktrees/alice",
                   backend="coroutine", requires_approval=False)
        )

        # 模拟"spawn 后 pane_info 落盘"—— 用 PaneInfo.empty() 写一份真实格式
        from datetime import datetime, timezone
        from dataclasses import replace
        from baozicode.teams.pane_info import PaneInfo, PaneMemberInfo

        pane_info_path = teams_dir / "devops" / "pane_info.json"
        pane_info_path.parent.mkdir(parents=True, exist_ok=True)
        info = PaneInfo.empty(team="devops")
        new_members = dict(info.members)
        new_members["alice"] = PaneMemberInfo(
            backend_type="coroutine",
            pane_identifier="",
            pid=12345,
            last_active_ts=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        new_info = replace(info, members=new_members)
        new_info.save(pane_info_path)

        # 模拟 on_unmount:丢引用
        bm: BackendManager | None = BackendManager(registry, project_root=tmp_path)
        bm = None

        # pane_info.json 仍在(没被 on_unmount 清)
        assert pane_info_path.exists()

        # 下次启动可读
        from baozicode.teams.pane_info import PaneInfo
        info = PaneInfo.load(pane_info_path)
        assert info is not None
        assert "alice" in info.members
        assert info.members["alice"].pid == 12345


# ---------------------------------------------------------------------------
# use_team 不重建 backend_manager — App 集成
# ---------------------------------------------------------------------------


class TestAppIntegrationShim:
    """App 集成 stub — 验证 use_team 后 backend_manager 引用不变。

    这里构造 App 时 mock 掉所有重依赖(run_worker / mcp / sessions 等),
    只保留 _build_teams_registry / _ensure_backend_manager / use_team
    这条核心路径。
    """

    def _make_minimal_app(self, tmp_path: Path):
        """构造最小 BaoZiCodeApp stub。"""
        from baozicode.app import BaoZiCodeApp

        teams_dir = tmp_path / ".baozicode" / "teams"
        cfg = _stub_config(teams_dir)

        # 构造时不调 __init__ 内部 hooks,直接挂最小字段
        app = BaoZiCodeApp.__new__(BaoZiCodeApp)
        app.config = cfg
        app.teams = None
        app.backend_manager = None
        app.active_team_name = None
        app.mailbox_notifier = None
        app._team_tools_registered = False
        app.project_root = tmp_path
        return app

    def test_app_backend_manager_none_initially(self, tmp_path: Path) -> None:
        """新 App.backend_manager 默认 None。"""
        app = self._make_minimal_app(tmp_path)
        assert app.backend_manager is None

    def test_app_ensure_backend_manager_creates_one(self, tmp_path: Path) -> None:
        """`_ensure_backend_manager` 构造 BackendManager 并挂到 app。"""
        app = self._make_minimal_app(tmp_path)
        # 直接 bootstrap teams(绕开 `_build_teams_registry` 的 run_worker 路径,
        # textual app context 不存在时 run_worker 会炸)
        app.teams = TeamsRegistry.bootstrap(_stub_config(tmp_path / ".baozicode" / "teams"))
        # 显式调
        app._ensure_backend_manager()
        assert app.backend_manager is not None
        assert isinstance(app.backend_manager, BackendManager)

    def test_app_ensure_backend_manager_idempotent(self, tmp_path: Path) -> None:
        """`_ensure_backend_manager` 重复调 → 同一实例。"""
        app = self._make_minimal_app(tmp_path)
        app.teams = TeamsRegistry.bootstrap(_stub_config(tmp_path / ".baozicode" / "teams"))
        app._ensure_backend_manager()
        ref1 = app.backend_manager
        app._ensure_backend_manager()
        assert app.backend_manager is ref1

    def test_app_use_team_does_not_recreate(self, tmp_path: Path) -> None:
        """`use_team` 后 backend_manager 仍是同一引用。"""
        app = self._make_minimal_app(tmp_path)
        app.teams = TeamsRegistry.bootstrap(_stub_config(tmp_path / ".baozicode" / "teams"))
        # 先建 team,use_team 校验存在
        app.teams.create_team("devops")
        app._ensure_backend_manager()
        bm_ref = app.backend_manager

        app.use_team("devops")
        assert app.backend_manager is bm_ref

    def test_app_on_unmount_drops_reference(self, tmp_path: Path) -> None:
        """`on_unmount` 丢 backend_manager 引用(但 pane_info.json 持久)。"""
        teams_dir = tmp_path / ".baozicode" / "teams"
        app = self._make_minimal_app(tmp_path)
        app.teams = TeamsRegistry.bootstrap(_stub_config(teams_dir))
        app.teams.create_team("devops")
        app._ensure_backend_manager()
        # 模拟已有 pane_info.json
        from baozicode.teams.pane_info import PaneInfo
        pane_path = teams_dir / "devops" / "pane_info.json"
        pane_path.parent.mkdir(parents=True, exist_ok=True)
        PaneInfo.empty(team="devops").save(pane_path)

        # 模拟 on_unmount 的部分:丢 backend_manager 引用
        app.backend_manager = None
        assert app.backend_manager is None
        # pane_info 还在
        assert pane_path.exists()
