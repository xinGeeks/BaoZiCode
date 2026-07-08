"""sessions 包的核心数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from baozicode.llm.base import Message


@dataclass
class SessionMeta:
    """`list_sessions` 扫盘后返回的元数据(无独立 metadata 文件,从 JSONL 推算)。"""

    id: str
    title: str
    created_at: datetime
    last_message_at: datetime
    message_count: int
    size_bytes: int
    path: Path


@dataclass
class SessionEntry:
    """JSONL 一行 = 一个 Message 的序列化结果,加时间戳和 tool_call_id。

    实际序列化走 `SessionArchiver._encode_message()`(在 archive.py 内),
    这里仅作为解析后的内存表示。`tool_call_id` 在 `role="tool"` 时
    标识对应的 ToolUseBlock。
    """

    timestamp: datetime
    role: Literal["user", "assistant", "tool"]
    blocks: list[dict] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class ResumeResult:
    """`load_session` 的产物。"""

    messages: list[Message] = field(default_factory=list)
    meta: SessionMeta | None = None
    warnings: list[str] = field(default_factory=list)
    applied_compact: bool = False
    time_gap_inserted: bool = False


__all__ = ["ResumeResult", "SessionEntry", "SessionMeta"]