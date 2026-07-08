"""v0.8 instructions tests — shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def tmp_project_root(tmp_path: Path) -> Path:
    """A fresh tmp dir treated as project_root."""
    return tmp_path


@pytest.fixture
def mock_user_baozicode_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Override Path.home() to a tmp dir so `~/.baozicode/...` tests are hermetic.

    Returns the fake home dir.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home
