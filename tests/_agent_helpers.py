"""Shared helpers for Agent tests — minimal AppConfig factory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.config.schema import AgentConfig, AppConfig, BackendConfig, RulesConfig


def make_minimal_config(**overrides) -> AppConfig:
    """Build a minimal AppConfig with all 4 backends stubbed.

    Accepts any AppConfig field as kwarg:
      make_minimal_config(backend="openai")
      make_minimal_config(custom_instructions="...")
      make_minimal_config(agent=AgentConfig(...))
    """
    defaults = dict(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
    )
    defaults.update(overrides)
    return AppConfig(**defaults)


__all__ = ["make_minimal_config"]
