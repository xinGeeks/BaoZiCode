"""memory 包的核心数据类型。

按 openspec/changes/v0-8-memory-and-sessions/specs/auto-memory/spec.md:
- 4 类笔记(USER_PREF / CORRECTION / PROJECT / REFERENCE)
- 每条 = 带 YAML frontmatter 的 Markdown 文件 `<slug>.md`
- 索引文件 `MEMORY.md` 由 `MemoryIndex` 解析,200 行 / 25 KB 上限
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class NoteType(str, Enum):
    """笔记四分类。str mixin 让 Pydantic / yaml 能直接序列化。"""

    USER_PREF = "user-pref"
    CORRECTION = "correction"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass
class Note:
    """单条笔记(写入磁盘前的内存表示)。"""

    type: NoteType
    slug: str
    title: str
    content: str
    created_at: datetime
    source_session: str
    tags: list[str] = field(default_factory=list)
    access_count: int = 0
    last_accessed: datetime | None = None
    path: Path | None = None


@dataclass
class IndexEntry:
    """`MEMORY.md` 索引里一行 `## [<type>] <slug> — <title>` 的解析结果。"""

    slug: str
    type: NoteType
    title: str
    one_liner: str


@dataclass
class MemoryIndex:
    """`MEMORY.md` 整文件的解析结果 + 物理指标。"""

    entries: list[IndexEntry] = field(default_factory=list)
    total_lines: int = 0
    total_bytes: int = 0

    def format_for_prompt(self) -> str:
        """渲染成 prompt 友好的 markdown 文本。

        格式(每条):
            ## [<type>] <slug> — <title>
            <one_liner>

        空 entries → 返回空串。
        """
        if not self.entries:
            return ""
        blocks: list[str] = []
        for e in self.entries:
            blocks.append(f"## [{e.type.value}] {e.slug} — {e.title}")
            blocks.append(e.one_liner)
            blocks.append("")  # 空行分隔
        # 去掉末尾多余空行
        while blocks and blocks[-1] == "":
            blocks.pop()
        return "\n".join(blocks)


class IndexOverflowError(RuntimeError):
    """`MEMORY.md` 超过 `index_max_lines` / `index_max_bytes` 时抛。"""


__all__ = [
    "IndexEntry",
    "IndexOverflowError",
    "MemoryIndex",
    "Note",
    "NoteType",
]