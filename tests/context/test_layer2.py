"""v0.7 Layer 2 CompactEngine 单元测试 — partition / parser / circuit breaker / compact。

Mock LLMClient 通过 `_FakeStreamLLM` 控制 stream() 返回文本 + 模拟异常。
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.context import (
    CompactionError,
    CompactEngine,
    ContextConfig,
    CompactionTelemetry,
)
from baozicode.llm.base import ContentDelta, LLMClient, Message


def _well_formed_summary() -> str:
    return (
        "---ANALYSIS---\n"
        "用户想压缩上下文。我看到 5 条对话,主要内容是修 bug。\n"
        "---END_ANALYSIS---\n"
        "---SUMMARY---\n"
        "## Goal\n修一个 bug\n"
        "## Progress\n修了 3 个文件\n"
        "## Decisions\n用 X 方案\n"
        "## Files\na.py, b.py\n"
        "## Open Issues\n无\n"
        "## Next\n跑测试\n"
        "---END_SUMMARY---\n"
    )


class _FakeStreamLLM(LLMClient):
    """测试用 mock LLMClient。

    `responses`:每次 stream() 调用 pop 一个;text 或 Exception(后者模拟 stream 异常)。
    """

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.call_count = 0

    async def stream(
        self,
        messages,
        system=None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        if not self._responses:
            raise RuntimeError("no more mock responses")
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        # resp 是字符串:yield text delta
        yield ContentDelta(type="text", text=resp)


def _make_engine(llm: LLMClient, config: ContextConfig, telemetry: CompactionTelemetry | None = None):
    return CompactEngine(llm=llm, config=config, telemetry=telemetry or CompactionTelemetry())


# ---------- partition tail tests ----------


def test_partition_tail_respects_min_messages_and_tokens(
    context_config: ContextConfig,
) -> None:
    """10 条 1500-token 消息 → tail 取最后 7 条(1500*7=10500 ≥ 10000,count 7 ≥ 5)。"""
    engine = _make_engine(_FakeStreamLLM([]), context_config)
    msgs = [Message(role="user", content="x" * 4500) for _ in range(10)]  # ~1500 tokens each
    head, tail = engine._partition_tail(msgs)
    assert len(tail) == 7
    assert len(head) == 3


def test_partition_tail_single_huge_message_satisfies_token_threshold(
    context_config: ContextConfig,
) -> None:
    """单条 30K tokens(> recent_window_tokens=10000)→ tail 仅这一条。"""
    engine = _make_engine(_FakeStreamLLM([]), context_config)
    msgs = [Message(role="user", content="x" * 30_000)]  # ≈10K tokens
    head, tail = engine._partition_tail(msgs)
    assert len(tail) == 1
    assert tail[0] is msgs[0]
    assert head == []


def test_partition_tail_needs_more_for_tokens(
    context_config: ContextConfig,
) -> None:
    """4 条 800-token 消息 → count=4 ≥ 5? 不够 → 至少再加 1 条(直到 token 满足)。"""
    engine = _make_engine(_FakeStreamLLM([]), context_config)
    msgs = [Message(role="user", content="x" * 2400) for _ in range(4)]  # ~800 tokens each
    head, tail = engine._partition_tail(msgs)
    # 4 条 = 3200 tokens,但 min_messages=5,token=10000
    # 必须加到 token ≥ 10000:10000/800 = 12.5 → 13 条才够(但只有 4 条)
    # 所以全选
    assert len(tail) == 4
    assert head == []


# ---------- parser tests ----------


def test_parse_summary_well_formed(context_config: ContextConfig) -> None:
    text = _well_formed_summary()
    parsed = CompactEngine._parse_summary(text)
    assert parsed is not None
    assert "## Goal" in parsed
    assert "## Next" in parsed
    assert "---ANALYSIS---" not in parsed  # analysis 被丢弃


def test_parse_summary_missing_end_marker(context_config: ContextConfig) -> None:
    text = (
        "---SUMMARY---\n## Goal\nx\n## Progress\ny\n"
        # 没有 ---END_SUMMARY---
    )
    parsed = CompactEngine._parse_summary(text)
    assert parsed is not None
    assert "## Goal" in parsed
    assert "## Progress" in parsed


def test_parse_summary_missing_summary_marker(context_config: ContextConfig) -> None:
    text = "## Goal\nsomething\n---END_SUMMARY---\n"
    parsed = CompactEngine._parse_summary(text)
    assert parsed is None


# ---------- circuit breaker tests ----------


@pytest.mark.asyncio
async def test_circuit_breaker_raises_after_max_failures(context_config: ContextConfig) -> None:
    """3 次 stream() 异常 → CompactionError,且 call_count=3。"""
    llm = _FakeStreamLLM([RuntimeError("boom")] * 5)
    engine = _make_engine(llm, context_config)
    # 5 条 30K-token 消息:partition 后 tail 仅 1 条,head 4 条 → 触发 LLM 调用
    msgs = [Message(role="user", content="x" * 30_000) for _ in range(5)]
    with pytest.raises(CompactionError):
        await engine.compact(msgs)
    # max_consecutive_failures=3 → 3 次调用
    assert llm.call_count == 3


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_success(context_config: ContextConfig) -> None:
    """先失败 1 次,再成功 → _consecutive_failures 归 0。"""
    # 第一次:异常;第二次:成功摘要;但因为 max_summary_tokens=2000,budget=128000-13000=115000
    # tail 估算 tokens > 115000? head 30K 单条消息被 partition 当 tail → head=[]
    # 所以 compact 会直接 return messages, estimate_messages_tokens(messages) → 不到 budget
    # 这种情况下不会调 LLM。需要大消息触发 partition 真的有 head。
    # 改:准备 5 条 30K tokens 的消息,partition 后 tail 仅 1 条(head 4 条),head 触发摘要。
    # 第一次异常,第二次成功。但 budget 阈值是 115K,post-summary 仅 1 条 30K ≈ 10K tokens,
    # 远小于 115K,应该成功。
    llm = _FakeStreamLLM([RuntimeError("first boom"), _well_formed_summary()])
    engine = _make_engine(llm, context_config)
    msgs = [Message(role="user", content="x" * 30_000) for _ in range(5)]
    new_msgs, tokens_after = await engine.compact(msgs)
    # 成功 → 失败计数归 0
    assert engine._consecutive_failures == 0
    # telemetry 累加
    assert engine._telemetry.compaction_count == 1
    assert engine._telemetry.total_tokens_saved >= 0
    assert engine._telemetry.last_compact_at is not None
    assert llm.call_count == 2


# ---------- compact result structure test ----------


@pytest.mark.asyncio
async def test_compact_produces_summary_post_reminder_tail(
    context_config: ContextConfig,
) -> None:
    """compact() 成功 → [summary_msg, post_compaction_msg, *tail]。"""
    llm = _FakeStreamLLM([_well_formed_summary()])
    engine = _make_engine(llm, context_config)
    # 5 条 30K-token 消息 → partition 后 tail 1 条,head 4 条
    msgs = [Message(role="user", content="x" * 30_000) for _ in range(5)]
    new_msgs, _ = await engine.compact(msgs)
    assert len(new_msgs) == 3  # summary + post_compaction + tail(1 条)
    # summary 消息
    assert new_msgs[0].role == "user"
    assert isinstance(new_msgs[0].content, str)
    assert "context_summary" in new_msgs[0].content
    assert "## Goal" in new_msgs[0].content
    # post_compaction 消息
    assert new_msgs[1].role == "user"
    assert isinstance(new_msgs[1].content, str)
    assert "post_compaction" in new_msgs[1].content
    # tail 消息 = 原 msgs[-1]
    assert new_msgs[2] is msgs[-1]