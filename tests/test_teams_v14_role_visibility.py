"""v1.4 Team Tools — `ToolDefinition.role_visibility` + `ToolRegistry.get_all_tools(role)` + Agent role 过滤测试。

覆盖 `openspec/changes/v1-4-team-tools/specs/tool-calling/spec.md`:

- `ToolDefinition.role_visibility` 默认 None / 接受合法角色 / 拒未知角色
- `ToolRegistry.get_all_tools(role=None)` 老路径兼容
- `ToolRegistry.get_all_tools(role='lead')` 返 team_* + 全员工具
- `ToolRegistry.get_all_tools(role='member'|'subagent')` 不返 team_*
- `Agent(role='subagent')` 默认;`role='lead'` 拿到 team_*;`role='member'`
  拿不到;`role='coordinator'` 允许但当前无专属工具
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from baozicode.agent.loop import Agent
from baozicode.tools.base import AGENT_ROLES, ToolDefinition
from baozicode.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _td(name: str, *, role_visibility=None) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"tool {name}",
        parameters={"type": "object", "properties": {}},
        role_visibility=role_visibility,
    )


@pytest.fixture
def builtin_set() -> list[ToolDefinition]:
    """模拟 7 个内置工具,全部 role_visibility=None(向后兼容默认)。"""
    return [_td(n) for n in ("Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch")]


@pytest.fixture
def team_tools() -> list[ToolDefinition]:
    """模拟 6 个 team_* 协作工具,全部 role_visibility=['lead']。"""
    return [
        _td("team_dispatch", role_visibility=["lead"]),
        _td("team_send_message", role_visibility=["lead"]),
        _td("team_cancel", role_visibility=["lead"]),
        _td("team_merge", role_visibility=["lead"]),
        _td("team_task_create", role_visibility=["lead"]),
        _td("team_task_query", role_visibility=["lead"]),
    ]


@pytest.fixture
def registry(builtin_set, team_tools) -> ToolRegistry:
    """内置 + team 工具的混合 registry。"""
    r = ToolRegistry()
    # 替换默认 builtin 为我们控制的 set,再手动 inject team tools
    for t in team_tools:
        r._mcp_tools[t.name] = t  # noqa: SLF001 — 测试辅助
        r._executors[t.name] = _noop_executor  # noqa: SLF001
    return r


async def _noop_executor(_args: dict) -> Any:
    from baozicode.tools.base import ToolResult
    return ToolResult(tool_call_id="x", content="ok")


# ---------------------------------------------------------------------------
# ToolDefinition.role_visibility
# ---------------------------------------------------------------------------


class TestRoleVisibilityDefault:
    """Requirement: ToolDefinition 默认 role_visibility=None。"""

    def test_default_none(self) -> None:
        t = _td("Read")
        assert t.role_visibility is None

    def test_all_seven_builtins_default_none(self, builtin_set) -> None:
        for t in builtin_set:
            assert t.role_visibility is None


class TestRoleVisibilityValidation:
    """Requirement: 已知角色合法;未知角色 / 非 list 拒。"""

    @pytest.mark.parametrize("role", sorted(AGENT_ROLES))
    def test_each_known_role_accepted(self, role: str) -> None:
        t = _td("t", role_visibility=[role])
        assert t.role_visibility == [role]

    def test_multiple_roles_accepted(self) -> None:
        t = _td("t", role_visibility=["lead", "coordinator"])
        assert t.role_visibility == ["lead", "coordinator"]

    def test_unknown_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知角色"):
            _td("t", role_visibility=["superuser"])

    def test_non_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="必须是 list"):
            _td("t", role_visibility="lead")  # type: ignore[arg-type]

    def test_mixed_known_unknown_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知角色"):
            _td("t", role_visibility=["lead", "hacker"])


# ---------------------------------------------------------------------------
# ToolRegistry.get_all_tools(role)
# ---------------------------------------------------------------------------


class TestGetAllToolsBackwardCompat:
    """Requirement: role=None 走老路径,返全部(v1.3 行为)。"""

    def test_none_returns_all(self, registry, team_tools) -> None:
        all_tools = registry.get_all_tools(role=None)
        names = {t.name for t in all_tools}
        assert "Read" in names
        assert "team_dispatch" in names
        assert len(names) == 7 + 6

    def test_no_arg_returns_all(self, registry) -> None:
        all_tools = registry.get_all_tools()
        names = {t.name for t in all_tools}
        assert "Read" in names
        assert "team_dispatch" in names


class TestGetAllToolsRoleFiltered:
    """Requirement: 按 role 过滤 team_* vs builtin。"""

    def test_lead_sees_team_tools(self, registry, team_tools) -> None:
        lead_tools = registry.get_all_tools(role="lead")
        names = {t.name for t in lead_tools}
        assert "Read" in names
        for t in team_tools:
            assert t.name in names

    def test_member_does_not_see_team_tools(self, registry, team_tools) -> None:
        member_tools = registry.get_all_tools(role="member")
        names = {t.name for t in member_tools}
        for t in team_tools:
            assert t.name not in names
        # 7 个内置仍在
        for n in ("Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch"):
            assert n in names

    def test_subagent_does_not_see_team_tools(self, registry, team_tools) -> None:
        sub_tools = registry.get_all_tools(role="subagent")
        names = {t.name for t in sub_tools}
        for t in team_tools:
            assert t.name not in names

    def test_coordinator_currently_sees_only_builtins(self, registry, team_tools) -> None:
        """当前没有 coordinator 专属工具;team_* role_visibility=['lead']
        不含 coordinator,所以 coordinator 看不到 — 直到 future 提案加
        role_visibility=['lead','coordinator'] 才扩展。"""
        coord_tools = registry.get_all_tools(role="coordinator")
        names = {t.name for t in coord_tools}
        for t in team_tools:
            assert t.name not in names

    def test_filter_preserves_internal_tools(self) -> None:
        """tool_type='internal' 且 role_visibility=None 的工具对所有 role 可见
        (load_skill 就是这样)。"""
        r = ToolRegistry()
        load_skill = _td("load_skill", role_visibility=None)
        load_skill.tool_type = "internal"
        r._mcp_tools["load_skill"] = load_skill  # noqa: SLF001
        r._executors["load_skill"] = _noop_executor  # noqa: SLF001

        for role in ("lead", "member", "subagent", "coordinator"):
            tools = r.get_all_tools(role=role)
            assert "load_skill" in {t.name for t in tools}


# ---------------------------------------------------------------------------
# Agent role
# ---------------------------------------------------------------------------


class TestAgentRole:
    """Requirement: Agent.__init__ role 默认 'subagent',可显式指定。"""

    def _make_agent(self, role: str, registry=None) -> Agent:
        from baozicode.conversation.manager import ConversationManager
        from baozicode.llm.base import LLMClient, Message

        class _StubLLM(LLMClient):
            async def stream(self, *args, **kwargs):  # pragma: no cover
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

    def test_default_role_is_subagent(self) -> None:
        a = self._make_agent("subagent")
        assert a.role == "subagent"

    def test_explicit_lead(self) -> None:
        a = self._make_agent("lead")
        assert a.role == "lead"

    def test_explicit_member(self) -> None:
        a = self._make_agent("member")
        assert a.role == "member"

    def test_coordinator_accepted(self) -> None:
        """'coordinator' 是为未来 proposal 准备的 — 本 proposal 必须接受。"""
        a = self._make_agent("coordinator")
        assert a.role == "coordinator"

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValueError, match="Agent.role 非法"):
            self._make_agent("superuser")  # type: ignore[arg-type]


class TestAgentFiltersByRole:
    """Requirement: Agent._filter_tools_by_role 按 role 过滤。"""

    def test_lead_sees_team_tools(self, registry) -> None:
        from baozicode.conversation.manager import ConversationManager
        from baozicode.llm.base import LLMClient, Message

        class _StubLLM(LLMClient):
            async def stream(self, *args, **kwargs):  # pragma: no cover
                yield Message(role="assistant", content="")
                return

        # 传入 registry,Lead Agent 看到 builtin + team_*
        all_tools = registry.get_all_tools()
        a = Agent(
            llm_client=_StubLLM(),
            tools=all_tools,
            conversation=MagicMock(),
            permissions=None,
            config=MagicMock(),
            role="lead",
            tool_registry=registry,
        )
        names = {t.name for t in a.available_tools}
        assert "Read" in names
        assert "team_dispatch" in names

    def test_member_does_not_see_team_tools(self, registry) -> None:
        from baozicode.conversation.manager import ConversationManager
        from baozicode.llm.base import LLMClient, Message

        class _StubLLM(LLMClient):
            async def stream(self, *args, **kwargs):  # pragma: no cover
                yield Message(role="assistant", content="")
                return

        all_tools = registry.get_all_tools()
        a = Agent(
            llm_client=_StubLLM(),
            tools=all_tools,
            conversation=MagicMock(),
            permissions=None,
            config=MagicMock(),
            role="member",
            tool_registry=registry,
        )
        names = {t.name for t in a.available_tools}
        assert "Read" in names
        assert "team_dispatch" not in names

    def test_subagent_default_does_not_see_team_tools(self, registry) -> None:
        from baozicode.conversation.manager import ConversationManager
        from baozicode.llm.base import LLMClient, Message

        class _StubLLM(LLMClient):
            async def stream(self, *args, **kwargs):  # pragma: no cover
                yield Message(role="assistant", content="")
                return

        # 不传 role —— 默认 'subagent'
        all_tools = registry.get_all_tools()
        a = Agent(
            llm_client=_StubLLM(),
            tools=all_tools,
            conversation=MagicMock(),
            permissions=None,
            config=MagicMock(),
            tool_registry=registry,
        )
        names = {t.name for t in a.available_tools}
        assert "team_dispatch" not in names