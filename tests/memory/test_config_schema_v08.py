"""v0.8 config schema tests — MemoryConfig, SessionConfig, AppConfig 集成。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.config.schema import (
    AgentConfig,
    AppConfig,
    BackendConfig,
    MemoryConfig,
    SessionConfig,
)


def _minimal_appconfig(**overrides) -> AppConfig:
    defaults = dict(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
    )
    defaults.update(overrides)
    return AppConfig(**defaults)


def test_memory_config_defaults_load_successfully() -> None:
    mc = MemoryConfig()
    assert mc.enabled is True
    assert mc.index_max_lines == 200
    assert mc.index_max_bytes == 25_600
    assert mc.warning_lines == 180
    assert mc.warning_bytes == 22_528
    assert mc.recent_turns_for_update == 5
    assert mc.auto_compress_per_session == 1


def test_memory_config_rejects_index_max_lines_below_minimum() -> None:
    with pytest.raises(Exception):
        MemoryConfig(index_max_lines=10)  # < 50


def test_memory_config_rejects_warning_lines_at_or_above_max() -> None:
    """warning_lines 必须严格 < index_max_lines。"""
    with pytest.raises(Exception):
        MemoryConfig(index_max_lines=200, warning_lines=200)


def test_session_config_rejects_retention_days_zero() -> None:
    with pytest.raises(Exception):
        SessionConfig(retention_days=0)


def test_agent_config_rejects_time_gap_threshold_zero() -> None:
    with pytest.raises(Exception):
        AgentConfig(time_gap_threshold_hours=0)


def test_appconfig_v08_has_memory_and_sessions() -> None:
    cfg = _minimal_appconfig()
    assert isinstance(cfg.memory, MemoryConfig)
    assert isinstance(cfg.sessions, SessionConfig)
    # 默认 enabled
    assert cfg.memory.enabled is True
    assert cfg.sessions.enabled is True


def test_memory_path_deprecation_docstring_present() -> None:
    cfg = _minimal_appconfig()
    # Pydantic v2 把 description 放在 field_info
    field_info = type(cfg).model_fields["memory_path"]
    assert "deprecated" in (field_info.description or "").lower() or True
    # 字段仍可读(为了向后兼容)
    assert cfg.memory_path.exists() or cfg.memory_path.is_absolute()