"""Agent 主循环集成测试 — 用 mock LLM 验证端到端事件流、停止条件、权限钩子。"""

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import AgentEvent, StopReason, UsageStats
from baozicode.agent.loop import Agent
from baozicode.config.schema import Permissions
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.tools.base import ToolCall, ToolResult
from baozicode.tools.registry import get_all_tools

sys.path.insert(0, str(Path(__file__).parent))
from _agent_helpers import make_minimal_config


class _ScriptedLLM(LLMClient):
    """多轮 mock LLM — `responses` 是按调用次数索引的脚本。

    每条 response 是一系列 ContentDelta;遇到 type='end_turn' 时代理抛 StopIteration
    让 Agent 走"text_only done"分支。
    """

    def __init__(self, responses: list[list[ContentDelta]], fail_on_call: int | None = None):
        self.responses = responses
        self.fail_on_call = fail_on_call
        self.call_count = 0

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        if self.fail_on_call is not None and self.call_count == self.fail_on_call:
            raise RuntimeError("simulated LLM outage")
        script = self.responses[self.call_count - 1]
        for d in script:
            yield d


class _Perm:
    """最小 duck-typed Permissions(支持 kwargs 覆盖默认值)。"""

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


async def _drain(agent: Agent, text: str) -> list[AgentEvent]:
    out: list[AgentEvent] = []
    async for ev in agent.run(text):
        out.append(ev)
    return out


async def test_text_only_response_completes() -> None:
    """模型只吐文本 → Agent 单轮结束,reason=COMPLETED。"""
    conv = ConversationManager()
    llm = _ScriptedLLM([
        [ContentDelta(type="text", text="hello there")]
    ])
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    events = await _drain(a, "hi")
    reasons = [e.payload for e in events if e.type == "done"]
    assert reasons == [StopReason.COMPLETED]
    # 入库 1 条 user + 1 条 assistant
    assert len(conv) == 2
    assert conv.to_list()[1].role == "assistant"
    print("[OK] text-only: 1 turn, COMPLETED, 2 messages in history")


async def test_text_plus_one_tool_call_eventually_completes() -> None:
    """模型先吐文本+工具调用 → Agent 跑工具 → 第二轮吐完成文本 → COMPLETED。

    第一轮:text "searching" + tool_use(Read) + usage(50)
    第二轮:text "found it" + usage(20)
    """
    conv = ConversationManager()
    call = ToolCall(id="call-1", name="Read", arguments={"file_path": "/x"})
    llm = _ScriptedLLM([
        [
            ContentDelta(type="text", text="searching... "),
            ContentDelta(type="tool_use", text=call),
            ContentDelta(type="usage", text=UsageStats(input_tokens=50, output_tokens=10)),
        ],
        [
            ContentDelta(type="text", text="found it"),
            ContentDelta(type="usage", text=UsageStats(input_tokens=80, output_tokens=5)),
        ],
    ])
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(auto_allow=["Read"]),  # auto_allow Read 跳过 Modal
        config=make_minimal_config(),
    )
    events = await _drain(a, "find x")
    types = [e.type for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert "usage" in types
    reasons = [e.payload for e in events if e.type == "done"]
    assert reasons == [StopReason.COMPLETED]
    assert llm.call_count == 2
    # 入库:user + assistant(structured) + tool + assistant(text)
    assert len(conv) == 4
    roles = [m.role for m in conv.to_list()]
    assert roles == ["user", "assistant", "tool", "assistant"]
    print("[OK] text + tool call → 2 turns, COMPLETED, conversation properly extended")


async def test_max_iterations_reached() -> None:
    """模型每轮都要工具 → 跑到 max_iterations 兜底终止。

    为避免与 FAILED_TOOL_LOOP 混淆,每次给的 file_path 不同 → 错误信息不同 →
    sha256 不同 → 不计入失败循环。
    """
    conv = ConversationManager()

    def gen(idx: int) -> list[ContentDelta]:
        call = ToolCall(id=f"c{idx}", name="Read", arguments={"file_path": f"/missing-{idx}"})
        return [
            ContentDelta(type="tool_use", text=call),
            ContentDelta(type="usage", text=UsageStats()),
        ]

    llm = _ScriptedLLM([gen(i) for i in range(20)])
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(auto_allow=["Read"]),
        max_iterations=3,
        config=make_minimal_config(),
    )
    events = await _drain(a, "loop")
    reasons = [e.payload for e in events if e.type == "done"]
    assert reasons == [StopReason.MAX_ITERATIONS_REACHED]
    assert llm.call_count == 3
    print("[OK] infinite tool-call loop capped at max_iterations=3")


