"""v1-4-team-coordinator — 端到端集成测试。

端到端跑 `team use --coordinator` → app.active_role='coordinator' →
Agent(role='coordinator', available_tools=白名单)+ Coordinator 能用
Read 读 member outbox.jsonl。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("BAOZICODE_COORDINATOR", raising=False)
    yield


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_app_shim(teams_dir: Path, *, coordinator_enabled: bool = True):
    from baozicode.app import BaoZiCodeApp
    from baozicode.config.schema import (
        AppConfig,
        BackendConfig,
        CoordinatorConfig,
        TeamsConfig,
    )
    from baozicode.teams import TeamsRegistry

    config = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="claude-test"),
        openai=BackendConfig(api_key="x", model="gpt-test"),
        minimax=BackendConfig(api_key="x", model="m-test"),
        deepseek=BackendConfig(api_key="x", model="d-test"),
        teams=TeamsConfig(
            coordinator=CoordinatorConfig(
                enabled=coordinator_enabled,
                env_var="BAOZICODE_COORDINATOR",
            )
        ),
    )
    app = BaoZiCodeApp.__new__(BaoZiCodeApp)
    app.config = config
    app.project_root = teams_dir.parent
    app.active_role = "subagent"
    app.active_coordinator = False
    app.active_team_name = None
    app.mailbox_notifier = None
    app.teams = TeamsRegistry(teams_dir)
    return app


def _create_team_with_member(
    teams_dir: Path,
    team_name: str,
    member_name: str,
    *,
    team_coordinator: bool = False,
) -> Path:
    """建 team + 加 1 个 member + 写一份 outbox.jsonl 模拟 member 已发消息。"""
    from baozicode.teams import Member, TeamsRegistry

    reg = TeamsRegistry(teams_dir)
    reg.create_team(team_name, coordinator=team_coordinator)
    team_dir = teams_dir / team_name
    # 加 member (直接操作 store)
    store = reg.get(team_name)
    member = Member(name=member_name, role="backend", backend="coroutine")
    store.add_member(member)
    # 模拟 member 已发消息
    member_dir = team_dir / member_name
    outbox = member_dir / "outbox.jsonl"
    outbox.write_text(
        json.dumps({"from": member_name, "content": "task done"}) + "\n",
        encoding="utf-8",
    )
    return member_dir


# ---------------------------------------------------------------------------
# 端到端:三锁命中 → use_team → active_role=coordinator → Agent 白名单
# ---------------------------------------------------------------------------


class TestEndToEndCoordinatorMode:
    """端到端:CLI → use_team → active_role=coordinator → 白名单工具。"""

    def test_full_flow_all_locks_hit(
        self,
        tmp_path: Path,
        clean_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        teams_root = tmp_path / "teams"
        _create_team_with_member(
            teams_root,
            team_name="devops",
            member_name="alice",
            team_coordinator=True,
        )
        app = _make_app_shim(teams_root, coordinator_enabled=True)
        app.use_team("devops", coordinator=True)

        # active_role 切到 coordinator
        assert app.active_role == "coordinator"
        assert app.active_coordinator is True

        # Agent(role='coordinator') 构造 + ToolRegistry 白名单
        from baozicode.agent.loop import Agent
        from baozicode.llm.base import LLMClient, Message
        from baozicode.tools.registry import get_default_tool_registry

        class _StubLLM(LLMClient):
            async def stream(self, *args, **kwargs):
                yield Message(role="assistant", content="")
                return

        agent = Agent(
            llm_client=_StubLLM(),
            tools=[],
            conversation=MagicMock(),
            permissions=None,
            config=app.config,
            role=app.active_role,
            tool_registry=get_default_tool_registry(),
        )
        names = {t.name for t in agent.available_tools}
        # 写类工具被剔除
        assert "Write" not in names
        assert "Edit" not in names
        assert "Bash" not in names
        # 读类工具可见
        assert "Read" in names
        assert "Grep" in names
        assert "Glob" in names
        assert "WebFetch" in names
        # team_* 工具可见(注册到全局后)
        # (不强制 — team_* 注册需 in-process 跑 register_team_tools)

        # Coordinator 能读 outbox.jsonl(普通 Read tool)
        outbox_path = teams_root / "devops" / "alice" / "outbox.jsonl"
        assert outbox_path.exists()
        content = outbox_path.read_text(encoding="utf-8")
        assert "task done" in content


# ---------------------------------------------------------------------------
# 端到端:三锁缺失 → use_team → active_role=lead(降级)
# ---------------------------------------------------------------------------


class TestEndToEndDemotePath:
    """端到端:三锁不全 → use_team → 降级 Lead。"""

    def test_demote_to_lead(
        self,
        tmp_path: Path,
        clean_env: None,
        capsys: pytest.CaptureFixture,
    ) -> None:
        # env var 未设(clean_env 已清)
        teams_dir = _create_team_with_member(
            tmp_path / "teams",
            team_name="devops",
            member_name="alice",
            team_coordinator=True,
        )
        app = _make_app_shim(tmp_path / "teams", coordinator_enabled=True)
        app.use_team("devops", coordinator=True)

        assert app.active_role == "lead"
        assert app.active_coordinator is False
        captured = capsys.readouterr()
        assert "env_var" in captured.err
        assert "降级" in captured.err


# ---------------------------------------------------------------------------
# 端到端:不传 coordinator → 走 Lead 路径
# ---------------------------------------------------------------------------


class TestEndToEndLeadPathUnchanged:
    """端到端:不传 --coordinator → 现有 Lead 路径(向后兼容)。"""

    def test_lead_path_default(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        teams_dir = _create_team_with_member(
            tmp_path / "teams",
            team_name="devops",
            member_name="alice",
            team_coordinator=False,  # 旧 team.json 无字段
        )
        app = _make_app_shim(tmp_path / "teams", coordinator_enabled=False)
        app.use_team("devops")  # coordinator=False 默认
        assert app.active_role == "lead"
        assert app.active_coordinator is False
        assert app.active_team_name == "devops"


# ---------------------------------------------------------------------------
# Read 工具读 outbox
# ---------------------------------------------------------------------------


class TestReadOutbox:
    """Coordinator 用 Read 读 member outbox.jsonl — 不需要新工具。"""

    def test_read_outbox_path(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        member_dir = _create_team_with_member(
            tmp_path / "teams",
            team_name="devops",
            member_name="alice",
            team_coordinator=True,
        )
        outbox = member_dir / "outbox.jsonl"
        assert outbox.exists()
        # L2 PathSandbox 已白名单 teams 路径(v1-4-pane-backend)—
        # Coordinator 直接 Read 即可
        from baozicode.tools.read import execute as read_execute

        result = read_execute({"file_path": str(outbox)})
        # read_execute 是 async — 同步上下文 run 一下
        import asyncio

        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        assert "task done" in result.content


# ---------------------------------------------------------------------------
# team_dispatch 仍可调
# ---------------------------------------------------------------------------


class TestTeamDispatchStillAvailable:
    """Coordinator Agent 仍能调 team_dispatch(member 接收消息)。"""

    def test_team_dispatch_in_coordinator_whitelist(self) -> None:
        """role_visibility=['lead','coordinator'] → coordinator 可见。"""
        from baozicode.tools.registry import ToolRegistry
        from baozicode.teams.tools import _TEAM_ROLE_VISIBILITY

        # 真实代码已让 _TEAM_ROLE_VISIBILITY = ['lead', 'coordinator']
        assert "coordinator" in _TEAM_ROLE_VISIBILITY

        # 模拟:给 registry 注册一个 team_dispatch tool,验证 coordinator 看到
        async def _noop(_args):
            from baozicode.tools.base import ToolResult
            return ToolResult(tool_call_id="x", content="ok")

        from baozicode.tools.base import ToolDefinition

        team_dispatch = ToolDefinition(
            name="team_dispatch",
            description="dispatch",
            parameters={"type": "object", "properties": {}},
            role_visibility=list(_TEAM_ROLE_VISIBILITY),
        )
        r = ToolRegistry()
        r._mcp_tools["team_dispatch"] = team_dispatch
        r._executors["team_dispatch"] = _noop
        names = {t.name for t in r.get_all_tools(role="coordinator")}
        assert "team_dispatch" in names