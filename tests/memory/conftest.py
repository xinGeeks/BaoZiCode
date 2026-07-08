"""v0.8 memory tests — shared fixtures."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.config.schema import AppConfig, BackendConfig, MemoryConfig
from baozicode.memory import MemoryStore, Note, NoteType


@pytest.fixture
def tmp_project_root(tmp_path: Path) -> Path:
    """A fresh tmp dir treated as project_root."""
    return tmp_path


@pytest.fixture
def memory_config(tmp_project_root: Path) -> MemoryConfig:
    """MemoryConfig with user_dir/project_dir pointing under tmp_project_root."""
    return MemoryConfig(
        user_dir=tmp_project_root / "user_memory",
        project_dir=tmp_project_root / "project_memory",
    )


@pytest.fixture
def user_store(tmp_project_root: Path, memory_config: MemoryConfig) -> MemoryStore:
    store = MemoryStore(memory_config.user_dir, scope="user")
    return store


@pytest.fixture
def project_store(tmp_project_root: Path, memory_config: MemoryConfig) -> MemoryStore:
    store = MemoryStore(memory_config.project_dir, scope="project")
    return store


@pytest.fixture
def sample_note() -> Note:
    return Note(
        type=NoteType.USER_PREF,
        slug="no-emoji",
        title="User dislikes emoji",
        content="用户不喜欢 emoji,响应中不要使用。",
        created_at=datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
        source_session="20260708-100000-a1b2",
        tags=["ui", "preference"],
    )