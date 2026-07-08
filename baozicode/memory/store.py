"""memory store — CRUD + 索引管理。

按 openspec/changes/v0-8-memory-and-sessions/specs/auto-memory/spec.md:
- 一条笔记 = 一个 `<slug>.md`,YAML frontmatter + Markdown body
- 索引文件 `MEMORY.md` 记录所有笔记的标题 + one_liner
- 两个 store(user / project)物理隔离

写盘用 `flush() + os.fsync()` 保原子;超限写入抛 `IndexOverflowError`。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

from baozicode.memory.schema import (
    IndexEntry,
    IndexOverflowError,
    MemoryIndex,
    Note,
    NoteType,
)

log = logging.getLogger(__name__)


_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SLUG_MAX_LEN = 60
_INDEX_HEADER_USER = "# Memory Index (user)\n\n_Empty — no notes yet._\n"
_INDEX_HEADER_PROJECT = "# Memory Index (project)\n\n_Empty — no notes yet._\n"
_INDEX_HEADING_PATTERN = re.compile(
    r"^##\s+\[(?P<type>[a-z\-]+)\]\s+(?P<slug>[a-z0-9][a-z0-9\-]*)"
    r"\s+—\s+(?P<title>.+?)\s*$"
)
_ONE_LINER_MAX = 80


def _validate_slug(slug: str) -> None:
    if not isinstance(slug, str) or not slug:
        raise ValueError(f"slug 必填且非空: {slug!r}")
    if len(slug) > _SLUG_MAX_LEN:
        raise ValueError(
            f"slug 长度 {len(slug)} > 上限 {_SLUG_MAX_LEN}: {slug!r}"
        )
    if not _SLUG_PATTERN.match(slug):
        raise ValueError(
            f"slug 格式不合法(必须 ^[a-z0-9][a-z0-9-]*$): {slug!r}"
        )


def _coerce_note_type(value: object) -> NoteType:
    if isinstance(value, NoteType):
        return value
    if isinstance(value, str):
        try:
            return NoteType(value)
        except ValueError as exc:
            raise ValueError(f"invalid note type: {value!r}") from exc
    raise ValueError(f"invalid note type: {value!r}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """单层 memory 目录的 CRUD + 索引读写。"""

    def __init__(self, root: Path, scope: Literal["user", "project"]) -> None:
        if scope not in ("user", "project"):
            raise ValueError(f"scope 必须是 'user' 或 'project': {scope!r}")
        self.root = Path(root)
        self.scope: Literal["user", "project"] = scope
        self._ensure_root()

    # ---- 内部辅助 ----

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        index_path = self.root / "MEMORY.md"
        if not index_path.exists():
            header = (
                _INDEX_HEADER_USER
                if self.scope == "user"
                else _INDEX_HEADER_PROJECT
            )
            self._atomic_write(index_path, header)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """写文件:flush + fsync 保原子。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

    def _note_path(self, slug: str) -> Path:
        _validate_slug(slug)
        return self.root / f"{slug}.md"

    # ---- 读 ----

    def read_index(self) -> MemoryIndex:
        """解析 `MEMORY.md` → MemoryIndex(同时算物理指标)。"""
        index_path = self.root / "MEMORY.md"
        if not index_path.exists():
            return MemoryIndex(entries=[], total_lines=0, total_bytes=0)

        raw = index_path.read_text(encoding="utf-8")
        total_bytes = len(raw.encode("utf-8"))
        total_lines = raw.count("\n") + (1 if raw and not raw.endswith("\n") else 0)

        entries: list[IndexEntry] = []
        current: IndexEntry | None = None
        for line in raw.splitlines():
            m = _INDEX_HEADING_PATTERN.match(line)
            if m:
                try:
                    current = IndexEntry(
                        type=_coerce_note_type(m.group("type")),
                        slug=m.group("slug"),
                        title=m.group("title").strip(),
                        one_liner="",
                    )
                    entries.append(current)
                except ValueError:
                    current = None
                continue
            if current is not None and not current.one_liner and line.strip():
                one = line.strip()
                if len(one) > _ONE_LINER_MAX:
                    one = one[: _ONE_LINER_MAX - 1] + "…"
                current.one_liner = one
        return MemoryIndex(
            entries=entries, total_lines=total_lines, total_bytes=total_bytes
        )

    def read_note(self, slug: str) -> Note | None:
        """读单条笔记 → Note(失败 / 不存在返回 None)。"""
        path = self._note_path(slug)
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        # 解析 frontmatter
        if not raw.startswith("---\n"):
            log.warning("memory: %s 缺 frontmatter 起始标记", path)
            return None
        end = raw.find("\n---\n", 4)
        if end == -1:
            log.warning("memory: %s frontmatter 缺闭合标记", path)
            return None
        try:
            fm = yaml.safe_load(raw[4:end])
        except yaml.YAMLError as exc:
            log.warning("memory: %s frontmatter 解析失败: %s", path, exc)
            return None
        if not isinstance(fm, dict):
            log.warning("memory: %s frontmatter 不是 dict", path)
            return None
        # 校验 type 必填
        if "type" not in fm:
            log.warning("memory: %s frontmatter 缺 type 字段", path)
            return None
        try:
            note_type = _coerce_note_type(fm["type"])
        except ValueError as exc:
            log.warning("memory: %s invalid type: %s", path, exc)
            return None
        # body 在第二个 --- 之后
        body_start = end + len("\n---\n")
        body = raw[body_start:].strip()
        # title 从第一行 H1 提取(若有);fallback = slug
        title = slug
        first_line = body.split("\n", 1)[0].strip() if body else ""
        if first_line.startswith("# "):
            title = first_line[2:].strip()
        # 时间字段(可缺)
        created_at = fm.get("created_at") or _now_iso()
        last_accessed = fm.get("last_accessed") or created_at
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        if isinstance(last_accessed, datetime):
            last_accessed = last_accessed.isoformat()
        return Note(
            type=note_type,
            slug=slug,
            title=title,
            content=body,
            created_at=created_at if isinstance(created_at, str) else _now_iso(),
            source_session=str(fm.get("source_session", "")),
            tags=list(fm.get("tags", []) or []),
            access_count=int(fm.get("access_count", 0) or 0),
            last_accessed=last_accessed if isinstance(last_accessed, str) else created_at,
            path=path,
        )

    # ---- 写 ----

    def add_note(self, note: Note) -> Path:
        """写入新笔记文件。type / slug 校验,frontmatter + body,fync。"""
        _validate_slug(note.slug)
        # type 校验(走 _coerce_note_type 抛 ValueError)
        _coerce_note_type(note.type)
        path = self._note_path(note.slug)
        if path.exists():
            raise FileExistsError(
                f"note 已存在,不要重复 add(用 update_note): {path}"
            )
        created = note.created_at or _now_iso()
        if isinstance(created, datetime):
            created = created.isoformat()
        last = note.last_accessed or created
        if isinstance(last, datetime):
            last = last.isoformat()
        tags_csv = ",".join(note.tags) if note.tags else ""
        fm_lines = [
            "---",
            f"type: {note.type.value}",
            f"created_at: {created}",
            f"source_session: {note.source_session}",
            f"tags: [{tags_csv}]",
            f"access_count: {note.access_count}",
            f"last_accessed: {last}",
            "---",
            "",
            f"# {note.title}",
            "",
            note.content,
            "",
        ]
        content = "\n".join(fm_lines)
        self._atomic_write(path, content)
        # Note.path 同步
        note.path = path
        return path

    def update_note(
        self,
        slug: str,
        new_content: str,
        *,
        current_session_id: str | None = None,
        require_same_session: bool = False,
    ) -> None:
        """追加内容到笔记 body,更新 `last_accessed`。

        - 默认 append-only(不覆盖现有内容)
        - `require_same_session=True` 且 `note.source_session != current_session_id` → 拒
        """
        path = self._note_path(slug)
        if not path.exists():
            raise FileNotFoundError(f"note 不存在: {path}")
        note = self.read_note(slug)
        if note is None:
            raise ValueError(f"note 解析失败: {path}")
        if require_same_session and current_session_id is not None:
            if note.source_session and note.source_session != current_session_id:
                raise PermissionError(
                    f"cannot update cross-session note: {slug} "
                    f"(source_session={note.source_session})"
                )
        # 重新构造 frontmatter(updated last_accessed)+ 旧 body + 新内容
        # 旧 body 在 note.content 里(已 strip)
        merged_body = note.content
        if new_content and new_content.strip():
            if merged_body:
                merged_body = merged_body + "\n\n" + new_content.strip()
            else:
                merged_body = new_content.strip()
        new_last = _now_iso()
        tags_csv = ",".join(note.tags) if note.tags else ""
        fm_lines = [
            "---",
            f"type: {note.type.value}",
            f"created_at: {note.created_at}",
            f"source_session: {note.source_session}",
            f"tags: [{tags_csv}]",
            f"access_count: {note.access_count}",
            f"last_accessed: {new_last}",
            "---",
            "",
            f"# {note.title}",
            "",
            merged_body,
            "",
        ]
        self._atomic_write(path, "\n".join(fm_lines))

    def delete_note(
        self,
        slug: str,
        *,
        current_session_id: str | None = None,
        require_same_session: bool = True,
    ) -> bool:
        """unlink 笔记文件,默认要求 source_session 匹配 current_session_id。"""
        path = self._note_path(slug)
        if not path.exists():
            return False
        if require_same_session:
            note = self.read_note(slug)
            if note is None:
                raise ValueError(f"note 解析失败: {path}")
            if (
                current_session_id is not None
                and note.source_session
                and note.source_session != current_session_id
            ):
                raise PermissionError(
                    f"cannot delete cross-session note: {slug} "
                    f"(source_session={note.source_session})"
                )
        path.unlink()
        return True

    def rewrite_index(
        self, entries: list[IndexEntry], *, max_lines: int, max_bytes: int
    ) -> None:
        """重写 `MEMORY.md`,超过 max_lines / max_bytes → 抛 IndexOverflowError。"""
        blocks: list[str] = [
            f"# Memory Index ({self.scope})",
            "",
        ]
        for e in entries:
            blocks.append(f"## [{e.type.value}] {e.slug} — {e.title}")
            blocks.append(e.one_liner)
            blocks.append("")
        while blocks and blocks[-1] == "":
            blocks.pop()
        blocks.append("")  # 文件末尾换行
        content = "\n".join(blocks)
        total_bytes = len(content.encode("utf-8"))
        total_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        if total_lines > max_lines:
            raise IndexOverflowError(
                f"lines {total_lines} > max {max_lines}"
            )
        if total_bytes > max_bytes:
            raise IndexOverflowError(
                f"bytes {total_bytes} > max {max_bytes}"
            )
        self._atomic_write(self.root / "MEMORY.md", content)

    def increment_access(self, slug: str) -> None:
        """bump access_count + 更新 last_accessed(保留其它字段)。"""
        path = self._note_path(slug)
        if not path.exists():
            raise FileNotFoundError(f"note 不存在: {path}")
        note = self.read_note(slug)
        if note is None:
            raise ValueError(f"note 解析失败: {path}")
        new_count = note.access_count + 1
        new_last = _now_iso()
        tags_csv = ",".join(note.tags) if note.tags else ""
        fm_lines = [
            "---",
            f"type: {note.type.value}",
            f"created_at: {note.created_at}",
            f"source_session: {note.source_session}",
            f"tags: [{tags_csv}]",
            f"access_count: {new_count}",
            f"last_accessed: {new_last}",
            "---",
            "",
            f"# {note.title}",
            "",
            note.content,
            "",
        ]
        self._atomic_write(path, "\n".join(fm_lines))


__all__ = ["MemoryStore"]