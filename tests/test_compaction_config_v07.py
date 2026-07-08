"""v0.7 schema extension tests — CompactionConfig, context_window_tokens."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import ValidationError

from baozicode.config.schema import (
    AgentConfig,
    AppConfig,
    BackendConfig,
    CompactionConfig,
)


def _minimal_appconfig(**overrides) -> AppConfig:
    """Build a minimal AppConfig with all 4 backends stubbed. Allow overrides."""
    defaults = dict(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
    )
    defaults.update(overrides)
    return AppConfig(**defaults)


def test_compaction_config_defaults() -> None:
    """CompactionConfig() with no args uses documented defaults."""
    cc = CompactionConfig()
    assert cc.per_block_threshold == 8192
    assert cc.per_message_threshold == 20480
    assert cc.recent_window_min_messages == 5
    assert cc.recent_window_tokens == 10_000
    assert cc.reserve_tokens_auto == 13_000
    assert cc.reserve_tokens_manual == 3_000
    assert cc.max_summary_tokens == 2_000
    assert cc.max_consecutive_failures == 3
    print("[OK] CompactionConfig() defaults match design D1-D6")


def test_agent_config_v07_defaults() -> None:
    """AgentConfig() in v0.7 adds context_window_tokens=128_000 and compaction block."""
    ac = AgentConfig()
    assert ac.context_window_tokens == 128_000
    assert isinstance(ac.compaction, CompactionConfig)
    # v0.3/0.4/0.5 fields still default correctly
    assert ac.max_iterations == 20
    assert ac.enable_system_reminders is True
    assert ac.denial_warn_threshold == 5
    print("[OK] AgentConfig() v0.7: 128K window + CompactionConfig block")


def test_appconfig_v07_defaults_load() -> None:
    """Minimal AppConfig has v0.7 fields with sensible defaults; effective = 128K."""
    cfg = _minimal_appconfig()
    assert cfg.active_agent().context_window_tokens == 128_000
    assert cfg.active_agent().compaction.per_block_threshold == 8192
    assert cfg.effective_context_window() == 128_000
    print("[OK] AppConfig minimal: 128K default + effective_context_window()")


def test_agent_context_window_negative_rejected() -> None:
    """Setting agent.context_window_tokens: -1 must fail Pydantic validation."""
    with pytest.raises(ValidationError) as exc:
        AgentConfig(context_window_tokens=-1)  # type: ignore[arg-type]
    assert "context_window_tokens" in str(exc.value)
    print("[OK] agent.context_window_tokens: -1 rejected by Pydantic")


def test_compaction_per_block_threshold_zero_rejected() -> None:
    """Setting per_block_threshold: 0 must fail (gt=0)."""
    with pytest.raises(ValidationError) as exc:
        CompactionConfig(per_block_threshold=0)
    assert "per_block_threshold" in str(exc.value)
    print("[OK] compaction.per_block_threshold: 0 rejected by Pydantic")


def test_backend_context_window_none_allowed() -> None:
    """BackendConfig.context_window_tokens: None is the default (fallback to global)."""
    bc = BackendConfig(api_key="x", model="m")
    assert bc.context_window_tokens is None
    print("[OK] BackendConfig context_window_tokens: None default")


def test_backend_context_window_zero_rejected() -> None:
    """BackendConfig.context_window_tokens: 0 must fail (gt=0)."""
    with pytest.raises(ValidationError) as exc:
        BackendConfig(api_key="x", model="m", context_window_tokens=0)
    assert "context_window_tokens" in str(exc.value)
    print("[OK] BackendConfig context_window_tokens: 0 rejected by Pydantic")


def test_backend_context_window_override_takes_precedence() -> None:
    """Backend override wins over agent.context_window_tokens for the active backend."""
    cfg = _minimal_appconfig(
        agent=AgentConfig(context_window_tokens=200_000),
        anthropic=BackendConfig(api_key="x", model="m", context_window_tokens=128_000),
    )
    # Anthropic = backend override
    assert cfg.effective_context_window() == 128_000
    # Other backends fall back to agent default
    cfg_openai = cfg.model_copy(update={"backend": "openai"})
    assert cfg_openai.effective_context_window() == 200_000
    print("[OK] effective_context_window: backend override > agent default")


def test_effective_context_window_with_no_override() -> None:
    """No backend override → falls back to agent.context_window_tokens."""
    cfg = _minimal_appconfig(agent=AgentConfig(context_window_tokens=200_000))
    assert cfg.effective_context_window() == 200_000
    print("[OK] effective_context_window: no override → agent default")


def test_compaction_config_extra_ignore() -> None:
    """CompactionConfig extra='ignore' silently drops unknown keys without crashing."""
    cc = CompactionConfig(some_future_field=42)  # type: ignore[call-arg]
    # Should not raise; defaults preserved
    assert cc.per_block_threshold == 8192
    assert not hasattr(cc, "some_future_field")
    print("[OK] CompactionConfig extra='ignore' drops unknown keys")
