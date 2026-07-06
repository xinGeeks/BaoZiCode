"""不依赖真实 API Key 的冒烟测试 — v0.3 加上 Agent Loop 端到端场景。"""

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent))

from baozicode.agent.events import AgentEvent, StopReason, UsageStats
from baozicode.agent.loop import Agent
from baozicode.config.schema import AppConfig, BackendConfig, Permissions
from baozicode.config.loader import load_config, ConfigError
from baozicode.llm.factory import create_client
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.conversation.manager import ConversationManager
from baozicode.tools.base import ToolCall
from baozicode.tools.registry import get_all_tools


def _full_cfg(perms: Permissions | None = None, agent_max: int = 20, plan_default: bool = False) -> AppConfig:
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="sk-test-a", model="claude-sonnet-4-6"),
        openai=BackendConfig(api_key="sk-test-o", model="gpt-5"),
        minimax=BackendConfig(api_key="sk-test-m", model="MiniMax-M3"),
        deepseek=BackendConfig(api_key="sk-test-d", model="deepseek-chat"),
        permissions=perms,
        agent={"max_iterations": agent_max} if agent_max != 20 else None,
    )


def test_config_schema() -> None:
    """v0.3:AppConfig 含 agent 字段。"""
    cfg = _full_cfg()
    assert cfg.active().api_key == "sk-test-a"
    assert cfg.active_agent().max_iterations == 20  # 默认
    print("[OK] AppConfig schema (4 backends + agent config)")


def test_factory() -> None:
    cfg = _full_cfg()
    client = create_client(cfg)
    assert isinstance(client, LLMClient)
    print(f"[OK] factory -> {type(client).__name__} for backend={cfg.backend}")

    for target in ("openai", "minimax", "deepseek"):
        cfg2 = cfg.model_copy(update={"backend": target})  # type: ignore[arg-type]
        c = create_client(cfg2)
        assert isinstance(c, LLMClient)
    print("[OK] factory works for all 4 backends")


def test_conversation() -> None:
    conv = ConversationManager()
    conv.add_user("hello")
    conv.add_assistant("hi there")
    conv.add_user("how are you?")
    msgs = conv.to_list()
    assert len(msgs) == 3
    assert msgs[0].role == "user"
    conv.clear()
    assert len(conv.to_list()) == 0
    print("[OK] ConversationManager add/clear/order")


def test_config_missing() -> None:
    try:
        load_config(explicit_path="/nonexistent/xxx.yaml")
    except ConfigError as e:
        print(f"[OK] missing config raises ConfigError: {str(e)[:60]}...")
        return
    raise AssertionError("expected ConfigError")


# ============================================================================
# v0.3 新增 — Agent Loop 端到端场景
# ============================================================================


class _ScriptedLLM(LLMClient):
    """多轮 mock — `responses` 索引是调用次数(从 1 开始)。

    每条 response 是 ContentDelta 列表;遇 type='text' 末尾为空字符串 →
    表示本轮没要工具,走 COMPLETED 分支。
    """

    def __init__(self, responses: list[list[ContentDelta]], *, fail_on: int | None = None):
        self.responses = responses
        self.fail_on = fail_on
        self.call_count = 0
        self.last_system: str | None = None
        self.last_messages_count: int = 0

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        self.last_system = system
        self.last_messages_count = len(messages)
        if self.fail_on is not None and self.call_count == self.fail_on:
            raise RuntimeError("simulated LLM outage")
        idx = min(self.call_count - 1, len(self.responses) - 1)
        script = self.responses[idx]
        for d in script:
            yield d


def _perms(**kw) -> Permissions:
    return Permissions(**kw)


