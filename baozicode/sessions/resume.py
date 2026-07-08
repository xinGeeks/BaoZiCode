"""`load_session` — 从 JSONL 重建 messages,带异常处理四件套。

按 openspec/changes/v0-8-memory-and-sessions/specs/session-archive/spec.md:
1. 坏行跳过(JSONDecodeError)
2. orphan tool_result 截断(seen_tool_use_ids 集合校验)
3. token 超预算 → 走 `maybe_compact(messages, trigger="resume", ctx=compact_ctx)`
4. time_gap > 阈值 → 在 `messages[-2]` 插 `<system-reminder type="time_gap">`
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from baozicode.context import MaybeCompactContext, maybe_compact
from baozicode.context.storage import ContextStorage
from baozicode.llm.base import (
    LLMClient,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from baozicode.sessions.cleanup import list_sessions
from baozicode.sessions.schema import ResumeResult, SessionMeta

log = logging.getLogger(__name__)


_RESERVE_TOKENS_FOR_RESUME = 8_000
_TIME_GAP_TTL = "once"


# ---------------------------------------------------------------------------
# 内部:从 SessionEntry dict 反推 Message
# ---------------------------------------------------------------------------


def _blocks_to_message_blocks(blocks_raw: list[dict]) -> list:
    """dict 列表 → ContentBlock 列表(跳过未知 type)。"""
    out: list = []
    for b in blocks_raw:
        if not isinstance(b, dict):
            continue
        btype = b.get("type")
        if btype == "text":
            out.append(TextBlock(text=str(b.get("text", ""))))
        elif btype == "tool_use":
            out.append(
                ToolUseBlock(
                    id=str(b.get("id", "")),
                    name=str(b.get("name", "")),
                    input=dict(b.get("input", {}) or {}),
                )
            )
        elif btype == "tool_result":
            offloaded_to_raw = b.get("offloaded_to")
            from pathlib import Path as _P

            offloaded_to = (
                _P(str(offloaded_to_raw)) if offloaded_to_raw else None
            )
            out.append(
                ToolResultBlock(
                    tool_use_id=str(b.get("tool_use_id", "")),
                    content=str(b.get("content", "")),
                    is_error=bool(b.get("is_error", False)),
                    offloaded_to=offloaded_to,
                    original_size=int(b.get("original_size", 0) or 0),
                )
            )
        # 未知 type 静默跳过(forward-compat)
    return out


def _entry_to_message(entry: dict) -> Message:
    """单个 JSONL 反序列化的 dict → Message(str 快速路径 vs list 路径)。"""
    role = str(entry.get("role", "user"))
    blocks_raw = entry.get("blocks") or []
    if role == "user" and len(blocks_raw) == 1 and blocks_raw[0].get("type") == "text":
        return Message(role="user", content=str(blocks_raw[0].get("text", "")))
    blocks = _blocks_to_message_blocks(blocks_raw)
    return Message(role=role, content=blocks)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# load_session
# ---------------------------------------------------------------------------


async def load_session(
    session_id: str,
    sessions_root: Path,
    *,
    context_storage: ContextStorage,
    llm: LLMClient,
    compact_ctx: MaybeCompactContext,
    time_gap_threshold_hours: int,
) -> ResumeResult:
    """从 `<sessions_root>/<session_id>.jsonl` 恢复 messages。

    四件套异常处理:坏行跳过 / orphan 截断 / token 超限 compact / time_gap 提醒。
    任意一步失败都不抛,只往 `ResumeResult.warnings` 累积 warning。
    """
    result = ResumeResult()
    jsonl_path = sessions_root / f"{session_id}.jsonl"
    if not jsonl_path.is_file():
        result.warnings.append(f"session file not found: {jsonl_path}")
        return result

    # ---- 1. line-by-line parse,跳过坏行 ----
    raw_entries: list[dict] = []
    try:
        text = jsonl_path.read_text(encoding="utf-8")
    except OSError as exc:
        result.warnings.append(f"failed to read {jsonl_path}: {exc}")
        return result
    for idx, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            result.warnings.append(f"skip bad line {idx}: {exc}")
            log.warning("sessions: skip bad line %d in %s: %s", idx, jsonl_path, exc)
            continue
        if not isinstance(entry, dict):
            result.warnings.append(f"skip non-dict line {idx}")
            continue
        raw_entries.append(entry)

    # ---- 2. orphan tool_call 截断 ----
    seen_tool_use_ids: set[str] = set()
    truncated_at: int | None = None
    for i, entry in enumerate(raw_entries):
        blocks = entry.get("blocks") or []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                bid = str(b.get("id", ""))
                if bid:
                    seen_tool_use_ids.add(bid)
        if entry.get("role") == "tool":
            tcid = entry.get("tool_call_id")
            if tcid and str(tcid) not in seen_tool_use_ids:
                truncated_at = i
                dropped = len(raw_entries) - i
                result.warnings.append(
                    f"orphan tool_result id={tcid} dropped, "
                    f"truncated {dropped} lines"
                )
                break
    if truncated_at is not None:
        raw_entries = raw_entries[:truncated_at]

    # ---- 3. 转 Message ----
    messages: list[Message] = []
    for entry in raw_entries:
        try:
            messages.append(_entry_to_message(entry))
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"failed to convert entry to Message: {exc}")
            continue

    # ---- 4. token 超限 → maybe_compact ----
    if messages:
        try:
            from baozicode.context.estimator import estimate_messages_tokens

            budget = (
                compact_ctx.config.context_window_tokens
                - _RESERVE_TOKENS_FOR_RESUME
            )
            current_tokens = estimate_messages_tokens(messages)
            if current_tokens > budget:
                new_messages, compact_result = await maybe_compact(
                    messages,
                    trigger="resume",
                    ctx=compact_ctx,
                )
                if compact_result.triggered:
                    messages = new_messages
                    result.applied_compact = True
                    result.warnings.append(
                        f"resume triggered compaction: "
                        f"{compact_result.tokens_before} → "
                        f"{compact_result.tokens_after} tokens"
                    )
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"maybe_compact failed on resume: {exc}")

    # ---- 5. time_gap 提醒(取首条 user message 的 timestamp) ----
    if messages:
        first_user_ts = _first_user_timestamp(raw_entries)
        if first_user_ts is not None:
            now = datetime.now(timezone.utc)
            gap_hours = (now - first_user_ts).total_seconds() / 3600
            if gap_hours > time_gap_threshold_hours:
                body = _build_time_gap_body(gap_hours, messages)
                reminder = Message(
                    role="user",
                    content=(
                        f'<system-reminder type="time_gap" ttl="{_TIME_GAP_TTL}">\n'
                        f"{body}\n"
                        "</system-reminder>"
                    ),
                )
                # 插在 messages[-2](最后一条 user 之前)
                if len(messages) >= 2:
                    messages.insert(-1, reminder)
                else:
                    messages.append(reminder)
                result.time_gap_inserted = True

    # ---- meta(scan 不重跑 — 直接从 list_sessions 找一条;失败返回 None) ----
    try:
        all_meta = list_sessions(sessions_root)
        for m in all_meta:
            if m.id == session_id:
                result.meta = m
                break
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"failed to build SessionMeta: {exc}")

    result.messages = messages
    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _first_user_timestamp(raw_entries: list[dict]) -> datetime | None:
    """返回第一条 user role entry 的 timestamp。"""
    for entry in raw_entries:
        if entry.get("role") != "user":
            continue
        ts_raw = entry.get("timestamp")
        if not isinstance(ts_raw, str):
            continue
        try:
            return datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
    return None


def _build_time_gap_body(gap_hours: float, messages: list[Message]) -> str:
    """构造 time_gap 提醒正文:列出最近 5 条 user message 标题。"""
    titles: list[str] = []
    for m in messages:
        if m.role != "user":
            continue
        text = ""
        if isinstance(m.content, str):
            text = m.content
        else:
            for b in m.content:
                if isinstance(b, TextBlock):
                    text = b.text
                    break
        if text.strip():
            titles.append(text.strip())
    last_5 = titles[-5:]
    body_lines = [
        f"距上次会话已过 {gap_hours:.1f} 小时,中间可能发生过上下文变化。",
        "请确认以下事项仍然成立:",
        "",
    ]
    for i, t in enumerate(last_5, start=1):
        one = t if len(t) <= 80 else t[:79] + "…"
        body_lines.append(f"{i}. {one}")
    return "\n".join(body_lines)


__all__ = ["load_session"]