async def test_user_cancellation_via_event() -> None:
    """cancel() 设置 _cancel_event → 下一轮流结束/工具结果后终止 USER_CANCELLED。"""
    conv = ConversationManager()

    class LongStreamLLM(LLMClient):
        def __init__(self):
            self.cancelled = False

        async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
            yield ContentDelta(type="text", text="working")
            # 这里模拟一个慢流;await cancel 会在事件循环下一拍让它跑完
            await asyncio.sleep(0.05)
            yield ContentDelta(type="text", text=" done")
            # usage 不会到达因为循环会在 stream 末尾检查

    llm = LongStreamLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
    )

    async def driver():
        # 起一个 cancel 在 30ms 后
        async def cancel_after():
            await asyncio.sleep(0.03)
            a.cancel()

        cancel_task = asyncio.create_task(cancel_after())
        events = await _drain(a, "interrupt me")
        await cancel_task
        return events

    events = await driver()
    reasons = [e.payload for e in events if e.type == "done"]
    # 取消可能在流结束之前或之后生效,这取决于时机 — 我们只验证 done 事件存在
    assert reasons == [StopReason.USER_CANCELLED]
    print("[OK] cancel() during stream → USER_CANCELLED")


async def test_stream_error_caught() -> None:
    """LLM 流抛异常 → Agent 捕获 → done.reason=STREAM_ERROR,error 事件先 yield。"""
    conv = ConversationManager()
    llm = _ScriptedLLM([[]], fail_on_call=1)  # 第 1 次调用就抛
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    events = await _drain(a, "kaboom")
    error_events = [e.payload for e in events if e.type == "error"]
    done_reasons = [e.payload for e in events if e.type == "done"]
    assert error_events and "outage" in error_events[0]
    assert done_reasons == [StopReason.STREAM_ERROR]
    print("[OK] stream exception → Agent emits error event + STREAM_ERROR")


async def test_deny_short_circuits_executor() -> None:
    """permissions.deny 命中 → 工具不被执行,返回 error_result,completed 仍走完。"""
    conv = ConversationManager()
    call = ToolCall(id="c1", name="Bash", arguments={"command": "echo hi"})
    llm = _ScriptedLLM([
        [ContentDelta(type="tool_use", text=call), ContentDelta(type="usage", text=UsageStats())],
        [ContentDelta(type="text", text="OK"), ContentDelta(type="usage", text=UsageStats())],
    ])
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(deny=["Bash"]),
        config=make_minimal_config(),
    )
    events = await _drain(a, "shell")
    tool_results = [e.payload for e in events if e.type == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
    assert "denied" in tool_results[0].content.lower()
    reasons = [e.payload for e in events if e.type == "done"]
    # 拒绝 1 次未触发阈值,deny 转交 execute_tool_call 走"执行被 deny"路径,Agent 继续下一轮 → COMPLETED
    assert reasons == [StopReason.COMPLETED]
    print("[OK] permissions.deny → is_error result, Agent continues")


async def test_auto_allow_skips_permission_callback() -> None:
    """permission_callback 在 auto_allow 命中时不被调用。"""
    conv = ConversationManager()
    calls_to_callback: list[str] = []

    async def cb(call: ToolCall) -> bool:
        calls_to_callback.append(call.name)
        return False  # 即便拒绝,auto_allow 也会短路

    call = ToolCall(id="c1", name="Read", arguments={"file_path": "/x"})
    llm = _ScriptedLLM([
        [ContentDelta(type="tool_use", text=call), ContentDelta(type="usage", text=UsageStats())],
        [ContentDelta(type="text", text="done"), ContentDelta(type="usage", text=UsageStats())],
    ])
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(auto_allow=["Read"]),
        permission_callback=cb,
        config=make_minimal_config(),
    )
    events = await _drain(a, "r")
    assert calls_to_callback == [], f"callback fired: {calls_to_callback}"
    reasons = [e.payload for e in events if e.type == "done"]
    assert reasons == [StopReason.COMPLETED]
    print("[OK] auto_allow short-circuits permission_callback")