async def test_e2e_read_then_edit_completes() -> None:
    """用户发"读 README.md 然后用 Edit 加一行" → Agent 跑 Read → 进 conversation → 续流 → Edit。

    Plan Mode 关闭 + side_effect=True 工具,但 Edit 被 user-permission-callback 拒绝,
    Agent 把它记为 deny + error result + 继续 → 完成。
    """
    read_call = ToolCall(id="read-1", name="Read", arguments={"file_path": "README.md"})
    edit_call = ToolCall(id="edit-1", name="Edit", arguments={"old_string": "x", "new_string": "y"})

    llm = _ScriptedLLM([
        # turn 1: Read
        [
            ContentDelta(type="text", text="reading..."),
            ContentDelta(type="tool_use", text=read_call),
            ContentDelta(type="usage", text=UsageStats(input_tokens=100, output_tokens=20)),
        ],
        # turn 2: Edit
        [
            ContentDelta(type="tool_use", text=edit_call),
            ContentDelta(type="usage", text=UsageStats(input_tokens=150, output_tokens=30)),
        ],
        # turn 3: 完成
        [
            ContentDelta(type="text", text="done"),
            ContentDelta(type="usage", text=UsageStats(input_tokens=180, output_tokens=10)),
        ],
    ])

    async def allow_read_only(call: ToolCall) -> bool:
        # auto_allow 之外的 Edit 由 user 拒绝
        return call.name == "Read"

    conv = ConversationManager()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_perms(auto_allow=["Read"]),
        permission_callback=allow_read_only,
        max_iterations=10,
    )
    events: list[AgentEvent] = []
    async for ev in a.run("read README then edit"):
        events.append(ev)

    types = [e.type for e in events]
    assert "text" in types
    tool_call_events = [e.payload for e in events if e.type == "tool_call"]
    assert len(tool_call_events) == 2  # Read + Edit
    tool_result_events = [e.payload for e in events if e.type == "tool_result"]
    assert len(tool_result_events) == 2
    usage_events = [e.payload for e in events if e.type == "usage"]
    assert len(usage_events) == 3

    reasons = [e.payload for e in events if e.type == "done"]
    assert reasons == [StopReason.COMPLETED]

    # session_usage 累加到 turn 3 末尾
    last_usage = usage_events[-1]["session_total"]
    assert last_usage.input_tokens == 100 + 150 + 180
    assert last_usage.output_tokens == 20 + 30 + 10

    # conversation 入库了 user + assistant + tool + assistant + tool + assistant
    assert len(conv) == 6
    assert [m.role for m in conv.to_list()] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant"
    ]
    print("[OK] e2e: Read + Edit → 6 messages in conv, COMPLETED, usage 累加")


async def test_e2e_5_stop_conditions() -> None:
    """5 种 StopReason 端到端各一 case。

    已分别由 test_agent_loop.py 覆盖,这里只验证它们各自能通过 Agent.run 跑到。
    """
    from baozicode.tools.base import ToolResult

    # (a) COMPLETED
    a1 = Agent(
        llm_client=_ScriptedLLM([[ContentDelta(type="text", text="ok")]]),
        tools=[], conversation=ConversationManager(), permissions=_perms(),
    )
    rs = [e.payload async for e in a1.run("x") if e.type == "done"]
    assert rs == [StopReason.COMPLETED]

    # (b) MAX_ITERATIONS_REACHED
    a2 = Agent(
        llm_client=_ScriptedLLM([
            [
                ContentDelta(
                    type="tool_use",
                    text=ToolCall(id=f"c{i}", name="Read", arguments={"file_path": f"/m-{i}"}),
                ),
                ContentDelta(type="usage", text=UsageStats()),
            ]
            for i in range(20)
        ]),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perms(auto_allow=["Read"]),
        max_iterations=3,
    )
    rs = [e.payload async for e in a2.run("x") if e.type == "done"]
    assert rs == [StopReason.MAX_ITERATIONS_REACHED]

    # (c) USER_CANCELLED
    class _CancelLLM(LLMClient):
        async def stream(self, messages, system=None, tools=None):
            await asyncio.sleep(0.05)
            yield ContentDelta(type="text", text="hello")

    cancel_agent = Agent(
        llm_client=_CancelLLM(),
        tools=[], conversation=ConversationManager(), permissions=_perms(),
    )

    async def cancel_after():
        await asyncio.sleep(0.02)
        cancel_agent.cancel()

    asyncio.create_task(cancel_after())
    rs = [e.payload async for e in cancel_agent.run("cancel me") if e.type == "done"]
    assert rs == [StopReason.USER_CANCELLED]

    # (d) UNKNOWN_TOOL_HALLUCINATION
    a4 = Agent(
        llm_client=_ScriptedLLM([
            [ContentDelta(type="tool_use", text=ToolCall(id="u1", name="TotallyFake", arguments={})),
             ContentDelta(type="usage", text=UsageStats())],
            [ContentDelta(type="tool_use", text=ToolCall(id="u2", name="TotallyFake", arguments={})),
             ContentDelta(type="usage", text=UsageStats())],
        ]),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perms(),
    )
    rs = [e.payload async for e in a4.run("x") if e.type == "done"]
    assert rs == [StopReason.UNKNOWN_TOOL_HALLUCINATION]

    # (e) DENIALS_EXCEEDED / FAILED_TOOL_LOOP precedence:
    # 同一工具反复拒绝时,"Tool X denied by user." 的 sha256 一直相同 →
    # 3 次后 FAILED_TOOL_LOOP 先触发(DENIALS_EXCEEDED 也是 true,但优先级靠后)。
    # 我们验证可达的最终 reason 是 FAILED_TOOL_LOOP。
    # DENIALS_EXCEEDED 由 test_guards.py 单元覆盖(纯函数,无 LLM 干扰)。

    async def deny(call):
        return False

    deny_llm = _ScriptedLLM([
        [ContentDelta(type="tool_use", text=ToolCall(id=f"d{i}", name="Bash", arguments={"command": str(i)})),
         ContentDelta(type="usage", text=UsageStats())]
        for i in range(5)
    ])
    a5 = Agent(
        llm_client=deny_llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perms(),
        permission_callback=deny,
        max_iterations=10,
    )
    rs = [e.payload async for e in a5.run("x") if e.type == "done"]
    assert rs == [StopReason.FAILED_TOOL_LOOP]

    # (f) FAILED_TOOL_LOOP — 同一文件读取失败 3 次
    fail_llm = _ScriptedLLM([
        [ContentDelta(type="tool_use", text=ToolCall(id=f"f{i}", name="Read", arguments={"file_path": "/same-missing"})),
         ContentDelta(type="usage", text=UsageStats())]
        for i in range(5)
    ])
    a6 = Agent(
        llm_client=fail_llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perms(auto_allow=["Read"]),
        max_iterations=10,
    )
    rs = [e.payload async for e in a6.run("x") if e.type == "done"]
    assert rs == [StopReason.FAILED_TOOL_LOOP]

    # (g) STREAM_ERROR
    a7 = Agent(
        llm_client=_ScriptedLLM([[]], fail_on=1),
        tools=[], conversation=ConversationManager(), permissions=_perms(),
    )
    rs = [e.payload async for e in a7.run("x") if e.type == "done"]
    assert rs == [StopReason.STREAM_ERROR]

    print("[OK] e2e: 5+ stop conditions all reachable via Agent.run")


