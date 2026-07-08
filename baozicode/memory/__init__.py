"""memory 包 — 两层笔记目录 + CRUD + 索引 + 异步更新 + 溢出处理。

按 openspec/changes/v0-8-memory-and-sessions/specs/auto-memory/spec.md:
- user-global:`~/.baozicode/memory/`(跨项目)
- project-local:`<project_root>/.baozicode/memory/`(仅本项目)
- 两层物理隔离,索引互相不可见
- `bootstrap(project_root, config) -> (user_store, project_store)`

公开 API:
- `MemoryStore`, `Note`, `NoteType`, `IndexEntry`, `MemoryIndex`, `IndexOverflowError`
- `MemoryUpdater`, `MemoryOverflowHandler`, `OverflowAction`, `OverflowState`
- `bootstrap(project_root, config) -> tuple[MemoryStore, MemoryStore]`
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from baozicode.memory.overflow import (
    MemoryOverflowHandler,
    OverflowAction,
    OverflowState,
)
from baozicode.memory.schema import (
    IndexEntry,
    IndexOverflowError,
    MemoryIndex,
    Note,
    NoteType,
)
from baozicode.memory.store import MemoryStore
from baozicode.memory.updater import MemoryUpdater

if TYPE_CHECKING:
    from baozicode.config.schema import AppConfig


def bootstrap(
    project_root: Path, config: AppConfig
) -> tuple[MemoryStore, MemoryStore]:
    """构造 user + project 两层 MemoryStore。

    - `config.memory.enabled = False` → 返回两个空 store(指向临时目录),
      实际上调用方应跳过 bootstrap;此处为兜底
    - 默认 user_dir / project_dir 来自 `MemoryConfig`
    """
    if not config.memory.enabled:
        # 兜底:返回指向 /tmp 的两个 store,后续 add/read 都无效但不影响 import
        fallback_root = Path("/tmp") / "baozicode-memory-disabled"
        return (
            MemoryStore(fallback_root, scope="user"),
            MemoryStore(fallback_root, scope="project"),
        )
    user_root = Path(config.memory.user_dir).expanduser()
    project_root_path = Path(config.memory.project_dir)
    if not project_root_path.is_absolute():
        project_root_path = project_root / project_root_path
    return (
        MemoryStore(user_root, scope="user"),
        MemoryStore(project_root_path, scope="project"),
    )


__all__ = [
    "IndexEntry",
    "IndexOverflowError",
    "MemoryIndex",
    "MemoryOverflowHandler",
    "MemoryStore",
    "MemoryUpdater",
    "Note",
    "NoteType",
    "OverflowAction",
    "OverflowState",
    "bootstrap",
]