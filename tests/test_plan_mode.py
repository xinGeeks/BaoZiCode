"""Plan Mode 工具过滤 + Agent 在 plan mode 下的行为测试。"""

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import Progress, StopReason
from baozicode.agent.loop import Agent
from baozicode.config.schema import Permissions
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message, TextBlock, ToolUseBlock
from baozicode.tools.base import ToolDefinition, ToolCall
from baozicode.tools.registry import get_all_tools

sys.path.insert(0, str(Path(__file__).parent))
from _agent_helpers import make_minimal_config


class _Perm:
    """满足 Agent 期望的 duck-typed Permissions(minimal surface)。"""

    def __init__(
        self,
        deny: list[str] | None = None,
        auto_allow: list[str] | None = None,
        batch_confirm: bool = False,
        bash_locked_cwd: bool = False,
    ) -> None:
        self.deny = deny if deny is not None else []
        self.auto_allow = auto_allow if auto_allow is not None else []
        self.batch_confirm = batch_confirm
        self.bash_locked_cwd = bash_locked_cwd


def test_plan_mode_filters_side_effect_tools() -> None:
    """Agent(plan_mode=True).available_tools 只保留 side_effect=False 工具。"""
    tools = get_all_tools()
    a = Agent(
        llm_client=_NoopLLM(),
        tools=tools,
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
        plan_mode=True,
    )
    names = sorted(t.name for t in a.available_tools)
    # 4 只读工具:Read, Grep, Glob, WebFetch
    assert names == ["Glob", "Grep", "Read", "WebFetch"]
    print("[OK] plan_mode=True: only side_effect=False tools exposed")


def test_plan_mode_false_exposes_all_tools() -> None:
    """Agent(plan_mode=False).available_tools 返回全部 7 工具。"""
    tools = get_all_tools()
    a = Agent(
        llm_client=_NoopLLM(),
        tools=tools,
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
        plan_mode=False,
    )
    names = sorted(t.name for t in a.available_tools)
    assert names == ["Bash", "Edit", "Glob", "Grep", "Read", "WebFetch", "Write"]
    print("[OK] plan_mode=False: all 7 tools exposed")


def test_plan_mode_property_reflects_constructor() -> None:
    a = Agent(
        llm_client=_NoopLLM(),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
        plan_mode=True,
    )
    assert a.plan_mode is True
    print("[OK] Agent.plan_mode property reflects constructor")


def test_plan_mode_hides_side_effect_tool_at_constructor_layer() -> None:
    """即使用户传全工具进去,plan_mode=True 时仍然过滤 side_effect=True 的工具。"""
    tools = get_all_tools()
    full = Agent(
        _NoopLLM(),
        tools,
        ConversationManager(),
        _Perm(),
        config=make_minimal_config(),
        plan_mode=False,
    )
    plan = Agent(
        _NoopLLM(),
        tools,
        ConversationManager(),
        _Perm(),
        config=make_minimal_config(),
        plan_mode=True,
    )
    full_names = {t.name for t in full.available_tools}
    plan_names = {t.name for t in plan.available_tools}
    diff = full_names - plan_names
    # 应该正好是 side_effect=True 的工具被屏蔽
    assert diff == {"Write", "Edit", "Bash"}
    print("[OK] plan mode hides exactly the side_effect=True tools")


class _NoopLLM(LLMClient):
    """用最少实现让 Agent 跑起来;其实只测 available_tools,不走 run。"""

    async def stream(self, messages, system=None, tools=None):
        if False:
            yield ContentDelta(type="text", text="")


class _PlanModeResistingLLM(LLMClient):
    """模拟 LLM 在 plan mode 下想调 Bash(应该被 Agent 当 unknown tool 终止)。"""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, system=None, tools=None):
        self.calls += 1
        # 偷看 Agent 给的 tools 列表 — plan mode 下不应包含 Bash
        available_names = [t.name for t in tools] if tools else []
        assert "Bash" not in available_names, (
            f"plan mode should filter Bash out, but tools={available_names}"
        )
        # 即便硬要 emit Bash(模拟模型失误),Agent 也会按 unknown 处理
        call = ToolCall(id="x1", name="Bash", arguments={"command": "ls"})
        yield ContentDelta(type="text", text="let me bash")
        # 通过助手 message block 表达 tool_use
        # 注意:StreamCollector 只吸收 ContentDelta,不再 yield delta.text 形式的 tool_use;
        # 模型的标准流都会先 token 化成 ContentDelta,这里硬塞
        # 我们改用直接往 collector 灌 tool_use delta 的方式测试 Agent 的 unknown 处理
        yield ContentDelta(type="tool_use", text=call)


async def test_agent_in_plan_mode_terminates_on_trying_side_effect_tool() -> None:
    """plan mode 下 LLM 想调 Bash → Agent 把它当 unknown tool 处理 + 第二次终止。"""
    conv = ConversationManager()
    a = Agent(
        llm_client=_PlanModeResistingLLM(),
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
        plan_mode=True,
    )
    events = []
    async for ev in a.run("list files"):
        events.append(ev)

    # 至少应该有 progress + done;done.reason 应该是 UNKNOWN_TOOL_HALLUCINATION
    reasons = [ev.payload for ev in events if ev.type == "done"]
    assert reasons, "Agent should have emitted a done event"
    assert reasons[-1] == StopReason.UNKNOWN_TOOL_HALLUCINATION
    print("[OK] plan mode + side_effect tool request → terminated")


def main() -> None:
    test_plan_mode_filters_side_effect_tools()
    test_plan_mode_false_exposes_all_tools()
    test_plan_mode_property_reflects_constructor()
    test_plan_mode_hides_side_effect_tool_at_constructor_layer()
    asyncio.run(test_agent_in_plan_mode_terminates_on_trying_side_effect_tool())
    print("\nAll plan_mode tests passed.")


if __name__ == "__main__":
    main()
