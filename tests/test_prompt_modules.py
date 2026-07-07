"""prompt/types.py 的 dataclass 形状测试。"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.prompt.types import (
    BuiltPrompt,
    BuildContext,
    CacheBreakpoint,
    SystemReminder,
)


def test_cache_breakpoint_is_frozen() -> None:
    bp = CacheBreakpoint(location="system_start", priority=100)
    try:
        bp.priority = 50  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("CacheBreakpoint should be frozen")


def test_builtprompt_default_factories() -> None:
    bp = BuiltPrompt(stable_system="hello")
    assert bp.stable_system == "hello"
    assert bp.dynamic_messages == []
    assert bp.augmented_tools == []
    assert bp.cache_breakpoints == []


def test_system_reminder_carries_kind_and_content() -> None:
    r = SystemReminder(kind="env", content="cwd: /tmp", ttl="static")
    assert r.kind == "env"
    assert r.content == "cwd: /tmp"
    assert r.ttl == "static"


def test_buildcontext_collects_config_and_rules() -> None:
    @dataclass
    class FakeConfig:
        custom_instructions: str = "extra"
        plan_mode: bool = False

    @dataclass
    class FakeRegistry:
        rules: tuple = ()

    ctx = BuildContext(config=FakeConfig(), rule_registry=FakeRegistry())  # type: ignore[arg-type]
    assert ctx.config.custom_instructions == "extra"
    assert ctx.rule_registry.rules == ()
