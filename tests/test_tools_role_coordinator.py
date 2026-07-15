"""v1-4-team-coordinator — ToolRegistry coordinator 角色过滤测试。

覆盖 `openspec/changes/v1-4-team-coordinator/specs/team-management/spec.md`
中:

- `ToolRegistry.get_all_tools(role='coordinator')` 显式剔除 Write/Edit/Bash
- Read/Grep/Glob/WebFetch 仍可见
- 6 个 team_* 工具(role_visibility=['lead','coordinator'])可见
- `tool_type='internal'` 工具(load_skill/task)不受 coordinator 剔除影响
- 真实注册链路:`register_team_tools` 后 coordinator 角色能看到 team_*
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from baozicode.tools.base import ToolDefinition
from baozicode.tools.registry import ToolRegistry


def _td(name: str, *, role_visibility=None, tool_type: str = "external") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"tool {name}",
        parameters={"type": "object", "properties": {}},
        role_visibility=role_visibility,
        tool_type=tool_type,
    )


async def _noop_executor(_args: dict) -> Any:
    from baozicode.tools.base import ToolResult
    return ToolResult(tool_call_id="x", content="ok")


@pytest.fixture
def builtin_set() -> list[ToolDefinition]:
    """7 个内置工具,全部 role_visibility=None(向后兼容默认)。"""
    return [_td(n) for n in ("Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch")]


@pytest.fixture
def team_tools() -> list[ToolDefinition]:
    """6 个 team_* 协作工具,role_visibility=['lead','coordinator'](v1-4-coordinator 扩展)。"""
    return [
        _td("team_dispatch", role_visibility=["lead", "coordinator"]),
        _td("team_send_message", role_visibility=["lead", "coordinator"]),
        _td("team_cancel", role_visibility=["lead", "coordinator"]),
        _td("team_merge", role_visibility=["lead", "coordinator"]),
        _td("team_task_create", role_visibility=["lead", "coordinator"]),
        _td("team_task_query", role_visibility=["lead", "coordinator"]),
    ]


@pytest.fixture
def registry(builtin_set, team_tools) -> ToolRegistry:
    """内置 + team 工具 + 2 个 internal 工具的混合 registry。"""
    r = ToolRegistry()
    for t in team_tools:
        r._mcp_tools[t.name] = t  # noqa: SLF001
        r._executors[t.name] = _noop_executor  # noqa: SLF001
    # internal 工具
    load_skill = _td("load_skill", role_visibility=None, tool_type="internal")
    task = _td("task", role_visibility=None, tool_type="internal")
    for t in (load_skill, task):
        r._mcp_tools[t.name] = t  # noqa: SLF001
        r._executors[t.name] = _noop_executor  # noqa: SLF001
    return r


# ---------------------------------------------------------------------------
# Write/Edit/Bash 剔除
# ---------------------------------------------------------------------------


class TestCoordinatorStripsMutatingTools:
    """Requirement: coordinator 显式剔除 Write/Edit/Bash。"""

    def test_write_hidden(self, registry) -> None:
        names = {t.name for t in registry.get_all_tools(role="coordinator")}
        assert "Write" not in names

    def test_edit_hidden(self, registry) -> None:
        names = {t.name for t in registry.get_all_tools(role="coordinator")}
        assert "Edit" not in names

    def test_bash_hidden(self, registry) -> None:
        names = {t.name for t in registry.get_all_tools(role="coordinator")}
        assert "Bash" not in names


# ---------------------------------------------------------------------------
# Read/Grep/Glob/WebFetch 仍可见
# ---------------------------------------------------------------------------


class TestCoordinatorSeesReadOnly:
    """Requirement: Read/Grep/Glob/WebFetch 仍可见。"""

    @pytest.mark.parametrize("tool_name", ["Read", "Grep", "Glob", "WebFetch"])
    def test_read_only_visible(self, registry, tool_name: str) -> None:
        names = {t.name for t in registry.get_all_tools(role="coordinator")}
        assert tool_name in names


# ---------------------------------------------------------------------------
# team_* role_visibility 扩展
# ---------------------------------------------------------------------------


class TestCoordinatorSeesTeamTools:
    """Requirement: 6 个 team_* role_visibility 含 coordinator → 可见。"""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "team_dispatch",
            "team_send_message",
            "team_cancel",
            "team_merge",
            "team_task_create",
            "team_task_query",
        ],
    )
    def test_team_tool_visible(self, registry, tool_name: str) -> None:
        names = {t.name for t in registry.get_all_tools(role="coordinator")}
        assert tool_name in names

    def test_all_six_team_tools_visible(self, registry, team_tools) -> None:
        names = {t.name for t in registry.get_all_tools(role="coordinator")}
        for t in team_tools:
            assert t.name in names


# ---------------------------------------------------------------------------
# internal 工具不受 coordinator 剔除影响
# ---------------------------------------------------------------------------


class TestCoordinatorSeesInternalTools:
    """Requirement: tool_type='internal' 的工具(load_skill/task)对所有 role 可见。"""

    def test_load_skill_visible(self, registry) -> None:
        names = {t.name for t in registry.get_all_tools(role="coordinator")}
        assert "load_skill" in names

    def test_task_visible(self, registry) -> None:
        names = {t.name for t in registry.get_all_tools(role="coordinator")}
        assert "task" in names


# ---------------------------------------------------------------------------
# 其他角色不受 coordinator 剔除影响
# ---------------------------------------------------------------------------


class TestOtherRolesUnaffected:
    """Requirement: 其他 role 不被 coordinator 剔除逻辑影响。"""

    def test_lead_still_sees_write(self, registry) -> None:
        names = {t.name for t in registry.get_all_tools(role="lead")}
        assert "Write" in names
        assert "Bash" in names

    def test_subagent_still_sees_write(self, registry) -> None:
        names = {t.name for t in registry.get_all_tools(role="subagent")}
        assert "Write" in names
        assert "Bash" in names

    def test_member_still_sees_write(self, registry) -> None:
        names = {t.name for t in registry.get_all_tools(role="member")}
        assert "Write" in names
        assert "Bash" in names


# ---------------------------------------------------------------------------
# Agent(role='coordinator') 集成校验
# ---------------------------------------------------------------------------


class TestAgentRoleCoordinatorFilters:
    """Agent(role='coordinator') 构造后 available_tools 走 coordinator 过滤。"""

    def _make_agent(self, role: str, tool_registry: ToolRegistry):
        from baozicode.agent.loop import Agent
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
            tool_registry=tool_registry,
        )

    def test_coordinator_agent_no_mutating_tools(self, registry) -> None:
        a = self._make_agent("coordinator", registry)
        names = {t.name for t in a.available_tools}
        assert "Write" not in names
        assert "Edit" not in names
        assert "Bash" not in names

    def test_coordinator_agent_sees_team_tools(self, registry) -> None:
        a = self._make_agent("coordinator", registry)
        names = {t.name for t in a.available_tools}
        assert "team_dispatch" in names

    def test_coordinator_agent_sees_read_only(self, registry) -> None:
        a = self._make_agent("coordinator", registry)
        names = {t.name for t in a.available_tools}
        assert "Read" in names
        assert "Grep" in names