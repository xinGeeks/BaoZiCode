"""Rule + RuleRegistry + DEFAULT_RULES 单元测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.prompt.rules import DEFAULT_RULES, Rule, RuleRegistry
from baozicode.tools.base import ToolDefinition


def test_default_rules_count_is_seven() -> None:
    assert len(DEFAULT_RULES) == 7


def test_default_rule_ids_unique() -> None:
    ids = [r.id for r in DEFAULT_RULES]
    assert len(set(ids)) == 7


def test_rule_is_frozen() -> None:
    r = Rule(id="x", prompt_text="p", applies_to=("*",), tool_prefix="")
    try:
        r.id = "y"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Rule should be frozen")


def test_registry_filters_by_tool_name() -> None:
    reg = RuleRegistry(rules=DEFAULT_RULES)
    edit_rules = reg.for_tool("Edit")
    write_rules = reg.for_tool("Write")
    bash_rules = reg.for_tool("Bash")
    # edit_requires_read 适用 Edit 和 Write
    edit_ids = {r.id for r in edit_rules}
    write_ids = {r.id for r in write_rules}
    bash_ids = {r.id for r in bash_rules}
    assert "edit_requires_read" in edit_ids
    assert "edit_requires_read" in write_ids
    assert "bash_timeout" in bash_ids
    assert "edit_requires_read" not in bash_ids


def test_registry_wildcard_included_in_all_tools() -> None:
    reg = RuleRegistry(rules=DEFAULT_RULES)
    edit_rules = reg.for_tool("Edit")
    read_rules = reg.for_tool("Read")
    edit_ids = {r.id for r in edit_rules}
    read_ids = {r.id for r in read_rules}
    # error_then_decide 适用 "*"
    assert "error_then_decide" in edit_ids
    assert "error_then_decide" in read_ids


def test_registry_render_for_prompt_returns_seven_blocks() -> None:
    reg = RuleRegistry(rules=DEFAULT_RULES)
    text = reg.render_for_prompt()
    for i in range(1, 8):
        assert f"{i}." in text


def test_tool_definition_is_replaced_not_mutated() -> None:
    """RuleRegistry.augment_tool 应返回新 ToolDefinition,不改原对象。"""
    original = ToolDefinition(
        name="Edit", description="原描述", parameters={}
    )
    reg = RuleRegistry(rules=DEFAULT_RULES)
    new = reg.augment_tool(original)
    assert new is not original
    assert original.description == "原描述"
    assert "原描述" in new.description
    # Edit 工具应被 edit_requires_read 加前缀
    assert "【必读】" in new.description
