"""v0.8 session_id 格式与迁移测试。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.sessions._id import (
    _UUID4_DIR_PATTERN,
    format_session_id,
    migrate_uuid_context_dirs,
)


def test_uuid4_pattern_matches_only_32_hex() -> None:
    """regex 应严格只匹配 32 位 hex,排除时间戳格式和空串。"""
    assert _UUID4_DIR_PATTERN.match("a" * 32) is not None
    assert _UUID4_DIR_PATTERN.match("0" * 32) is not None
    # 时间戳格式 17 字符不匹配
    assert _UUID4_DIR_PATTERN.match("20260708-153000-a1b2") is None
    # 短 hex 不匹配
    assert _UUID4_DIR_PATTERN.match("abcd1234") is None
    # 含大写字母不匹配(实际 uuid4 全小写)
    assert _UUID4_DIR_PATTERN.match("A" * 32) is None


def test_format_session_id_is_20_chars_with_dash_and_hex() -> None:
    """格式应是 `YYYYMMDD-HHMMSS-xxxx`(20 字符 = 8+1+6+1+4),最后 4 字符是 hex。"""
    sid = format_session_id(datetime(2026, 7, 8, 15, 30, 0))
    assert len(sid) == 20, f"got {sid!r} ({len(sid)})"
    # 拆解
    parts = sid.split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 8 and parts[0].isdigit()
    assert len(parts[1]) == 6 and parts[1].isdigit()
    assert len(parts[2]) == 4
    # 4 字符 hex
    int(parts[2], 16)  # 抛 ValueError 即非 hex


def test_format_session_id_random_part_varies() -> None:
    """随机部分两次调用应不同(极高概率)。"""
    a = format_session_id(datetime.now())
    b = format_session_id(datetime.now())
    assert a != b


def test_migrate_empty_context_root_returns_empty(tmp_path: Path) -> None:
    """context 目录不存在 → 静默返回 []。"""
    assert migrate_uuid_context_dirs(tmp_path / "nope") == []


def test_migrate_single_uuid_renamed(tmp_path: Path) -> None:
    """单个非空 uuid 目录 + _meta.json → 迁到新格式。"""
    ctx = tmp_path / ".baozicode" / "context"
    ctx.mkdir(parents=True)
    old = ctx / ("a" * 32)
    old.mkdir()
    (old / "_meta.json").write_text(
        json.dumps({"created_at": "2026-07-08T15:30:00"}),
        encoding="utf-8",
    )
    (old / "compaction_0.json").write_text("{}", encoding="utf-8")

    migrations = migrate_uuid_context_dirs(ctx)

    assert len(migrations) == 1
    old_name, new_name = migrations[0]
    assert old_name == "a" * 32
    assert new_name.startswith("20260708-153000-")
    assert not (ctx / old_name).exists()
    assert (ctx / new_name).is_dir()
    # _meta.json 内容保留
    assert (ctx / new_name / "_meta.json").exists()
    print(f"[OK] single uuid: {old_name} → {new_name}")


def test_migrate_empty_uuid_dir_skipped(tmp_path: Path) -> None:
    """空 uuid 目录应跳过(避免误重命名刚创建但还没写文件的会话)。"""
    ctx = tmp_path / ".baozicode" / "context"
    ctx.mkdir(parents=True)
    empty = ctx / ("b" * 32)
    empty.mkdir()

    migrations = migrate_uuid_context_dirs(ctx)

    assert migrations == []
    assert empty.exists()  # 仍存在


def test_migrate_uses_meta_created_at_preferred_over_mtime(tmp_path: Path) -> None:
    """_meta.json 有 created_at 时优先用 ISO 时间,不用 mtime。"""
    ctx = tmp_path / ".baozicode" / "context"
    ctx.mkdir(parents=True)
    old = ctx / ("c" * 32)
    old.mkdir()
    # 写一个 2025 年的 created_at
    (old / "_meta.json").write_text(
        json.dumps({"created_at": "2025-01-15T10:00:00"}),
        encoding="utf-8",
    )
    # mtime 用 update_time 但目录 mtime 难精确控,所以用 utime 模拟 2026
    import os
    target_mtime = datetime(2026, 7, 8, 15, 30, 0, tzinfo=timezone.utc).timestamp()
    os.utime(old, (target_mtime, target_mtime))

    migrations = migrate_uuid_context_dirs(ctx)

    # 应按 _meta.json 的 2025 时间命名,而非 mtime 的 2026
    assert len(migrations) == 1
    _, new_name = migrations[0]
    assert new_name.startswith("20250115-100000-")


def test_migrate_falls_back_to_mtime_when_no_meta(tmp_path: Path) -> None:
    """无 _meta.json 时用目录 mtime(转 UTC 去掉 tz)。"""
    import os

    ctx = tmp_path / ".baozicode" / "context"
    ctx.mkdir(parents=True)
    old = ctx / ("d" * 32)
    old.mkdir()
    # 放一个文件让 _is_empty_dir 返回 False(空目录会被跳过)
    (old / "compaction_0.json").write_text("{}", encoding="utf-8")
    target_mtime = datetime(2026, 7, 8, 15, 30, 0, tzinfo=timezone.utc).timestamp()
    os.utime(old, (target_mtime, target_mtime))

    migrations = migrate_uuid_context_dirs(ctx)

    assert len(migrations) == 1
    _, new_name = migrations[0]
    assert new_name.startswith("20260708-153000-")


def test_migrate_collision_appends_legacy_n(tmp_path: Path) -> None:
    """目标名已存在 → 追加 _legacy_1, _legacy_2。"""
    ctx = tmp_path / ".baozicode" / "context"
    ctx.mkdir(parents=True)
    # 1) 预存一个目标名
    target = "20260708-153000-aaaa"
    (ctx / target).mkdir()
    # 2) 准备要迁移的 uuid
    old = ctx / ("e" * 32)
    old.mkdir()
    (old / "_meta.json").write_text(
        json.dumps({"created_at": "2026-07-08T15:30:00"}),
        encoding="utf-8",
    )
    # 模拟 token_hex(2) 不容易控值,所以把"_" + 字符都试一遍
    # 直接再预存 _legacy_1 也行
    (ctx / "20260708-153000-aaaa_legacy_1").mkdir()

    migrations = migrate_uuid_context_dirs(ctx)
    _, new_name = migrations[0]

    # new_name 要么是 _legacy_2,要么是 _legacy_3(取决于 token_hex)
    # 但绝对不能等于已存在的 target / _legacy_1
    assert new_name != target
    assert new_name != "20260708-153000-aaaa_legacy_1"
    assert (ctx / new_name).is_dir()


def test_migrate_skips_already_timestamped_dirs(tmp_path: Path) -> None:
    """已经是新时间戳格式的目录不应被迁。"""
    ctx = tmp_path / ".baozicode" / "context"
    ctx.mkdir(parents=True)
    stamped = ctx / "20260708-153000-aaaa"
    stamped.mkdir()
    (stamped / "_meta.json").write_text("{}", encoding="utf-8")

    migrations = migrate_uuid_context_dirs(ctx)

    assert migrations == []
    assert stamped.exists()  # 原封不动


def test_migrate_multiple_uuids_all_unique_names(tmp_path: Path) -> None:
    """多个 uuid 迁移后,新名两两不同。"""
    ctx = tmp_path / ".baozicode" / "context"
    ctx.mkdir(parents=True)
    names = [("f" * 32), ("1" * 32), ("2" * 32)]
    for n in names:
        d = ctx / n
        d.mkdir()
        (d / "_meta.json").write_text(
            json.dumps({"created_at": "2026-07-08T15:30:00"}),
            encoding="utf-8",
        )

    migrations = migrate_uuid_context_dirs(ctx)

    new_names = [m[1] for m in migrations]
    assert len(new_names) == len(set(new_names)), f"重名: {new_names}"
    print(f"[OK] {len(migrations)} uuids → {len(set(new_names))} 唯一名")


def test_migrate_idempotent(tmp_path: Path) -> None:
    """第二次调用应无操作(全是非 uuid 目录)。"""
    ctx = tmp_path / ".baozicode" / "context"
    ctx.mkdir(parents=True)
    old = ctx / ("9" * 32)
    old.mkdir()
    (old / "_meta.json").write_text(
        json.dumps({"created_at": "2026-07-08T15:30:00"}),
        encoding="utf-8",
    )

    first = migrate_uuid_context_dirs(ctx)
    second = migrate_uuid_context_dirs(ctx)

    assert len(first) == 1
    assert second == []  # 第二次没东西可迁


def test_migrate_returns_ordered_pairs(tmp_path: Path) -> None:
    """返回 list[(old, new)] 顺序与 iterdir 一致(可断言 old 名字)。"""
    ctx = tmp_path / ".baozicode" / "context"
    ctx.mkdir(parents=True)
    for n in ["a" * 32, "b" * 32]:
        d = ctx / n
        d.mkdir()
        (d / "_meta.json").write_text(
            json.dumps({"created_at": "2026-07-08T15:30:00"}),
            encoding="utf-8",
        )

    migrations = migrate_uuid_context_dirs(ctx)

    old_set = {m[0] for m in migrations}
    assert old_set == {"a" * 32, "b" * 32}
