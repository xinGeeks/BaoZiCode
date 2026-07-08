"""v0.8 Agent 循环钩子点测试 — archiver 转发 / memory_updater 注入 /
enqueue_reminder / run() 末端的异步记忆触发逻辑。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import AgentEvent, StopReason
from baozicode.agent.loop import Agent, ReminderKind
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.sessions.archive import SessionArchiver
from baozicode.tools.base import ToolCall
from baozicode.tools.registry import get_all_tools

sys.path.insert(0, str(Path(__file__).parent))
from _agent_helpers import make_minimal_config


class _Perm:
    deny: list[str] = []
    auto_allow: list[str] = []


class _ScriptedLLM(LLMClient):
    """按 responses 序列返回 delta,用于驱动 Agent.run。"""

    def __init__(self, responses: list[list[ContentDelta]]) -> None:
        self.responses = responses
        self.call_count = 0
        self.last_messages: list[Message] = []

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        self.last_messages = list(messages)
        for d in self.responses[self.call_count - 1]:
            yield d


def _drain(agent: Agent, text: str) -> list[AgentEvent]:
    out: list[AgentEvent] = []
    async def _go():
        async for ev in agent.run(text):
            out.append(ev)
    asyncio.run(_go())
    return out


# ---- set_archiver ----


def test_set_archiver_forwards_to_conversation(tmp_path: Path) -> None:
    """Agent.set_archiver() 应转发到 ConversationManager.set_archiver()。"""
    conv = ConversationManager()
    a = Agent(
        llm_client=_ScriptedLLM([[ContentDelta(type="text", text="hi")]]),
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    sessions_root = tmp_path / "sessions"
    arch = SessionArchiver(sessions_root, session_id="20260708-153000-a1b2")
    a.set_archiver(arch)

    _drain(a, "hello")

    arch.close()
    jsonl = sessions_root / "20260708-153000-a1b2.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
    # 2 条消息(1 user + 1 assistant)
    assert len(lines) == 2
    print(f"[OK] set_archiver: 2 messages appended to {jsonl.name}")


def test_set_archiver_none_disables() -> None:
    """set_archiver(None) 之后 add_* 不再 append。"""
    spy_calls: list[Message] = []
    class _Spy:
        def append(self, msg: Message) -> bool:
            spy_calls.append(msg)
            return True
    conv = ConversationManager(archiver=_Spy())  # type: ignore[arg-type]
    a = Agent(
        llm_client=_ScriptedLLM([[ContentDelta(type="text", text="hi")]]),
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    a.set_archiver(None)
    _drain(a, "hi")
    assert spy_calls == []  # set None 后禁用


# ---- set_memory_updater ----


def test_set_memory_updater_stored() -> None:
    """set_memory_updater 应存到 self._memory_updater。"""
    a = Agent(
        llm_client=_ScriptedLLM([[ContentDelta(type="text", text="hi")]]),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    assert a._memory_updater is None

    class _Updater:
        async def update(self, snapshot):
            return None

    updater = _Updater()
    a.set_memory_updater(updater)
    assert a._memory_updater is updater


# ---- enqueue_reminder + _inject_reminders ----


def test_enqueue_reminder_once_consumed_next_iteration() -> None:
    """ttl=once 的 reminder 注入一次后从队列移除。"""
    a = Agent(
        llm_client=_ScriptedLLM([
            [ContentDelta(type="text", text="first")],
            [ContentDelta(type="text", text="second")],
        ]),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    a.enqueue_reminder("time_gap", "已经 2 小时没说话了")

    # 第一轮迭代:env reminder + time_gap reminder + user = 3
    msgs1 = a._inject_reminders(
        [Message(role="user", content="hi")], iteration=1
    )
    assert len(msgs1) == 3
    time_gap_msgs = [m for m in msgs1 if "time_gap" in m.content]
    assert len(time_gap_msgs) == 1

    # 队列已清空
    assert a._pending_reminders == []

    # 第二轮迭代应不再注入
    msgs2 = a._inject_reminders(
        [Message(role="user", content="hi")], iteration=2
    )
    assert len(msgs2) == 2  # env + user,无 time_gap
    assert not any("time_gap" in m.content for m in msgs2)
    print("[OK] enqueue_reminder once: 注入 1 次后丢弃")


def test_enqueue_reminder_sticky_repeats() -> None:
    """ttl=sticky 的 reminder 每轮迭代都重发。"""
    a = Agent(
        llm_client=_ScriptedLLM([[ContentDelta(type="text", text="hi")]]),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    a.enqueue_reminder("memory_refreshed", "记忆已更新", ttl="sticky")

    msgs1 = a._inject_reminders(
        [Message(role="user", content="hi")], iteration=1
    )
    msgs2 = a._inject_reminders(
        [Message(role="user", content="hi")], iteration=2
    )
    msgs3 = a._inject_reminders(
        [Message(role="user", content="hi")], iteration=3
    )
    for msgs in (msgs1, msgs2, msgs3):
        assert any("memory_refreshed" in m.content for m in msgs)
    # sticky 的 reminder 还在队列里
    assert len(a._pending_reminders) == 1
    print("[OK] enqueue_reminder sticky: 3 轮迭代都注入")


def test_enqueue_reminder_mixed_ttls() -> None:
    """once 和 sticky 混用时,once 一次后只剩 sticky。"""
    a = Agent(
        llm_client=_ScriptedLLM([[ContentDelta(type="text", text="x")]]),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    a.enqueue_reminder("time_gap", "gap msg")           # once
    a.enqueue_reminder("memory_refreshed", "mem msg", ttl="sticky")
    assert len(a._pending_reminders) == 2

    msgs = a._inject_reminders(
        [Message(role="user", content="hi")], iteration=1
    )
    # 两条 reminder 都在注入里
    assert any("time_gap" in m.content for m in msgs)
    assert any("memory_refreshed" in m.content for m in msgs)

    # 队列只剩 sticky 那条
    assert len(a._pending_reminders) == 1
    assert "memory_refreshed" in a._pending_reminders[0].content


def test_inject_reminders_disabled_by_config() -> None:
    """agent.enable_system_reminders=False → enqueue 的 reminder 也不注入。"""
    cfg = make_minimal_config()
    # 强制 disable
    from baozicode.config.schema import AgentConfig
    cfg = cfg.model_copy(update={
        "agent": AgentConfig(enable_system_reminders=False),
    })
    a = Agent(
        llm_client=_ScriptedLLM([[ContentDelta(type="text", text="x")]]),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=cfg,
    )
    a.enqueue_reminder("time_gap", "should not appear")

    msgs = a._inject_reminders(
        [Message(role="user", content="hi")], iteration=1
    )
    # 应原样返回
    assert len(msgs) == 1
    assert msgs[0].content == "hi"


# ---- run() 末端的 memory update 触发 ----


def test_run_completed_triggers_memory_updater() -> None:
    """COMPLETED 终止时,fire-and-forget 调 updater.update(snapshot)。"""
    update_calls: list[list[Message]] = []
    update_started = asyncio.Event()

    class _Updater:
        async def update(self, snapshot: list[Message]) -> None:
            update_calls.append(list(snapshot))
            update_started.set()

    conv = ConversationManager()
    a = Agent(
        llm_client=_ScriptedLLM([[ContentDelta(type="text", text="hi")]]),
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    a.set_memory_updater(_Updater())

    events = _drain(a, "user message")
    reasons = [e.payload for e in events if e.type == "done"]
    assert reasons == [StopReason.COMPLETED]

    # 等异步 task 跑完
    asyncio.run(asyncio.wait_for(update_started.wait(), timeout=2.0))
    assert len(update_calls) == 1
    # snapshot 含 user + assistant
    assert len(update_calls[0]) == 2
    assert update_calls[0][0].role == "user"
    assert update_calls[0][1].role == "assistant"
    print("[OK] run COMPLETED → memory updater 触发 1 次")


def test_run_max_iterations_also_triggers_memory() -> None:
    """MAX_ITERATIONS_REACHED 兜底时也触发 updater(用户可能想看长跑对话后能写什么)。"""
    update_calls: list[list[Message]] = []
    update_started = asyncio.Event()

    class _Updater:
        async def update(self, snapshot: list[Message]) -> None:
            update_calls.append(list(snapshot))
            update_started.set()

    # 永远要工具的 LLM → 跑完 max_iterations
    # 工具选 Bash(command="echo hi") 走真路径,permission_callback 放行
    class _EndlessToolLLM(LLMClient):
        async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
            yield ContentDelta(type="tool_use", text=ToolCall(
                id="t1", name="Bash", arguments={"command": "echo hi"}
            ))

    async def _allow_all(call):
        return True

    a = Agent(
        llm_client=_EndlessToolLLM(),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
        max_iterations=3,
        permission_callback=_allow_all,
    )
    a.set_memory_updater(_Updater())

    events = _drain(a, "go")
    reasons = [e.payload for e in events if e.type == "done"]
    assert reasons == [StopReason.MAX_ITERATIONS_REACHED]

    asyncio.run(asyncio.wait_for(update_started.wait(), timeout=2.0))
    assert len(update_calls) == 1
    print("[OK] run MAX_ITERATIONS_REACHED → memory updater 触发 1 次")


def test_run_user_cancelled_does_not_trigger_memory() -> None:
    """USER_CANCELLED 不触发 updater — 用户主动中止,不该偷偷写笔记。"""
    update_calls: list[list[Message]] = []

    class _Updater:
        async def update(self, snapshot: list[Message]) -> None:
            update_calls.append(list(snapshot))

    class _NeverEndingLLM(LLMClient):
        """持续 1 秒不出 token,期间主循环检查 cancel。"""
        async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
            await asyncio.sleep(1.0)
            yield ContentDelta(type="text", text="late")

    a = Agent(
        llm_client=_NeverEndingLLM(),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
        max_iterations=3,
    )
    a.set_memory_updater(_Updater())

    # 在另一个 task 里 cancel
    async def _go():
        events: list[AgentEvent] = []
        agen = a.run("x")
        # 拿到第一个 event 后立即 cancel
        first = await agen.__anext__()
        events.append(first)
        a.cancel()
        async for ev in agen:
            events.append(ev)
        return events
    events = asyncio.run(_go())
    reasons = [e.payload for e in events if e.type == "done"]
    assert reasons == [StopReason.USER_CANCELLED]
    # updater 不应被触发(给一个小宽限等 task 跑)
    asyncio.run(asyncio.sleep(0.1))
    assert update_calls == []
    print("[OK] run USER_CANCELLED → memory updater 不触发")


def test_run_no_updater_set_does_not_error() -> None:
    """updater=None 时 run() 不应抛。"""
    conv = ConversationManager()
    a = Agent(
        llm_client=_ScriptedLLM([[ContentDelta(type="text", text="hi")]]),
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    # _memory_updater is None — 默认
    events = _drain(a, "hi")
    assert [e.payload for e in events if e.type == "done"] == [StopReason.COMPLETED]


def test_run_reminder_injected_into_llm_messages() -> None:
    """enqueue 后真跑,LLM 收到的 messages 列表应含 reminder。"""
    llm = _ScriptedLLM([[ContentDelta(type="text", text="ok")]])
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
    )
    a.enqueue_reminder("time_gap", "上次对话在 2 小时前")

    _drain(a, "hello")
    # LLM 收到的 messages 列表里应能找到 reminder
    found = any(
        "time_gap" in m.content and "system-reminder" in m.content
        for m in llm.last_messages
    )
    assert found, f"reminder 未注入,last_messages 角色={[m.role for m in llm.last_messages]}"
    print("[OK] enqueue reminder 跑到 LLM 的 messages 列表里")