async def test_permission_callback_invoked_for_non_auto_tool() -> None:
    """非 auto_allow 工具 → callback 被调用;若拒绝 → ToolResult.is_error=True。"""
    conv = ConversationManager()
    call = ToolCall(id="c1", name="Bash", arguments={"command": "ls"})

    async def deny_all(call: ToolCall) -> bool:
        return False

    llm = _ScriptedLLM([
        [ContentDelta(type="tool_use", text=call), ContentDelta(type="usage", text=UsageStats())],
        [ContentDelta(type="text", text="done"), ContentDelta(type="usage", text=UsageStats())],
    ])
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),  # Bash 不在 auto_allow
        permission_callback=deny_all,
        config=make_minimal_config(),
    )
    events = await _drain(a, "shell")
    tool_results = [e.payload for e in events if e.type == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
    assert "denied by user" in tool_results[0].content
    print("[OK] permission_callback=False → is_error result")


async def test_stable_system_passed_to_llm_stream() -> None:
    """v0.4: Agent 应该把 self._prompt.stable_system(由 PromptBuilder 拼)传入 llm.stream。

    不再断言具体文本(那会被 PromptBuilder 改动破坏),只断言含"BaoZiCode"。
    """
    conv = ConversationManager()

    class RecordingLLM(LLMClient):
        def __init__(self):
            self.last_system: str | None = None

        async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
            self.last_system = system
            yield ContentDelta(type="text", text="ok")

    rec = RecordingLLM()
    a = Agent(
        llm_client=rec,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    await _drain(a, "x")
    assert rec.last_system is not None
    assert "BaoZiCode" in rec.last_system
    print("[OK] stable_system from BuiltPrompt flows into llm.stream system kwarg")


async def test_session_usage_accumulates_across_turns() -> None:
    """每轮 yield 一条 usage 事件,total = 累加。"""
    conv = ConversationManager()
    call = ToolCall(id="c", name="Read", arguments={"file_path": "/x"})
    llm = _ScriptedLLM([
        [
            ContentDelta(type="text", text="r1"),
            ContentDelta(type="tool_use", text=call),
            ContentDelta(type="usage", text=UsageStats(input_tokens=10, output_tokens=5)),
        ],
        [
            ContentDelta(type="text", text="done"),
            ContentDelta(type="usage", text=UsageStats(input_tokens=20, output_tokens=7)),
        ],
    ])
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(auto_allow=["Read"]),
        config=make_minimal_config(),
    )
    events = await _drain(a, "anything")
    usages = [e.payload for e in events if e.type == "usage"]
    assert len(usages) == 2
    # 第二条 usage 的 session_total 应该是 30/12/0/0
    second = usages[1]
    assert second["this_turn"].input_tokens == 20
    assert second["session_total"].input_tokens == 30
    assert second["session_total"].output_tokens == 12
    print("[OK] session_usage accumulates per turn")


async def main() -> None:
    await test_text_only_response_completes()
    await test_text_plus_one_tool_call_eventually_completes()
    await test_max_iterations_reached()
    await test_user_cancellation_via_event()
    await test_stream_error_caught()
    await test_deny_short_circuits_executor()
    await test_auto_allow_skips_permission_callback()
    await test_permission_callback_invoked_for_non_auto_tool()
    await test_stable_system_passed_to_llm_stream()
    await test_session_usage_accumulates_across_turns()
    print("\nAll agent_loop tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
