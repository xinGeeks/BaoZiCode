"""session_id 生成 — `YYYYMMDD-HHMMSS-xxxx`(17 字符)。

按 openspec/changes/v0-8-memory-and-sessions/specs/context-management/spec.md
"Session ID format change" 段:
- 15 字符时间戳 + 1 个横杠 + 4 字符随机 hex
- 随机部分来自 `secrets.token_hex(2)`,保证同一秒内多次调用互不冲突
- 人类可读(从 ID 即可读出大致日期)

同时提供 v0.7 uuid → v0.8 时间戳格式的目录迁移工具。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


_UUID4_DIR_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def format_session_id(dt: datetime) -> str:
    """返回 `YYYYMMDD-HHMMSS-xxxx`(17 字符)。"""
    return f"{dt.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def _read_meta_created_at(meta_path: Path) -> datetime | None:
    """读 `_meta.json` 的 `created_at`(ISO 8601),失败返回 None。"""
    if not meta_path.is_file():
        return None
    try:
        raw = meta_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    ts = data.get("created_at")
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _resolve_unique_target(context_root: Path, base: str) -> str:
    """base 已存在 → 追加 `_legacy_<n>`(n=1,2,...)直到唯一。"""
    candidate = base
    n = 1
    while (context_root / candidate).exists():
        candidate = f"{base}_legacy_{n}"
        n += 1
    return candidate


def _is_empty_dir(path: Path) -> bool:
    """目录存在但没有任何文件 / 子目录。"""
    if not path.is_dir():
        return False
    try:
        return not any(path.iterdir())
    except OSError:
        return False


def migrate_uuid_context_dirs(context_root: Path) -> list[tuple[str, str]]:
    """扫描 `.baozicode/context/` 下 v0.7 uuid4 命名的目录,迁到新时间戳格式。

    - 跳过空目录(避免误重命名刚创建但还没写文件的会话)
    - `_meta.json` 优先(其 `created_at`),fallback 到目录 `st_mtime`
    - 冲突目标名追加 `_legacy_<n>`
    - 每个迁移 INFO log:`<old> → <new>`

    返回 `[(old_name, new_name), ...]` 列表(便于测试断言)。
    """
    if not context_root.is_dir():
        return []
    migrations: list[tuple[str, str]] = []
    for child in context_root.iterdir():
        if not child.is_dir():
            continue
        if not _UUID4_DIR_PATTERN.match(child.name):
            continue
        if _is_empty_dir(child):
            continue
        # 选时间来源
        dt = _read_meta_created_at(child / "_meta.json")
        if dt is None:
            mtime = child.stat().st_mtime
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        # 去掉 tz(避免日期因 tzoff 而跳到前一天)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        base = format_session_id(dt)
        new_name = _resolve_unique_target(context_root, base)
        new_path = context_root / new_name
        try:
            child.rename(new_path)
        except OSError as exc:
            log.warning(
                "session_id migration: rename %s → %s failed: %s",
                child.name, new_name, exc,
            )
            continue
        log.info(
            "session_id migration: %s → %s", child.name, new_name
        )
        migrations.append((child.name, new_name))
    return migrations


__all__ = ["format_session_id", "migrate_uuid_context_dirs"]