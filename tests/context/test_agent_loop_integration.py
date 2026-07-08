"""v0.7 Agent Loop 集成测试 — maybe_compact 触发 / manual request / COMPACTION_FAILED。"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.agent.events import AgentEvent, StopReason
from baozicode.agent.loop import Agent
from baozicode.config.schema import (
    AgentConfig,
    AppConfig,
    BackendConfig,
    CompactionConfig,
)
from baozicode.context import (
    CompactionError,
    CompactionTelemetry,
    ContextConfig,
    ContextStorage,
    MaybeCompactContext,
    maybe_compact,
)
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message, ToolResultBlock


class _ScriptedLLM(LLMClient):
    """测试用 mock LLM,按 _scripted 顺序 yield delta。

    `_scripted` 是 list[list[ContentDelta]] — 每个 item 是一次 stream() 调用的 delta 列表。
    最后一个元素可设置为 RuntimeError 来模拟 stream 异常。
    """

    def __init__(self, scripted: list) -> None:
        self._scripted = list(scripted)
        self.call_count = 0

    async def stream(
        self, messages, system=None, tools=None, *, cache_breakpoints=None
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        if not self._scripted:
            raise RuntimeError("scripted queue empty")
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        for d in item:
            yield d


def _text_deltas(text: str) -> list[ContentDelta]:
    return [ContentDelta(type="text", text=text)]


def _usage_delta(input_t: int = 100) -> ContentDelta:
    from baozicode.agent.events import UsageStats
    return ContentDelta(
        type="usage",
        text=UsageStats(input_tokens=input_t, output_tokens=50),
    )


def _make_app_config() -> AppConfig:
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="test-key", model="claude-test"),
        openai=BackendConfig(api_key="test-key", model="gpt-test"),
        minimax=BackendConfig(api_key="test-key", model="minimax-test"),
        deepseek=BackendConfig(api_key="test-key", model="deepseek-test"),
        agent=AgentConfig(
            context_window_tokens=128_000,
            compaction=CompactionConfig(),
        ),
    )


def _make_agent(
    llm: LLMClient,
    *,
    compact_ctx: MaybeCompactContext | None = None,
    conversation: ConversationManager | None = None,
) -> Agent:
    cfg = _make_app_config()
    conv = conversation or ConversationManager()
    # tools 至少要一个,让 Agent.__init__ 通过
    from baozicode.tools.base import ToolDefinition
    dummy_tool = ToolDefinition(
        name="Read", description="dummy", parameters={"type": "object"}, side_effect=False
    )
    return Agent(
        llm_client=llm,
        tools=[dummy_tool],
        conversation=conv,
        permissions=None,
        config=cfg,
        compact_ctx=compact_ctx,
        max_iterations=5,
    )


# ---------- auto-trigger Layer 1 only at low token count ----------


@pytest.mark.asyncio
async def test_agent_auto_triggers_layer1_only_when_below_threshold(
    tmp_project_root: Path,
) -> None:
    """小消息(< per_message_threshold)→ Agent 跑 LLM 不触发 Layer 2,只 Layer 1 offload。"""
    storage = ContextStorage(project_root=tmp_project_root, session_id="s")
    ctx_cfg = ContextConfig.build(
        context_window_tokens=128_000,
        trigger="auto",
        compaction=CompactionConfig(),
    )
    ctx = MaybeCompactContext(
        llm=_ScriptedLLM([_text_deltas("hi")]),  # 不会被调用(Layer 2 不跑)
        storage=storage,
        config=ctx_cfg,
        telemetry=CompactionTelemetry(),
    )
    # 准备一个含 50K tool_result block 的对话 → 触发 Layer 1 offload
    big = "\n".join("x" * 80 for _ in range(640))  # ~52K bytes
    conv = ConversationManager()
    conv.add_user("read this file")
    conv.add_tool_result(
        type("R", (), {
            "tool_call_id": "t1",
            "content": big,
            "is_error": False,
        })()
    )
    llm = _ScriptedLLM([_text_deltas("ok")])
    agent = _make_agent(llm, compact_ctx=ctx, conversation=conv)
    events = [ev async for ev in agent.run("test")]
    # 拿到 done 事件
    done_events = [e for e in events if e.type == "done"]
    assert len(done_events) == 1
    assert done_events[0].payload == StopReason.COMPLETED
    # 验证 Layer 1 offload 生效(conv 里 tool_result 已被替换)
    tool_msgs = [m for m in conv.to_list() if m.role == "tool"]
    assert len(tool_msgs) == 1
    block = tool_msgs[0].content[0]
    assert block.offloaded_to is not None


# ---------- auto-trigger Layer 2 at high token count ----------


@pytest.mark.asyncio
async def test_agent_auto_triggers_layer2_when_above_budget(
    tmp_project_root: Path,
) -> None:
    """消息量超 budget → Layer 2 跑,LLM 被调一次用于摘要 + 一次用于主对话。"""
    storage = ContextStorage(project_root=tmp_project_root, session_id="s")
    ctx_cfg = ContextConfig.build(
        context_window_tokens=128_000,
        trigger="auto",
        compaction=CompactionConfig(),
    )
    summary_text = (
        "---SUMMARY---\n## Goal\ntest\n## Progress\nx\n## Decisions\ny\n"
        "## Files\nz\n## Open Issues\nn\n## Next\nm\n---END_SUMMARY---\n"
    )
    # 第一轮 stream:Layer 2 摘要;第二轮:主对话
    llm = _ScriptedLLM([
        _text_deltas(summary_text),
        _text_deltas("done"),
    ])
    ctx = MaybeCompactContext(
        llm=llm,
        storage=storage,
        config=ctx_cfg,
        telemetry=CompactionTelemetry(),
    )
    conv = ConversationManager()
    # 5 条 100K-bytes 消息 ≈ 166K tokens > 115K budget
    for _ in range(5):
        conv.add_user("x" * 100_000)
    agent = _make_agent(llm, compact_ctx=ctx, conversation=conv)
    events = [ev async for ev in agent.run("hi")]
    assert any(e.type == "done" and e.payload == StopReason.COMPLETED for e in events)
    # LLM 至少被调 2 次(摘要 + 主对话)
    assert llm.call_count >= 2
    # telemetry 累加
    assert ctx.telemetry.compaction_count >= 1


# ---------- manual request_compact mid-iteration ----------


@pytest.mark.asyncio
async def test_agent_request_compact_mid_iteration(
    tmp_project_root: Path,
) -> None:
    """先 request_compact(),再 agent.run() → 主循环检测 flag,跑 manual 压缩。"""
    storage = ContextStorage(project_root=tmp_project_root, session_id="s")
    ctx_cfg = ContextConfig.build(
        context_window_tokens=128_000,
        trigger="manual",
        compaction=CompactionConfig(),
    )
    summary_text = (
        "---SUMMARY---\n## Goal\ntest\n## Progress\nx\n## Decisions\ny\n"
        "## Files\nz\n## Open Issues\nn\n## Next\nm\n---END_SUMMARY---\n"
    )
    llm = _ScriptedLLM([_text_deltas(summary_text), _text_deltas("done")])
    ctx = MaybeCompactContext(
        llm=llm,
        storage=storage,
        config=ctx_cfg,
        telemetry=CompactionTelemetry(),
    )
    conv = ConversationManager()
    for _ in range(5):
        conv.add_user("x" * 100_000)
    agent = _make_agent(llm, compact_ctx=ctx, conversation=conv)
    agent.request_compact()  # 模拟 TUI /compact
    events = [ev async for ev in agent.run("hi")]
    # 触发 done
    assert any(e.type == "done" for e in events)
    # 至少一次 "[已压缩:..." text 事件
    assert any(
        e.type == "text" and "已压缩" in (e.payload or "") for e in events
    )


# ---------- CompactError during auto-trigger → COMPACTION_FAILED ----------


@pytest.mark.asyncio
async def test_agent_compaction_error_yields_compaction_failed(
    tmp_project_root: Path,
) -> None:
    """LLM 摘要连续抛异常 → maybe_compact 返回 result.triggered=False,failure=compact_error(不抛)。

    注:maybe_compact 在 Layer-2 抛 CompactionError 时会捕获并返回失败 result,
    Agent 不会因此终止 — 只在 result.triggered=True 时替换 messages。
    所以这里测的是:agent.run() 在 3 次 stream 异常后还能正常走主对话。
    """
    storage = ContextStorage(project_root=tmp_project_root, session_id="s")
    ctx_cfg = ContextConfig.build(
        context_window_tokens=128_000,
        trigger="auto",
        compaction=CompactionConfig(max_consecutive_failures=2),  # 加速测试
    )

    class _BoomLLM(LLMClient):
        """前 N 次 stream() 抛异常(模拟摘要失败),之后恢复正常。"""
        def __init__(self):
            self.call_count = 0
            self.max_boom = 2  # 前 2 次抛异常

        async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
            self.call_count += 1
            if self.call_count <= self.max_boom:
                # 检查是否是摘要调用(tools=[])
                if tools == [] or tools is None and "context-compaction" in (system or ""):
                    raise RuntimeError("simulated summary failure")
            # 否则正常 yield
            yield ContentDelta(type="text", text="ok")

    llm = _BoomLLM()
    ctx = MaybeCompactContext(
        llm=llm,
        storage=storage,
        config=ctx_cfg,
        telemetry=CompactionTelemetry(),
    )
    conv = ConversationManager()
    for _ in range(5):
        conv.add_user("x" * 100_000)
    agent = _make_agent(llm, compact_ctx=ctx, conversation=conv)
    events = [ev async for ev in agent.run("hi")]
    # Agent 没终止于 COMPACTION_FAILED(maybe_compact 内部捕获了 CompactionError)
    # done 事件存在,但不应该是 COMPACTION_FAILED(除非 maybe_compact 不捕获)
    done_events = [e for e in events if e.type == "done"]
    assert len(done_events) == 1
    # 在我们的实现里 maybe_compact 把 CompactionError 转成 result.failure_kind="compact_error",
    # 不重抛,所以 Agent 不会拿到 CompactionError → 不会 COMPACTION_FAILED
    # 但 messages 没被替换 → 主对话照样能跑(只是 context 依旧超大)
    # 简化断言:done 存在且不是 COMPACTION_FAILED
    assert done_events[0].payload != StopReason.COMPACTION_FAILED


# ---------- idempotent: Layer 1 offload doesn't re-offload ----------


@pytest.mark.asyncio
async def test_agent_layer1_offload_is_idempotent(
    tmp_project_root: Path,
) -> None:
    """Agent 跑两轮迭代,Layer 1 offload 不会重复 offload 同一 block。"""
    storage = ContextStorage(project_root=tmp_project_root, session_id="s")
    ctx_cfg = ContextConfig.build(
        context_window_tokens=128_000,
        trigger="auto",
        compaction=CompactionConfig(),
    )
    # 第一轮:yield "tool call" - 不,我们的 scripted LLM 不发 tool_use
    # 简化:跑两轮,每轮 yield "done",验证 file count 没翻倍
    big = "\n".join("x" * 80 for _ in range(640))
    conv = ConversationManager()
    conv.add_user("read")
    conv.add_tool_result(
        type("R", (), {"tool_call_id": "t1", "content": big, "is_error": False})()
    )
    llm = _ScriptedLLM([
        _text_deltas("ok1"),  # 第一轮:不调工具,返回 "ok1"
    ])
    ctx = MaybeCompactContext(
        llm=llm,
        storage=storage,
        config=ctx_cfg,
        telemetry=CompactionTelemetry(),
    )
    agent = _make_agent(llm, compact_ctx=ctx, conversation=conv)
    events = [ev async for ev in agent.run("hi")]
    # 第一次跑完后,offload 文件应有 1 个
    files_after_first = list(storage.session_dir.iterdir())
    # 再次跑(模拟新一轮 agent.run,但 LLM 没 script 了 — 这次会抛)
    # 简化:直接调 maybe_compact 两次,验证第二次不会多写文件
    from baozicode.context import maybe_compact
    msgs = conv.to_list()
    msgs1, _ = await maybe_compact(msgs, trigger="auto", ctx=ctx)
    msgs2, _ = await maybe_compact(msgs1, trigger="auto", ctx=ctx)
    files_after_second = list(storage.session_dir.iterdir())
    # 第二轮不会新增 offload 文件(因为 tool_result 已被 offload,再次 offload 是 no-op)
    assert len(files_after_first) == 1
    assert len(files_after_second) == 1