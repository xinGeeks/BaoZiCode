"""v0.8 memory store tests — CRUD、frontmatter、索引、隔离。"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.memory import (
    IndexEntry,
    IndexOverflowError,
    MemoryIndex,
    Note,
    NoteType,
)


# ---------------------------------------------------------------------------
# 1. 初始化 — 空 MEMORY.md 自动创建
# ---------------------------------------------------------------------------


def test_bootstrap_creates_empty_memory_md(user_store, project_store) -> None:
    for store in (user_store, project_store):
        idx_path = store.root / "MEMORY.md"
        assert idx_path.is_file()
        text = idx_path.read_text(encoding="utf-8")
        assert "Memory Index" in text
        assert "no notes yet" in text.lower() or "empty" in text.lower()


def test_read_index_on_empty_store_returns_empty(user_store) -> None:
    idx = user_store.read_index()
    assert idx.entries == []
    assert idx.total_bytes > 0  # header 占字节
    assert idx.total_lines > 0


# ---------------------------------------------------------------------------
# 2. add_note — frontmatter 写入 + slug 校验
# ---------------------------------------------------------------------------


def test_add_note_creates_valid_file_with_frontmatter(
    user_store, sample_note
) -> None:
    path = user_store.add_note(sample_note)
    assert path.exists()
    raw = path.read_text(encoding="utf-8")
    # frontmatter
    assert raw.startswith("---\n")
    assert "type: user-pref" in raw
    assert "source_session: 20260708-100000-a1b2" in raw
    assert "tags: [ui,preference]" in raw
    assert "access_count: 0" in raw
    # body
    assert "# User dislikes emoji" in raw
    assert "用户不喜欢 emoji" in raw


def test_add_note_validates_slug_rejects_uppercase(user_store, sample_note) -> None:
    sample_note.slug = "NoEmoji"
    with pytest.raises(ValueError, match="slug 格式不合法"):
        user_store.add_note(sample_note)


def test_add_note_validates_slug_rejects_leading_dash(user_store, sample_note) -> None:
    sample_note.slug = "-leading"
    with pytest.raises(ValueError, match="slug 格式不合法"):
        user_store.add_note(sample_note)


def test_add_note_validates_slug_rejects_too_long(user_store, sample_note) -> None:
    sample_note.slug = "a" * 61
    with pytest.raises(ValueError, match="长度"):
        user_store.add_note(sample_note)


def test_add_note_duplicate_raises_file_exists(user_store, sample_note) -> None:
    user_store.add_note(sample_note)
    with pytest.raises(FileExistsError):
        user_store.add_note(sample_note)


# ---------------------------------------------------------------------------
# 3. read_note — round-trip
# ---------------------------------------------------------------------------


def test_read_note_roundtrip_preserves_fields(user_store, sample_note) -> None:
    user_store.add_note(sample_note)
    note = user_store.read_note("no-emoji")
    assert note is not None
    assert note.slug == "no-emoji"
    assert note.type == NoteType.USER_PREF
    assert note.title == "User dislikes emoji"
    assert "用户不喜欢 emoji" in note.content
    assert note.source_session == "20260708-100000-a1b2"
    assert note.tags == ["ui", "preference"]
    assert note.access_count == 0


# ---------------------------------------------------------------------------
# 4. update_note — append + last_accessed 更新
# ---------------------------------------------------------------------------


def test_update_note_appends_without_losing_existing(user_store, sample_note) -> None:
    user_store.add_note(sample_note)
    user_store.update_note("no-emoji", "补充:连 Markdown 装饰 emoji 也不要。")
    note = user_store.read_note("no-emoji")
    assert note is not None
    assert "用户不喜欢 emoji" in note.content  # 旧内容保留
    assert "补充:连 Markdown 装饰 emoji 也不要" in note.content


def test_update_note_cross_session_rejected(user_store, sample_note) -> None:
    user_store.add_note(sample_note)
    with pytest.raises(PermissionError, match="cross-session"):
        user_store.update_note(
            "no-emoji",
            "x",
            current_session_id="20990101-000000-ffff",
            require_same_session=True,
        )


# ---------------------------------------------------------------------------
# 5. delete_note — source_session 校验
# ---------------------------------------------------------------------------


def test_delete_note_own_session_removes_file(user_store, sample_note) -> None:
    user_store.add_note(sample_note)
    result = user_store.delete_note(
        "no-emoji", current_session_id="20260708-100000-a1b2"
    )
    assert result is True
    assert not (user_store.root / "no-emoji.md").exists()


def test_delete_note_cross_session_rejected(user_store, sample_note) -> None:
    user_store.add_note(sample_note)
    with pytest.raises(PermissionError, match="cross-session"):
        user_store.delete_note(
            "no-emoji", current_session_id="20990101-000000-ffff"
        )
    # 文件仍在
    assert (user_store.root / "no-emoji.md").exists()


def test_delete_note_missing_returns_false(user_store) -> None:
    result = user_store.delete_note("ghost", current_session_id="x")
    assert result is False


# ---------------------------------------------------------------------------
# 6. rewrite_index — 超限抛 IndexOverflowError
# ---------------------------------------------------------------------------


def test_rewrite_index_under_limit_writes(user_store) -> None:
    entries = [
        IndexEntry(
            slug=f"note-{i}",
            type=NoteType.PROJECT,
            title=f"Note {i}",
            one_liner=f"line {i}",
        )
        for i in range(3)
    ]
    user_store.rewrite_index(entries, max_lines=200, max_bytes=25_600)
    idx = user_store.read_index()
    assert len(idx.entries) == 3


def test_rewrite_index_over_lines_limit_refused(user_store) -> None:
    entries = [
        IndexEntry(
            slug=f"n-{i}",
            type=NoteType.PROJECT,
            title=f"t{i}",
            one_liner=f"line {i}",
        )
        for i in range(10)
    ]
    with pytest.raises(IndexOverflowError, match="lines"):
        user_store.rewrite_index(entries, max_lines=5, max_bytes=25_600)
    # 现有 MEMORY.md 不变
    assert "Memory Index" in (user_store.root / "MEMORY.md").read_text(encoding="utf-8")


def test_rewrite_index_over_bytes_limit_refused(user_store) -> None:
    # one_liner 拼长字符串造大体积
    entries = [
        IndexEntry(
            slug=f"n-{i}",
            type=NoteType.PROJECT,
            title=f"t{i}",
            one_liner="x" * 500,
        )
        for i in range(3)
    ]
    with pytest.raises(IndexOverflowError, match="bytes"):
        user_store.rewrite_index(entries, max_lines=200, max_bytes=100)


# ---------------------------------------------------------------------------
# 7. increment_access — bump count
# ---------------------------------------------------------------------------


def test_increment_access_bumps_count(user_store, sample_note) -> None:
    user_store.add_note(sample_note)
    user_store.increment_access("no-emoji")
    user_store.increment_access("no-emoji")
    note = user_store.read_note("no-emoji")
    assert note is not None
    assert note.access_count == 2


# ---------------------------------------------------------------------------
# 8. user / project 物理隔离
# ---------------------------------------------------------------------------


def test_user_and_project_stores_physically_isolated(
    user_store, project_store, sample_note
) -> None:
    """写入 user 的笔记不应出现在 project 的文件 / 索引中(反之亦然)。

    index 隔离需先 rewrite_index(把当前 note 文件同步进 MEMORY.md),
    因为 add_note 默认不触发索引重建——索引由 MemoryUpdater 在 apply
    operations 之后统一刷新。
    """
    user_note = Note(
        type=NoteType.USER_PREF,
        slug="no-emoji",
        title="User pref",
        content="user only",
        created_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
        source_session="20260708-100000-aaaa",
    )
    project_note = Note(
        type=NoteType.PROJECT,
        slug="uses-uv",
        title="Project uses uv",
        content="project only",
        created_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
        source_session="20260708-100000-bbbb",
    )
    user_store.add_note(user_note)
    project_store.add_note(project_note)

    # 物理文件互不影响
    assert (user_store.root / "no-emoji.md").exists()
    assert not (project_store.root / "no-emoji.md").exists()
    assert (project_store.root / "uses-uv.md").exists()
    assert not (user_store.root / "uses-uv.md").exists()

    # 索引隔离需先 rewrite_index(add_note 不自动更新索引)
    user_entries = [
        IndexEntry(
            slug="no-emoji",
            type=NoteType.USER_PREF,
            title="User pref",
            one_liner="user only",
        )
    ]
    project_entries = [
        IndexEntry(
            slug="uses-uv",
            type=NoteType.PROJECT,
            title="Project uses uv",
            one_liner="project only",
        )
    ]
    user_store.rewrite_index(user_entries, max_lines=200, max_bytes=25_600)
    project_store.rewrite_index(project_entries, max_lines=200, max_bytes=25_600)

    user_idx = user_store.read_index()
    project_idx = project_store.read_index()

    assert any(e.slug == "no-emoji" for e in user_idx.entries)
    assert not any(e.slug == "uses-uv" for e in user_idx.entries)
    assert any(e.slug == "uses-uv" for e in project_idx.entries)
    assert not any(e.slug == "no-emoji" for e in project_idx.entries)


# ---------------------------------------------------------------------------
# 9. MemoryIndex.format_for_prompt
# ---------------------------------------------------------------------------


def test_memory_index_format_for_prompt_with_entries() -> None:
    idx = MemoryIndex(
        entries=[
            IndexEntry(
                slug="no-emoji",
                type=NoteType.USER_PREF,
                title="User dislikes emoji",
                one_liner="user wants no emoji",
            ),
            IndexEntry(
                slug="uses-uv",
                type=NoteType.PROJECT,
                title="Project uses uv",
                one_liner="not poetry",
            ),
        ],
    )
    text = idx.format_for_prompt()
    assert "## [user-pref] no-emoji — User dislikes emoji" in text
    assert "user wants no emoji" in text
    assert "## [project] uses-uv — Project uses uv" in text
    assert "not poetry" in text


def test_memory_index_format_for_prompt_empty() -> None:
    idx = MemoryIndex()
    assert idx.format_for_prompt() == ""