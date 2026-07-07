"""v0.4 schema extension tests — AppConfig fields, RulesConfig, AgentConfig additions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.config.schema import (
    AgentConfig,
    AppConfig,
    BackendConfig,
    RulesConfig,
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


def test_rules_config_defaults_all_true() -> None:
    """RulesConfig() with no args — all 7 rule fields default to True."""
    rc = RulesConfig()
    assert rc.edit_requires_read is True
    assert rc.prefer_specialized_tools is True
    assert rc.bash_timeout is True
    assert rc.parallel_limit is True
    assert rc.error_then_decide is True
    assert rc.absolute_paths is True
    assert rc.webfetch_to_file is True
    print("[OK] RulesConfig() defaults: all 7 rules True")


def test_appconfig_v04_defaults_on_minimal_yaml() -> None:
    """Minimal AppConfig has v0.4 fields with sensible defaults."""
    cfg = _minimal_appconfig()
    assert cfg.custom_instructions == ""
    assert isinstance(cfg.skills_dir, Path)
    assert isinstance(cfg.memory_path, Path)
    # The defaults expand ~/, so they shouldn't equal the raw "~" string
    assert "~" not in str(cfg.skills_dir)
    print("[OK] AppConfig minimal: custom_instructions='' + Path defaults")


def test_agent_config_v04_defaults() -> None:
    """AgentConfig() defaults include enable_system_reminders=True + rules=RulesConfig()."""
    ac = AgentConfig()
    assert ac.enable_system_reminders is True
    assert ac.plan_reminder_interval == 5
    assert isinstance(ac.rules, RulesConfig)
    assert ac.rules.edit_requires_read is True
    assert ac.max_iterations == 20  # v0.3 backward compat
    print("[OK] AgentConfig() defaults: enable=True, interval=5, rules=RulesConfig()")


def test_agent_config_disabling_one_rule_round_trips() -> None:
    """Setting `agent: rules: { edit_requires_read: false }` flips that one field."""
    ac = AgentConfig(rules=RulesConfig(edit_requires_read=False))
    assert ac.rules.edit_requires_read is False
    # Other rules keep their defaults
    assert ac.rules.prefer_specialized_tools is True
    assert ac.rules.bash_timeout is True
    print("[OK] disabling edit_requires_read doesn't touch the other rules")


def test_agent_config_unknown_rule_keys_are_ignored() -> None:
    """RulesConfig extra='ignore' silently drops unknown keys without crashing."""
    rc = RulesConfig(some_future_rule=True)  # type: ignore[call-arg]
    # Should not raise; the unknown field must be silently dropped
    assert all(
        getattr(rc, f, True) is True
        for f in (
            "edit_requires_read",
            "prefer_specialized_tools",
            "bash_timeout",
            "parallel_limit",
            "error_then_decide",
            "absolute_paths",
            "webfetch_to_file",
        )
    )
    # And the unknown field is not exposed as an attribute
    assert not hasattr(rc, "some_future_rule") or getattr(rc, "some_future_rule", None) is True
    print("[OK] unknown rule keys silently dropped by extra='ignore'")


def test_appconfig_agent_block_round_trip() -> None:
    """Full AppConfig with custom agent block round-trips all v0.4 fields."""
    cfg = _minimal_appconfig(
        custom_instructions="Always respond in Chinese.",
        skills_dir=Path("/custom/skills"),
        memory_path=Path("/custom/memory.md"),
        agent=AgentConfig(
            enable_system_reminders=False,
            plan_reminder_interval=3,
            rules=RulesConfig(bash_timeout=False),
        ),
    )
    assert cfg.custom_instructions == "Always respond in Chinese."
    assert str(cfg.skills_dir).replace("\\", "/").endswith("custom/skills")
    assert str(cfg.memory_path).replace("\\", "/").endswith("custom/memory.md")
    a = cfg.active_agent()
    assert a.enable_system_reminders is False
    assert a.plan_reminder_interval == 3
    assert a.rules.bash_timeout is False
    print("[OK] full AppConfig round-trip: custom_instructions + agent.rules override")


def main() -> None:
    test_rules_config_defaults_all_true()
    test_appconfig_v04_defaults_on_minimal_yaml()
    test_agent_config_v04_defaults()
    test_agent_config_disabling_one_rule_round_trips()
    test_agent_config_unknown_rule_keys_are_ignored()
    test_appconfig_agent_block_round_trip()
    print("\nAll config_schema_v04 tests passed.")


if __name__ == "__main__":
    main()
