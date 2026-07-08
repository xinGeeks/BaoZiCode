"""v0.8 sessions cleanup + listing tests。"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.sessions.archive import SessionArchiver
from baozicode.sessions.cleanup import cleanup_expired, list_sessions


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _set_mtime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def test_cleanup_expired_removes_old_session(
    tmp_sessions_root: Path, tmp_project_root: Path
) -> None:
    """mtime 31 天的 JSONL + context dir 被清,context dir 不存在时不报错。"""
    ctx_root = tmp_project_root / ".baozicode" / "context"
    old_id = "20260101-120000-aaaa"
    new_id = "20260708-120000-bbbb"

    old_jsonl = tmp_sessions_root / f"{old_id}.jsonl"
    _write_jsonl(old_jsonl, [{"role": "user", "blocks": [{"type": "text", "text": "old"}]}])

    new_jsonl = tmp_sessions_root / f"{new_id}.jsonl"
    _write_jsonl(new_jsonl, [{"role": "user", "blocks": [{"type": "text", "text": "new"}]}])

    # mtime: old = 31 天前, new = 今天
    old_mtime = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
    _set_mtime(old_jsonl, old_mtime)
    _set_mtime(new_jsonl, datetime.now(timezone.utc).timestamp())

    # 给 old 一个 context dir
    old_ctx = ctx_root / old_id
    old_ctx.mkdir(parents=True)
    (old_ctx / "tool_abcdef_1.json").write_text("{}", encoding="utf-8")

    removed = cleanup_expired(
        tmp_sessions_root,
        context_root=ctx_root,
        retention_days=30,
    )
    assert removed == 1
    assert not old_jsonl.exists()
    assert not old_ctx.exists()
    assert new_jsonl.exists()


def test_cleanup_today_preserved(tmp_sessions_root: Path) -> None:
    """今天的会话不会被清。"""
    jsonl = tmp_sessions_root / "today.jsonl"
    _write_jsonl(jsonl, [{"role": "user"}])
    removed = cleanup_expired(
        tmp_sessions_root,
        context_root=tmp_sessions_root.parent / "context",
        retention_days=30,
    )
    assert removed == 0
    assert jsonl.exists()


def test_cleanup_zero_retention_disabled(tmp_sessions_root: Path) -> None:
    """retention_days <= 0 → 直接返回 0,不删任何东西。"""
    jsonl = tmp_sessions_root / "any.jsonl"
    _write_jsonl(jsonl, [{"role": "user"}])
    old_mtime = (datetime.now(timezone.utc) - timedelta(days=999)).timestamp()
    _set_mtime(jsonl, old_mtime)
    removed = cleanup_expired(
        tmp_sessions_root,
        context_root=tmp_sessions_root.parent / "context",
        retention_days=0,
    )
    assert removed == 0
    assert jsonl.exists()


def test_cleanup_does_not_touch_other_context_dirs(
    tmp_sessions_root: Path, tmp_project_root: Path
) -> None:
    """清掉 session A 时,session B 的 context dir 不动。"""
    ctx_root = tmp_project_root / ".baozicode" / "context"
    a_id = "20260101-000000-aaaa"
    b_id = "20260708-000000-bbbb"

    a_jsonl = tmp_sessions_root / f"{a_id}.jsonl"
    _write_jsonl(a_jsonl, [{"role": "user"}])
    old_mtime = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    _set_mtime(a_jsonl, old_mtime)

    a_ctx = ctx_root / a_id
    a_ctx.mkdir(parents=True)
    (a_ctx / "f.json").write_text("{}", encoding="utf-8")
    b_ctx = ctx_root / b_id
    b_ctx.mkdir(parents=True)
    (b_ctx / "g.json").write_text("{}", encoding="utf-8")

    cleanup_expired(
        tmp_sessions_root, context_root=ctx_root, retention_days=30
    )
    assert not a_ctx.exists()
    assert b_ctx.exists()
    assert (b_ctx / "g.json").exists()


def test_cleanup_idempotent_when_run_twice(tmp_sessions_root: Path) -> None:
    jsonl = tmp_sessions_root / "old.jsonl"
    _write_jsonl(jsonl, [{"role": "user"}])
    old_mtime = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
    _set_mtime(jsonl, old_mtime)

    cleanup_expired(
        tmp_sessions_root,
        context_root=tmp_sessions_root.parent / "context",
        retention_days=30,
    )
    assert not jsonl.exists()
    # 第二次跑(已经没有该文件)
    removed = cleanup_expired(
        tmp_sessions_root,
        context_root=tmp_sessions_root.parent / "context",
        retention_days=30,
    )
    assert removed == 0


def test_list_sessions_returns_sorted_by_mtime_desc(
    tmp_sessions_root: Path,
) -> None:
    """3 个 session,mtime 不同 → 排序按 mtime 倒序。"""
    ids = ["sid-a", "sid-b", "sid-c"]
    titles = ["First session", "Second session", "Third session"]
    base_time = datetime.now(timezone.utc).timestamp()
    for sid, title, offset in [
        (ids[0], titles[0], 100),
        (ids[1], titles[1], 200),
        (ids[2], titles[2], 300),
    ]:
        path = tmp_sessions_root / f"{sid}.jsonl"
        _write_jsonl(
            path,
            [{"role": "user", "blocks": [{"type": "text", "text": title}]}],
        )
        _set_mtime(path, base_time + offset)

    sessions = list_sessions(tmp_sessions_root)
    assert [s.id for s in sessions] == ["sid-c", "sid-b", "sid-a"]
    assert sessions[0].title == "Third session"
    assert sessions[0].message_count == 1


def test_list_sessions_empty_directory(tmp_sessions_root: Path) -> None:
    """空目录 → 空 list,不报错。"""
    assert list_sessions(tmp_sessions_root) == []


def test_list_sessions_no_dir_returns_empty(tmp_path: Path) -> None:
    """sessions_root 不存在 → 空 list,不报错。"""
    assert list_sessions(tmp_path / "nope") == []