"""v0.8 sessions resume tests — 异常四件套 + 时间间隔。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.context import MaybeCompactContext
from baozicode.llm.base import Message, TextBlock, ToolUseBlock
from baozicode.sessions.archive import SessionArchiver
from baozicode.sessions.resume import load_session


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _user_entry(text: str, ts: datetime | None = None) -> dict:
    return {
        "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
        "role": "user",
        "blocks": [{"type": "text", "text": text}],
    }


def _assistant_entry(text: str = "", tool_use_id: str | None = None,
                     tool_name: str = "read", tool_input: dict | None = None) -> dict:
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    if tool_use_id:
        blocks.append(
            {
                "type": "tool_use",
                "id": tool_use_id,
                "name": tool_name,
                "input": tool_input or {},
            }
        )
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": "assistant",
        "blocks": blocks,
    }


def _tool_entry(tool_use_id: str) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": "tool",
        "blocks": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": "result",
                "is_error": False,
            }
        ],
        "tool_call_id": tool_use_id,
    }


# ---------------------------------------------------------------------------
# 1. well-formed → exact message reconstruction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_well_formed_jsonl(
    tmp_sessions_root: Path,
    context_storage,
    mock_llm,
    compact_ctx: MaybeCompactContext,
) -> None:
    sid = "20260708-153000-a1b2"
    jsonl = tmp_sessions_root / f"{sid}.jsonl"
    _write_jsonl(
        jsonl,
        [
            _user_entry("hi"),
            _assistant_entry(text="hello back", tool_use_id="t1", tool_name="read"),
            _tool_entry("t1"),
        ],
    )
    result = await load_session(
        sid,
        tmp_sessions_root,
        context_storage=context_storage,
        llm=mock_llm,
        compact_ctx=compact_ctx,
        time_gap_threshold_hours=8,
    )
    assert len(result.messages) == 3
    assert result.messages[0].role == "user"
    assert result.messages[0].content == "hi"
    assert result.messages[1].role == "assistant"
    # assistant 是 list[ContentBlock]
    assert isinstance(result.messages[1].content, list)
    assert result.messages[2].role == "tool"


# ---------------------------------------------------------------------------
# 2. bad line skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_skips_bad_line(
    tmp_sessions_root: Path,
    context_storage,
    mock_llm,
    compact_ctx: MaybeCompactContext,
) -> None:
    sid = "sid-bad"
    jsonl = tmp_sessions_root / f"{sid}.jsonl"
    tmp_sessions_root.mkdir(parents=True, exist_ok=True)
    # 写 3 条:好/坏/好
    raw = (
        json.dumps(_user_entry("first"), ensure_ascii=False)
        + "\n"
        + "{this is not valid json\n"
        + json.dumps(_user_entry("third"), ensure_ascii=False)
        + "\n"
    )
    jsonl.write_text(raw, encoding="utf-8")

    result = await load_session(
        sid,
        tmp_sessions_root,
        context_storage=context_storage,
        llm=mock_llm,
        compact_ctx=compact_ctx,
        time_gap_threshold_hours=8,
    )
    assert len(result.messages) == 2
    assert result.messages[0].content == "first"
    assert result.messages[1].content == "third"
    assert any("skip bad line" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 3. orphan tool_call truncated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_truncates_at_orphan_tool_result(
    tmp_sessions_root: Path,
    context_storage,
    mock_llm,
    compact_ctx: MaybeCompactContext,
) -> None:
    sid = "sid-orphan"
    jsonl = tmp_sessions_root / f"{sid}.jsonl"
    # tool_result id="orphan" 没有任何 tool_use 匹配 → 截断
    _write_jsonl(
        jsonl,
        [
            _user_entry("q1"),
            _assistant_entry(text="a1", tool_use_id="ok", tool_name="read"),
            _tool_entry("ok"),
            _user_entry("q2"),
            _tool_entry("orphan"),  # 触发截断
            _user_entry("q3-after"),
        ],
    )
    result = await load_session(
        sid,
        tmp_sessions_root,
        context_storage=context_storage,
        llm=mock_llm,
        compact_ctx=compact_ctx,
        time_gap_threshold_hours=8,
    )
    # 截断在第 5 行(q2 保留,orphan 之后全丢)
    assert len(result.messages) == 4
    assert result.messages[-1].content == "q2"
    assert any("orphan tool_result" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 4. all tool_results match — no truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_no_truncation_when_all_matched(
    tmp_sessions_root: Path,
    context_storage,
    mock_llm,
    compact_ctx: MaybeCompactContext,
) -> None:
    sid = "sid-matched"
    jsonl = tmp_sessions_root / f"{sid}.jsonl"
    _write_jsonl(
        jsonl,
        [
            _user_entry("q"),
            _assistant_entry(tool_use_id="x1", tool_name="read"),
            _tool_entry("x1"),
            _assistant_entry(tool_use_id="x2", tool_name="read"),
            _tool_entry("x2"),
            _user_entry("done"),
        ],
    )
    result = await load_session(
        sid,
        tmp_sessions_root,
        context_storage=context_storage,
        llm=mock_llm,
        compact_ctx=compact_ctx,
        time_gap_threshold_hours=8,
    )
    assert len(result.messages) == 6
    assert not any("orphan" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 5. time_gap inserted when gap > threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_inserts_time_gap_when_over_threshold(
    tmp_sessions_root: Path,
    context_storage,
    mock_llm,
    compact_ctx: MaybeCompactContext,
) -> None:
    sid = "sid-gap"
    jsonl = tmp_sessions_root / f"{sid}.jsonl"
    old_ts = datetime.now(timezone.utc) - timedelta(hours=12)
    _write_jsonl(
        jsonl,
        [
            _user_entry("old q1", ts=old_ts),
            _assistant_entry(text="old a1", tool_use_id="x", tool_name="read"),
            _tool_entry("x"),
            _user_entry("old q2"),
        ],
    )
    result = await load_session(
        sid,
        tmp_sessions_root,
        context_storage=context_storage,
        llm=mock_llm,
        compact_ctx=compact_ctx,
        time_gap_threshold_hours=8,
    )
    assert result.time_gap_inserted is True
    # reminder 在 messages[-2]
    assert len(result.messages) >= 2
    reminder = result.messages[-2]
    assert reminder.role == "user"
    assert '<system-reminder type="time_gap"' in reminder.content  # type: ignore[operator]


# ---------------------------------------------------------------------------
# 6. time_gap NOT inserted when gap ≤ threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_no_time_gap_when_under_threshold(
    tmp_sessions_root: Path,
    context_storage,
    mock_llm,
    compact_ctx: MaybeCompactContext,
) -> None:
    sid = "sid-recent"
    jsonl = tmp_sessions_root / f"{sid}.jsonl"
    recent_ts = datetime.now(timezone.utc) - timedelta(hours=4)
    _write_jsonl(
        jsonl,
        [
            _user_entry("recent q", ts=recent_ts),
            _assistant_entry(text="a"),
        ],
    )
    result = await load_session(
        sid,
        tmp_sessions_root,
        context_storage=context_storage,
        llm=mock_llm,
        compact_ctx=compact_ctx,
        time_gap_threshold_hours=8,
    )
    assert result.time_gap_inserted is False
    assert not any("time_gap" in m.content for m in result.messages if isinstance(m.content, str))  # type: ignore[operator]


# ---------------------------------------------------------------------------
# 7. empty JSONL → empty messages + warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_empty_jsonl(
    tmp_sessions_root: Path,
    context_storage,
    mock_llm,
    compact_ctx: MaybeCompactContext,
) -> None:
    sid = "sid-empty"
    jsonl = tmp_sessions_root / f"{sid}.jsonl"
    tmp_sessions_root.mkdir(parents=True, exist_ok=True)
    jsonl.write_text("", encoding="utf-8")

    result = await load_session(
        sid,
        tmp_sessions_root,
        context_storage=context_storage,
        llm=mock_llm,
        compact_ctx=compact_ctx,
        time_gap_threshold_hours=8,
    )
    assert result.messages == []
    assert result.applied_compact is False
    assert result.time_gap_inserted is False


# ---------------------------------------------------------------------------
# 8. missing file → warning, empty messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_missing_file(
    tmp_sessions_root: Path,
    context_storage,
    mock_llm,
    compact_ctx: MaybeCompactContext,
) -> None:
    result = await load_session(
        "sid-nonexistent",
        tmp_sessions_root,
        context_storage=context_storage,
        llm=mock_llm,
        compact_ctx=compact_ctx,
        time_gap_threshold_hours=8,
    )
    assert result.messages == []
    assert any("not found" in w for w in result.warnings)