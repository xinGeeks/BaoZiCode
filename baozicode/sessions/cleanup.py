"""sessions cleanup + listing。

按 openspec/changes/v0-8-memory-and-sessions/specs/session-archive/spec.md:
- `cleanup_expired`:启动时扫,删除 mtime > retention_days 的 JSONL + 对应 context dir
- `list_sessions`:扫盘 + 从首行推 title + 按 mtime desc 排序

两个函数都 best-effort:任何 IO 失败 log warning 但继续。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from baozicode.sessions.schema import SessionMeta

log = logging.getLogger(__name__)


_USER_MSG_TITLE_MAX = 60


def _safe_remove(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError as exc:
        log.warning("sessions: failed to remove %s: %s", path, exc)
        return False


def _safe_rmdir(path: Path) -> bool:
    try:
        path.rmdir()
        return True
    except OSError as exc:
        log.warning("sessions: failed to rmdir %s: %s", path, exc)
        return False


def _remove_dir_recursive(path: Path) -> int:
    """递归删除目录(用于清空 session 关联的 .baozicode/context/<sid>/)返回文件数。"""
    if not path.is_dir():
        return 0
    removed = 0
    for f in path.iterdir():
        if f.is_file():
            if _safe_remove(f):
                removed += 1
        elif f.is_dir():
            removed += _remove_dir_recursive(f)
    if _safe_rmdir(path):
        return removed
    return removed


def cleanup_expired(
    sessions_root: Path,
    *,
    context_root: Path,
    retention_days: int,
) -> int:
    """删除 mtime > retention_days 的 JSONL 文件 + 关联 context dir。

    - `retention_days <= 0` 视为 disabled,直接返回 0
    - 今天内的 mtime 跳过(defensive guard — 防止 mtime 漂移把今天会话误删)
    - 每个 session 独立 try/except,失败 log warning 不中断
    """
    if retention_days <= 0:
        return 0
    if not sessions_root.is_dir():
        return 0
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - retention_days * 86400
    removed = 0
    for jsonl in sessions_root.glob("*.jsonl"):
        if not jsonl.is_file():
            continue
        try:
            mtime = jsonl.stat().st_mtime
        except OSError as exc:
            log.warning("sessions: stat failed for %s: %s", jsonl, exc)
            continue
        if mtime >= cutoff:
            # 在窗口内 — 跳过(包括今天)
            continue
        # 删 JSONL
        if _safe_remove(jsonl):
            removed += 1
        # 删关联 context dir
        session_id = jsonl.stem
        ctx_dir = context_root / session_id
        if ctx_dir.is_dir():
            _remove_dir_recursive(ctx_dir)
    return removed


def _extract_title(jsonl_path: Path) -> str:
    """从首行提取首个 user message 的 text 作为 title,截断 60 字符。

    文件为空 / 首行不是 user / 解析失败 → 返回空串。
    """
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            first_line = f.readline()
    except OSError:
        return ""
    if not first_line.strip():
        return ""
    try:
        data = json.loads(first_line)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    if data.get("role") != "user":
        return ""
    blocks = data.get("blocks") or []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            text = str(b.get("text", "")).strip()
            if text:
                if len(text) > _USER_MSG_TITLE_MAX:
                    return text[: _USER_MSG_TITLE_MAX - 1] + "…"
                return text
    return ""


def list_sessions(sessions_root: Path) -> list[SessionMeta]:
    """扫 sessions_root 下的 *.jsonl,返回 SessionMeta 列表(mtime desc)。

    物理文件读失败 / 解析失败 → 跳过该文件(不抛)。
    """
    if not sessions_root.is_dir():
        return []
    results: list[SessionMeta] = []
    for jsonl in sessions_root.glob("*.jsonl"):
        if not jsonl.is_file():
            continue
        try:
            stat = jsonl.stat()
        except OSError as exc:
            log.warning("sessions: stat failed for %s: %s", jsonl, exc)
            continue
        # 算 message_count:读行数(不解析,避免大文件慢)
        try:
            line_count = 0
            with open(jsonl, "r", encoding="utf-8") as f:
                for _line in f:
                    if _line.strip():
                        line_count += 1
        except OSError as exc:
            log.warning("sessions: read failed for %s: %s", jsonl, exc)
            continue
        mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        created_dt = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        results.append(
            SessionMeta(
                id=jsonl.stem,
                title=_extract_title(jsonl),
                created_at=created_dt,
                last_message_at=mtime_dt,
                message_count=line_count,
                size_bytes=stat.st_size,
                path=jsonl,
            )
        )
    results.sort(key=lambda m: m.last_message_at, reverse=True)
    return results


__all__ = ["cleanup_expired", "list_sessions"]