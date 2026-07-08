"""v0.7 maybe_compact 编排器测试 — Layer 1 + 条件 Layer 2 调度。"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.config.schema import CompactionConfig
from baozicode.context import (
    CompactionError,
    CompactEngine,
    CompactionTelemetry,
    ContextConfig,
    ContextStorage,
    MaybeCompactContext,
    maybe_compact,
)
from baozicode.llm.base import ContentDelta, LLMClient, Message, ToolResultBlock


class _FakeStreamLLM(LLMClient):
    """Test mock — stream() 返回预置文本。"""

    def __init__(self, response: str = "") -> None:
        self._response = response
        self.call_count = 0

    async def stream(
        self, messages, system=None, tools=None, *, cache_breakpoints=None
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        yield ContentDelta(type="text", text=self._response)


def _well_formed_summary() -> str:
    return (
        "---SUMMARY---\n"
        "## Goal\n压缩测试\n"
        "## Progress\n跑通了\n"
        "## Decisions\n无\n"
        "## Files\ntest.py\n"
        "## Open Issues\n无\n"
        "## Next\n收工\n"
        "---END_SUMMARY---\n"
    )


@pytest.fixture
def storage(tmp_project_root: Path) -> ContextStorage:
    return ContextStorage(project_root=tmp_project_root, session_id="test-session")


@pytest.fixture
def auto_config() -> ContextConfig:
    return ContextConfig.build(
        context_window_tokens=128_000,
        trigger="auto",
        compaction=CompactionConfig(),
    )


@pytest.fixture
def manual_config() -> ContextConfig:
    return ContextConfig.build(
        context_window_tokens=128_000,
        trigger="manual",
        compaction=CompactionConfig(),
    )


# ---------- Layer 1 only ----------


@pytest.mark.asyncio
async def test_small_messages_no_compaction(
    storage: ContextStorage,
    auto_config: ContextConfig,
) -> None:
    """小消息(总 token 远低于 budget)→ Layer 1 跑但不触发 Layer 2。"""
    llm = _FakeStreamLLM()
    ctx = MaybeCompactContext(llm=llm, storage=storage, config=auto_config, telemetry=CompactionTelemetry())
    msgs = [Message(role="user", content="hello world")]
    new_msgs, result = await maybe_compact(msgs, trigger="auto", ctx=ctx)
    assert result.triggered is False
    assert result.failure_kind == "below_threshold"
    assert llm.call_count == 0  # Layer 2 没跑


@pytest.mark.asyncio
async def test_layer1_offloads_large_tool_result(
    storage: ContextStorage,
    auto_config: ContextConfig,
) -> None:
    """单条 tool message 含 50K block → Layer 1 offload 后总 token 仍小 → 不触发 Layer 2。"""
    llm = _FakeStreamLLM()
    ctx = MaybeCompactContext(llm=llm, storage=storage, config=auto_config, telemetry=CompactionTelemetry())
    big = "\n".join("x" * 80 for _ in range(640))  # ~51K bytes
    msgs = [
        Message(role="user", content="read this"),
        Message(role="tool", content=[ToolResultBlock(tool_use_id="t1", content=big)]),
    ]
    new_msgs, result = await maybe_compact(msgs, trigger="auto", ctx=ctx)
    # 51K bytes → ~17K tokens 离 budget 115K 还远,不触发 Layer 2
    assert result.triggered is False
    assert llm.call_count == 0
    # 但 Layer 1 offload 跑过 → tool_result block 被替换
    tool_msg = new_msgs[1]
    assert isinstance(tool_msg.content, list)
    block = tool_msg.content[0]
    assert block.offloaded_to is not None


# ---------- Layer 2 triggered ----------


@pytest.mark.asyncio
async def test_large_messages_layer2_runs(
    storage: ContextStorage,
    auto_config: ContextConfig,
) -> None:
    """5 条 100K bytes 消息(共 ~166K tokens,超过 budget 115K)→ Layer 2 跑。"""
    llm = _FakeStreamLLM(response=_well_formed_summary())
    ctx = MaybeCompactContext(llm=llm, storage=storage, config=auto_config, telemetry=CompactionTelemetry())
    # 100K bytes ASCII ≈ 33K tokens;5 条 = 165K tokens > 115K budget
    msgs = [Message(role="user", content="x" * 100_000) for _ in range(5)]
    new_msgs, result = await maybe_compact(msgs, trigger="auto", ctx=ctx)
    assert result.triggered is True
    assert result.failure_kind == "none"
    assert result.tokens_after < result.tokens_before
    assert llm.call_count == 1


# ---------- Layer 2 error ----------


@pytest.mark.asyncio
async def test_layer2_raises_compaction_error_propagates(
    storage: ContextStorage,
    auto_config: ContextConfig,
) -> None:
    """LLM 连续 3 次抛异常 → CompactionError → result.triggered=False, failure_kind="compact_error"。"""
    # 用一个每次 stream() 都抛异常的 mock
    class _BoomLLM(LLMClient):
        def __init__(self):
            self.call_count = 0

        async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
            self.call_count += 1
            raise RuntimeError("simulated LLM error")
            yield  # unreachable, makes it async generator

    llm = _BoomLLM()
    ctx = MaybeCompactContext(llm=llm, storage=storage, config=auto_config, telemetry=CompactionTelemetry())
    # 5 × 100K bytes = 500K bytes ≈ 166K tokens > 115K budget → Layer 2 触发
    msgs = [Message(role="user", content="x" * 100_000) for _ in range(5)]
    new_msgs, result = await maybe_compact(msgs, trigger="auto", ctx=ctx)
    assert result.triggered is False
    assert result.failure_kind == "compact_error"
    # messages 不变
    assert new_msgs == msgs
    assert llm.call_count == 3


# ---------- manual trigger reserve ----------


@pytest.mark.asyncio
async def test_manual_trigger_uses_3k_reserve(
    storage: ContextStorage,
) -> None:
    """manual 触发 → reserve=3K, budget = 128K - 3K = 125K,小一些。"""
    # 用 117K tokens 的消息:auto(13K reserve, budget 115K)不触发,manual(3K reserve, budget 125K)触发
    # 117K bytes / 3 ≈ 39K tokens,远超 115K,auto 也会触发
    # 改:用 ~50K bytes ≈ 17K tokens:auto(115K budget)不触发,manual(125K budget)也不触发
    # 真正能区分 reserve 的是:消息量在 (128K-13K, 128K-3K) 区间内
    # 用 120K tokens 的消息:auto 不触发,manual 触发
    # 120K tokens ≈ 360K bytes ASCII
    # 但 360K byte tool_result 会被 Layer 1 offload 成 preview,降低到 < 1K
    # 所以需要 ~30 条 12K bytes(没超 per_block 8K)user 消息:
    # 30 * 12K = 360K bytes ≈ 120K tokens
    llm = _FakeStreamLLM(response=_well_formed_summary())
    manual_ctx = MaybeCompactContext(
        llm=llm,
        storage=storage,
        config=ContextConfig.build(
            context_window_tokens=128_000,
            trigger="manual",
            compaction=CompactionConfig(),
        ),
        telemetry=CompactionTelemetry(),
    )
    # 12K bytes user messages × 30 = 360K bytes ≈ 120K tokens
    msgs = [Message(role="user", content="x" * 12_000) for _ in range(30)]
    _, result = await maybe_compact(msgs, trigger="manual", ctx=manual_ctx)
    # budget = 128K - 3K = 125K,total ~120K tokens,post-Layer1 ≈ 120K > 125K?
    # 实际上 120K < 125K,所以 manual 也不会触发
    # 这测试不严谨,跳过 OR 改成大消息
    # 改:30 条 15K bytes × 30 = 450K bytes ≈ 150K tokens,manual 也触发
    # 改:auto 不触发,manual 触发:需要一个 < 115K 但 > 125K 的区间 — 不可能(manual budget 大)
    # 反过来:auto 触发,manual 不触发:用 120K bytes × 30 = 360K bytes ≈ 120K tokens > 115K(auto budget)
    # 但 < 125K(manual budget)→ manual 不触发,auto 触发 ✓
    msgs_auto = [Message(role="user", content="x" * 12_000) for _ in range(30)]
    auto_config = ContextConfig.build(
        context_window_tokens=128_000,
        trigger="auto",
        compaction=CompactionConfig(),
    )
    llm_auto_ctx = MaybeCompactContext(
        llm=_FakeStreamLLM(response=_well_formed_summary()),
        storage=ContextStorage(project_root=storage.project_root, session_id="s2"),
        config=auto_config,
        telemetry=CompactionTelemetry(),
    )
    _, auto_result = await maybe_compact(msgs_auto, trigger="auto", ctx=llm_auto_ctx)
    # auto: budget = 115K,total ≈ 120K → 触发
    assert auto_result.triggered is True
    # manual: budget = 125K,total ≈ 120K → 不触发
    assert result.triggered is False
    assert result.failure_kind == "below_threshold"