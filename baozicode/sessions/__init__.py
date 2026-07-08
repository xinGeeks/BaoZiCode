"""sessions 包 — JSONL 存档 + resume + cleanup + listing。

按 openspec/changes/v0-8-memory-and-sessions/specs/session-archive/spec.md:
- `bootstrap(project_root, config, session_id) -> (SessionArchiver, list[SessionMeta])`
  - 启动时跑 `cleanup_expired` 清过期
  - 构造当前 session 的 SessionArchiver(用于 ConversationManager)
  - 返回已有 sessions 列表(给 /resume 用)
- `enabled=False` 时返回 (None, [])

公开 API:
- `SessionArchiver`, `SessionMeta`, `SessionEntry`, `ResumeResult`
- `load_session`, `cleanup_expired`, `list_sessions`
- `bootstrap`
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from baozicode.sessions._id import format_session_id, migrate_uuid_context_dirs
from baozicode.sessions.archive import SessionArchiver
from baozicode.sessions.cleanup import cleanup_expired, list_sessions
from baozicode.sessions.resume import load_session
from baozicode.sessions.schema import ResumeResult, SessionEntry, SessionMeta

if TYPE_CHECKING:
    from baozicode.config.schema import AppConfig


def bootstrap(
    project_root: Path,
    config: AppConfig,
    session_id: str,
) -> tuple[SessionArchiver | None, list[SessionMeta]]:
    """启动入口。

    - `config.sessions.enabled = False` → 返回 `(None, [])`,不创建目录
    - 否则:
      1. 跑 `cleanup_expired`(删除 retention_days 之前的 JSONL + context dir)
      2. 构造当前 session 的 SessionArchiver
      3. 返回 (archiver, list_sessions(sessions_root))
    """
    if not config.sessions.enabled:
        return None, []
    sessions_root = Path(config.sessions.dir)
    if not sessions_root.is_absolute():
        sessions_root = project_root / sessions_root
    context_root = project_root / ".baozicode" / "context"
    # 1. cleanup
    cleanup_expired(
        sessions_root,
        context_root=context_root,
        retention_days=config.sessions.retention_days,
    )
    # 2. archiver
    archiver = SessionArchiver(sessions_root, session_id=session_id)
    # 3. list
    sessions_meta = list_sessions(sessions_root)
    return archiver, sessions_meta


__all__ = [
    "ResumeResult",
    "SessionArchiver",
    "SessionEntry",
    "SessionMeta",
    "bootstrap",
    "cleanup_expired",
    "format_session_id",
    "list_sessions",
    "load_session",
    "migrate_uuid_context_dirs",
]