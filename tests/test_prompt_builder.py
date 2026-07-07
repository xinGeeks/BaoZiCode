"""PromptBuilder.build() 端到端测试。"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.prompt.builder import PromptBuilder
from baozicode.prompt.rules import DEFAULT_RULES, RuleRegistry
from baozicode.prompt.types import BuiltPrompt, CacheBreakpoint
from baozicode.tools.base import ToolDefinition


@dataclass
class FakeConfig:
    system_prompt: str = "You are BaoZiCode, a helpful AI coding assistant."
    custom_instructions: str = ""
    skills_dir: Path = Path("/nonexistent/skills")
    memory_path: Path = Path("/nonexistent/memory.md")


def _ctx(plan_mode: bool = False, custom: str = "") -> object:
    @dataclass
    class C:
        system_prompt: str = "You are BaoZiCode, a helpful AI coding assistant."
        custom_instructions: str = custom
        skills_dir: Path = field(default_factory=lambda: Path("/nonexistent/skills"))
        memory_path: Path = field(default_factory=lambda: Path("/nonexistent/memory.md"))

    return C()


def test_build_returns_builtprompt() -> None:
    builder = PromptBuilder(rule_registry=RuleRegistry())
    bp = builder.build(config=_ctx(), plan_mode=False, tools=[])  # type: ignore[arg-type]
    assert isinstance(bp, BuiltPrompt)


def test_build_stable_system_contains_all_7_fixed_headings() -> None:
    builder = PromptBuilder(rule_registry=RuleRegistry())
    bp = builder.build(config=_ctx(), plan_mode=False, tools=[])  # type: ignore[arg-type]
    for heading in [
        "## 身份",
        "## 系统约束",
        "## 任务模式",
        "## 动作执行",
        "## 工具使用关键规则",
        "## 语气风格",
        "## 文本输出",
    ]:
        assert heading in bp.stable_system, f"missing heading: {heading}"


def test_build_env_info_not_in_stable_system() -> None:
    """env_info 是动态,应该进 dynamic_messages 而不是 stable_system。"""
    builder = PromptBuilder(rule_registry=RuleRegistry())
    bp = builder.build(config=_ctx(), plan_mode=False, tools=[])  # type: ignore[arg-type]
    assert "## 环境信息" not in bp.stable_system
    assert len(bp.dynamic_messages) == 1
    assert "<system-reminder type=\"env\"" in bp.dynamic_messages[0].content


def test_build_optional_sections_excluded_when_empty() -> None:
    builder = PromptBuilder(rule_registry=RuleRegistry())
    bp = builder.build(config=_ctx(custom=""), plan_mode=False, tools=[])  # type: ignore[arg-type]
    assert "## 自定义指令" not in bp.stable_system
    assert "## 已激活 Skill" not in bp.stable_system
    assert "## 长期记忆" not in bp.stable_system


def test_build_custom_instructions_appear_when_set() -> None:
    builder = PromptBuilder(rule_registry=RuleRegistry())
    bp = builder.build(config=_ctx(custom="总是用中文回复"), plan_mode=False, tools=[])  # type: ignore[arg-type]
    assert "## 自定义指令" in bp.stable_system
    assert "总是用中文回复" in bp.stable_system


def test_build_plan_mode_changes_task_mode_heading_text() -> None:
    builder = PromptBuilder(rule_registry=RuleRegistry())
    bp = builder.build(config=_ctx(), plan_mode=True, tools=[])  # type: ignore[arg-type]
    assert "Plan(只读)" in bp.stable_system
    assert "只能用无副作用工具" in bp.stable_system


def test_build_cache_breakpoints_always_present() -> None:
    builder = PromptBuilder(rule_registry=RuleRegistry())
    bp = builder.build(config=_ctx(), plan_mode=False, tools=[])  # type: ignore[arg-type]
    locations = [b.location for b in bp.cache_breakpoints]
    assert "system_start" in locations
    assert "after_tools" in locations


def test_build_augments_tools_with_rule_prefixes() -> None:
    builder = PromptBuilder(rule_registry=RuleRegistry())
    edit = ToolDefinition(
        name="Edit", description="Edit file content.", parameters={}
    )
    bp = builder.build(config=_ctx(), plan_mode=False, tools=[edit])  # type: ignore[arg-type]
    assert len(bp.augmented_tools) == 1
    aug = bp.augmented_tools[0]
    assert aug.name == "Edit"
    assert "Edit file content." in aug.description
    assert "【必读】" in aug.description  # edit_requires_read 注入


def test_build_sections_separated_by_blank_lines() -> None:
    builder = PromptBuilder(rule_registry=RuleRegistry())
    bp = builder.build(config=_ctx(), plan_mode=False, tools=[])  # type: ignore[arg-type]
    # 任意两个 ## 标题之间应有 \n\n
    import re
    headings = re.findall(r"^## .+$", bp.stable_system, re.MULTILINE)
    assert len(headings) >= 7
    # 用 heading 在原文中找位置,验证任意两个 heading 之间的间隔含 \n\n
    text = bp.stable_system
    pos_a = text.index(headings[0])
    pos_b = text.index(headings[1])
    between = text[pos_a:pos_b]
    assert "\n\n" in between
