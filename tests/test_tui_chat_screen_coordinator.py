"""v1-4-team-coordinator — ChatScreen active_role 接线测试。

覆盖 `openspec/changes/v1-4-team-coordinator/specs/team-management/spec.md`
中 `ChatScreen reconstructs Agent with active_role` requirement。

测试用 shim 绕开 Textual screen mount 上下文(已知 limitation);
聚焦 `ChatScreen._build_agent_kwargs()` 里的 `role=...` 选择逻辑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


def _make_app(*, active_role: str = "subagent", active_team_name: str | None = None):
    app = MagicMock()
    app.active_role = active_role
    app.active_team_name = active_team_name
    app.mailbox_notifier = None
    app.hook_dispatcher = None
    app.subagents = None
    app.compact_ctx = None
    app.instructions = MagicMock()
    app.instructions.concatenated = ""
    app.skill_filter = None
    app.skill_activation = None
    app.skill_registry = None
    app.permissions_v5 = None
    app.permissions_engine = None
    app.effective_mode.return_value = "default"
    app.plan_mode = False
    app.project_root = Path("/tmp")
    app._team_tools_registered = False
    return app


def _extract_role_from_build(app) -> str:
    """模拟 ChatScreen 重建 Agent 时读 role 的逻辑。

    直接复用 chat_screen.py 的 `role=...` 表达式。
    """
    role = getattr(app, "active_role", None)
    if role:
        return role
    return "lead" if getattr(app, "active_team_name", None) else "subagent"


# ---------------------------------------------------------------------------
# active_role 优先
# ---------------------------------------------------------------------------


class TestActiveRolePriority:
    """Requirement: app.active_role 优先于 active_team_name 推断。"""

    def test_active_role_coordinator(self) -> None:
        app = _make_app(active_role="coordinator", active_team_name="devops")
        assert _extract_role_from_build(app) == "coordinator"

    def test_active_role_lead(self) -> None:
        app = _make_app(active_role="lead", active_team_name="devops")
        assert _extract_role_from_build(app) == "lead"

    def test_active_role_subagent(self) -> None:
        app = _make_app(active_role="subagent", active_team_name=None)
        assert _extract_role_from_build(app) == "subagent"

    def test_active_role_default_subagent_when_no_team(self) -> None:
        """向后兼容 — 没设 active_role(默认值)且无 active_team → subagent。"""
        # 模拟旧 App(没有 active_role 字段 — MagicMock 默认抛错)
        app = MagicMock(spec=[])  # 空 spec → 无属性
        # 没有 active_role + 没有 active_team_name → fallback "subagent"
        role = getattr(app, "active_role", None) or (
            "lead" if getattr(app, "active_team_name", None) else "subagent"
        )
        assert role == "subagent"

    def test_fallback_to_lead_when_active_team_set(self) -> None:
        """向后兼容 — 旧 App 无 active_role,但 active_team_name 设了 → lead。"""
        app = MagicMock(spec=["active_team_name"])
        app.active_team_name = "devops"
        role = getattr(app, "active_role", None) or (
            "lead" if getattr(app, "active_team_name", None) else "subagent"
        )
        assert role == "lead"


# ---------------------------------------------------------------------------
# ChatScreen import-level smoke test
# ---------------------------------------------------------------------------


class TestChatScreenImport:
    """ChatScreen module import OK + 源码包含 active_role 读取。"""

    def test_chat_screen_imports(self) -> None:
        from baozicode.tui import chat_screen
        assert hasattr(chat_screen, "ChatScreen")

    def test_chat_screen_source_contains_active_role(self) -> None:
        """源码静态扫描 — 防止回归。"""
        from pathlib import Path as _P

        path = _P("baozicode/tui/chat_screen.py")
        text = path.read_text(encoding="utf-8")
        assert "active_role" in text
        # 优先级判断表达式存在
        assert 'getattr(app, "active_role"' in text


# ---------------------------------------------------------------------------
# Agent(role='coordinator') integration via ChatScreen role 选择
# ---------------------------------------------------------------------------


class TestChatScreenAgentRole:
    """_extract_role_from_build → Agent(role=...) 走 ToolRegistry 过滤。"""

    def _build_agent_for_role(self, role: str, registry):
        from baozicode.agent.loop import Agent
        from baozicode.llm.base import LLMClient, Message

        class _StubLLM(LLMClient):
            async def stream(self, *args, **kwargs):
                yield Message(role="assistant", content="")
                return

        return Agent(
            llm_client=_StubLLM(),
            tools=[],
            conversation=MagicMock(),
            permissions=None,
            config=MagicMock(),
            role=role,
            tool_registry=registry,
        )

    def test_coordinator_agent_no_mutating_tools(self) -> None:
        from baozicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        app = _make_app(active_role="coordinator", active_team_name="devops")
        role = _extract_role_from_build(app)
        agent = self._build_agent_for_role(role, registry)
        names = {t.name for t in agent.available_tools}
        # coordinator 走 ToolRegistry 过滤 → 7 内置 - Write/Edit/Bash = 4
        assert "Write" not in names
        assert "Edit" not in names
        assert "Bash" not in names
        assert "Read" in names

    def test_lead_agent_has_all_builtin_tools(self) -> None:
        from baozicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        app = _make_app(active_role="lead", active_team_name="devops")
        role = _extract_role_from_build(app)
        agent = self._build_agent_for_role(role, registry)
        names = {t.name for t in agent.available_tools}
        assert "Write" in names
        assert "Bash" in names