async def test_e2e_plan_then_do_workflow() -> None:
    """Plan Mode 端到端:`/plan <task>` 只走 Read 类,产出 plan;`/do` 用全工具跑。"""

    # Plan 阶段:只 Read,产出计划
    plan_llm = _ScriptedLLM([
        [ContentDelta(type="text", text="here's the plan: refactor X, then add test Y"),
         ContentDelta(type="usage", text=UsageStats(input_tokens=50, output_tokens=30))],
    ])
    plan_agent = Agent(
        llm_client=plan_llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perms(),
        plan_mode=True,
    )
    plan_events = [e async for e in plan_agent.run("plan the auth refactor")]

    plan_text_events = [e.payload for e in plan_events if e.type == "text"]
    assert "refactor X" in "".join(plan_text_events)
    assert [e.payload for e in plan_events if e.type == "done"] == [StopReason.COMPLETED]

    # plan 期间只能见到 4 个只读工具
    names = [t.name for t in plan_agent.available_tools]
    assert names == ["Read", "Grep", "Glob", "WebFetch"]

    # Do 阶段:用 plan 的 conversation + 全工具执行
    edit_call = ToolCall(id="e1", name="Edit", arguments={"old_string": "a", "new_string": "b"})
    do_llm = _ScriptedLLM([
        [ContentDelta(type="text", text="executing..."),
         ContentDelta(type="tool_use", text=edit_call),
         ContentDelta(type="usage", text=UsageStats(input_tokens=80, output_tokens=15))],
        [ContentDelta(type="text", text="done"), ContentDelta(type="usage", text=UsageStats(input_tokens=100, output_tokens=10))],
    ])

    # 复用 plan 阶段的 conversation,新建 plan_mode=False 的 Agent
    conv = plan_agent._conversation  # type: ignore[attr-defined]

    async def allow_all(call): return True

    do_agent = Agent(
        llm_client=do_llm,
        tools=get_all_tools(),
        conversation=conv,
        permissions=_perms(auto_allow=["Edit"]),  # 跳过 Edit Modal
        permission_callback=allow_all,
        plan_mode=False,
    )
    do_events = [e async for e in do_agent.run("do it")]
    assert [e.payload for e in do_events if e.type == "done"] == [StopReason.COMPLETED]
    assert "Write" in {t.name for t in do_agent.available_tools}  # 全工具确实回来了
    print("[OK] e2e: /plan (read-only) → /do (full tools) both COMPLETED")


def main() -> None:
    test_config_schema()
    test_factory()
    test_conversation()
    test_config_missing()
    asyncio.run(test_e2e_read_then_edit_completes())
    asyncio.run(test_e2e_5_stop_conditions())
    asyncio.run(test_e2e_plan_then_do_workflow())
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
