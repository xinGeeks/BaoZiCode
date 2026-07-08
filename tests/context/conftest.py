"""v0.7 context-management tests conftest — shared fixtures."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.config.schema import CompactionConfig
from baozicode.context.schema import CompactionTelemetry, ContextConfig
from baozicode.context.storage import ContextStorage


@pytest.fixture
def tmp_project_root(tmp_path: Path) -> Path:
    """autouse? No — caller can opt-out. Returns a fresh tmp dir for project root."""
    return tmp_path


@pytest.fixture
def context_storage(tmp_project_root: Path) -> ContextStorage:
    """Build a ContextStorage with a random session_id under tmp_project_root."""
    sid = uuid.uuid4().hex
    return ContextStorage(project_root=tmp_project_root, session_id=sid)


@pytest.fixture
def context_config() -> ContextConfig:
    """Default ContextConfig (128K window, 13K auto reserve, 8K block threshold)."""
    return ContextConfig.build(
        context_window_tokens=128_000,
        trigger="auto",
        compaction=CompactionConfig(),
    )


@pytest.fixture
def compaction_telemetry() -> CompactionTelemetry:
    return CompactionTelemetry()


@pytest.fixture
def gitignore_present(tmp_project_root: Path) -> Path:
    """Create a project_root that already has a .gitignore without our line."""
    gi = tmp_project_root / ".gitignore"
    gi.write_text("__pycache__/\n.env\n", encoding="utf-8")
    return tmp_project_root
