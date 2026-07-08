"""v1.0 Skills — 工具白名单双层防御单元测试。

覆盖:
- `validate_declared_tools`:全 OK / 缺失 / None / 空 list
- `SkillWhitelistFilter.is_allowed`:
  - 无 active Skill → 永远放行
  - active Skill 但全部未声明 allowed-tools → 放行
  - active Skill 声明 → union 内放行 / 之外拦截
  - internal 工具 → 永远放行(load_skill)
  - 多 Skill union
  - explicit 空 list = 「仅 internal」
- SkillLoader.load_skill 集成 L1 校验(声明工具不存在 → 失败并保留激活态)
- v0.5 Agent 集成 L2 校验(`skill_filter` 阻塞 → 立即 deny)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from baozicode.commands.registry import CommandRegistry
from baozicode.skills.activation import SkillActivation
from baozicode.skills.whitelist import (
    SkillWhitelistFilter,
    validate_declared_tools,
)
from baozicode.tools.base import ToolCall, ToolDefinition, ToolResult
from baozicode.tools.registry import ToolRegistry as ToolsReg


# ---- 临时 Skill 构造 helper ----


def _sd(text: str):
    """用 frontmatter 文本造 SkillDef 替身(避开真实目录)。"""
    from baozicode.skills.schema import parse_frontmatter
    from pathlib import Path

    fm, body = parse_frontmatter(text, file_path=Path("/tmp/x.md"))

    class _Stub:
        def __init__(self):
            self._fm = fm
            self.body = body
            self.frontmatter = fm
            self.name = fm.name
            self.description = fm.description
            self.mode = fm.mode
            self.allowed_tools = list(fm.allowed_tools or [])
            self.history_bubbles = fm.history_bubbles
            self.model = fm.model
            self.hidden = fm.hidden

    return _Stub()


class _StubRegistry:
    def __init__(self, sd):
        self._sd = sd

    def lookup(self, name):
        if self._sd and self._sd.name == name:
            return self._sd
        return None


# ---- L1 validate_declared_tools ----


class TestValidateDeclaredTools:
    def test_all_present_ok(self) -> None:
        reg = ToolsReg()
        # Bash / Read / Grep / Glob 是 7 个 builtin 工具中的实际名
        validate_declared_tools(
            ["Read", "Bash"], reg, skill_name="test"
        )  # 不抛即过

    def test_missing_raises(self) -> None:
        reg = ToolsReg()
        with pytest.raises(ValueError, match="未注册工具"):
            validate_declared_tools(
                ["Read", "GhostTool"], reg, skill_name="bad"
            )

    def test_none_skipped(self) -> None:
        reg = ToolsReg()
        validate_declared_tools(None, reg, skill_name="ok")

    def test_empty_list_skipped(self) -> None:
        reg = ToolsReg()
        validate_declared_tools([], reg, skill_name="ok")


# ---- L2 SkillWhitelistFilter ----


class TestFilterNoActiveSkill:
    def test_no_active_always_allowed(self) -> None:
        reg = ToolsReg()
        activation = SkillActivation(CommandRegistry())
        filt = SkillWhitelistFilter(activation, reg)
        call = ToolCall(id="1", name="Bash", arguments={"cmd": "ls"})
        assert filt.is_allowed(call) is True

    def test_active_but_undeclared_always_allowed(self) -> None:
        """Skill 激活了但 frontmatter.allowed-tools 未声明 → 不限制。"""
        reg = ToolsReg()
        cmd_reg = CommandRegistry()
        activation = SkillActivation(cmd_reg)
        activation.activate("testskill", "body")  # 无 allowed-tools
        filt = SkillWhitelistFilter(activation, reg)
        # 任何 tool 都应放行(包括实际不存在的虚构名)
        call = ToolCall(id="1", name="Anything", arguments={})
        assert filt.is_allowed(call) is True

    def test_active_declared_filters(self) -> None:
        reg = ToolsReg()
        cmd_reg = CommandRegistry()
        activation = SkillActivation(cmd_reg)
        activation.activate(
            "limited", "body", allowed_tools=["Bash"]
        )
        filt = SkillWhitelistFilter(activation, reg)
        allowed_call = ToolCall(id="1", name="Bash", arguments={})
        blocked_call = ToolCall(id="2", name="Read", arguments={})
        assert filt.is_allowed(allowed_call) is True
        assert filt.is_allowed(blocked_call) is False

    def test_internal_tool_bypasses_filter(self) -> None:
        """tool_type='internal' 工具永远放行(load_skill 这类系统级)。"""
        reg = ToolsReg()
        # 注入一个 internal 工具
        asyncio.run(reg.register_tool(
            ToolDefinition(
                name="my_internal",
                description="sys",
                parameters={},
                tool_type="internal",
            ),
            lambda args: ToolResult.success("", "ok"),
        ))
        activation = SkillActivation(CommandRegistry())
        activation.activate(
            "limited", "body", allowed_tools=["Bash"]
        )
        filt = SkillWhitelistFilter(activation, reg)
        call = ToolCall(id="1", name="my_internal", arguments={})
        assert filt.is_allowed(call) is True

    def test_multi_skill_union(self) -> None:
        """多个 Skill 声明 → 取 union,任一 Skill 里有就放行。"""
        reg = ToolsReg()
        cmd_reg = CommandRegistry()
        activation = SkillActivation(cmd_reg)
        activation.activate("a", "body", allowed_tools=["Bash"])
        activation.activate("b", "body", allowed_tools=["Read"])
        filt = SkillWhitelistFilter(activation, reg)
        # 两个都在 union 内 → 都放行
        assert filt.is_allowed(
            ToolCall(id="1", name="Bash", arguments={})
        ) is True
        assert filt.is_allowed(
            ToolCall(id="2", name="Read", arguments={})
        ) is True
        # 都不在
        assert filt.is_allowed(
            ToolCall(id="3", name="Grep", arguments={})
        ) is False

    def test_explicit_empty_list_no_user_restriction(self) -> None:
        """`allowed-tools: []`(显式空)目前与「未声明」等价 —— 不限制。

        v1.0 设计选择:`[]` 和 None 都视为「Skill 没限制工具集」。
        想「仅 internal」的 Skill 应在 frontmatter 显式列出想暴露的工具,
        load_skill 由 tool_type='internal' 通路豁免,不需专门写。
        """
        reg = ToolsReg()
        activation = SkillActivation(CommandRegistry())
        activation.activate("lockdown", "body", allowed_tools=[])
        filt = SkillWhitelistFilter(activation, reg)
        call = ToolCall(id="1", name="Bash", arguments={})
        assert filt.is_allowed(call) is True

    def test_active_declared_tools_union(self) -> None:
        reg = ToolsReg()
        activation = SkillActivation(CommandRegistry())
        activation.activate("a", "b", allowed_tools=["Bash", "Read"])
        activation.activate("c", "d", allowed_tools=["Grep"])
        filt = SkillWhitelistFilter(activation, reg)
        assert filt.active_declared_tools() == {"Bash", "Read", "Grep"}


# ---- SkillLoader.load_skill 集成 L1 校验 ----


class TestLoaderIntegratesL1:
    def _fm(self, name="commit", tools=None):
        tools_part = (
            f"allowed-tools: [{', '.join(tools)}]" if tools else ""
        )
        return (
            f"---\nname: {name}\ndescription: d\n{tools_part}\n---\nbody"
        )

    def test_missing_declared_tool_returns_failure(self) -> None:
        sd = _sd(self._fm(tools=["Read", "GhostTool"]))
        reg = _StubRegistry(sd)
        activation = SkillActivation(CommandRegistry())
        tool_reg = ToolsReg()
        from baozicode.skills.loader import SkillLoader

        loader = SkillLoader(reg, activation, tool_registry=tool_reg)
        result = loader.load_skill("commit")
        assert result.ok is False
        assert "未注册工具" in result.summary
        # 不应激活到 active set(校验失败,应直接拒绝)
        assert activation.is_active("commit") is False

    def test_no_tool_registry_skips_validation(self) -> None:
        """向下兼容:tool_registry=None 时不校验(测试或特殊 boot 路径)。"""
        sd = _sd(self._fm(tools=["Read", "GhostTool"]))
        reg = _StubRegistry(sd)
        activation = SkillActivation(CommandRegistry())
        from baozicode.skills.loader import SkillLoader

        loader = SkillLoader(reg, activation)  # 无 tool_registry
        result = loader.load_skill("commit")
        assert result.ok is True  # 通过


# ---- Agent._v5_executor L2 集成(完整跑通) ----


sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAgentIntegrationL2:
    @pytest.mark.asyncio
    async def test_skill_filter_blocks_read(self) -> None:
        """Skill 声明只允许 Bash,Agent._v5_executor 把 Read 拒掉。"""
        from _agent_helpers import make_minimal_config
        from baozicode.agent.guards import GuardState
        from baozicode.agent.loop import Agent
        from baozicode.conversation.manager import ConversationManager
        from baozicode.tools.registry import get_all_tools

        config = make_minimal_config()
        activation = SkillActivation(CommandRegistry())
        activation.activate(
            "limited", "body", allowed_tools=["Bash"]
        )
        tool_reg = ToolsReg()
        filt = SkillWhitelistFilter(activation, tool_reg)

        class _NoopLLM:
            async def stream(self, *args, **kwargs):
                if False:
                    yield

        agent = Agent(
            llm_client=_NoopLLM(),
            tools=get_all_tools(),
            conversation=ConversationManager(),
            permissions=None,
            config=config,
            skill_filter=filt,
        )
        blocked_call = ToolCall(id="1", name="Read", arguments={})
        result = await agent._v5_executor(
            blocked_call, guard_state=GuardState()
        )
        assert result.is_error is True
        assert "Skill" in result.content

    @pytest.mark.asyncio
    async def test_skill_filter_allows_declared(self) -> None:
        from _agent_helpers import make_minimal_config
        from baozicode.agent.guards import GuardState
        from baozicode.agent.loop import Agent
        from baozicode.conversation.manager import ConversationManager
        from baozicode.tools.registry import get_all_tools

        config = make_minimal_config()
        activation = SkillActivation(CommandRegistry())
        activation.activate(
            "limited", "body", allowed_tools=["Bash"]
        )
        tool_reg = ToolsReg()
        filt = SkillWhitelistFilter(activation, tool_reg)

        class _NoopLLM:
            async def stream(self, *args, **kwargs):
                if False:
                    yield

        agent = Agent(
            llm_client=_NoopLLM(),
            tools=get_all_tools(),
            conversation=ConversationManager(),
            permissions=None,
            config=config,
            skill_filter=filt,
        )
        call = ToolCall(id="1", name="Bash", arguments={"cmd": "echo x"})
        result = await agent._v5_executor(
            call, guard_state=GuardState()
        )
        if result.is_error and "Skill" in result.content:
            pytest.fail(
                f"Bash 应该通过 Skill 白名单,得到: {result.content}"
            )
