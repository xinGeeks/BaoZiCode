"""v1.0 Skills — SkillActivation 单元测试。

覆盖:
- activate / deactivate / clear 基本行为 + 斜杠注册
- 幂等性(同 body/mode 不重复注册)
- 斜杠命令 PROMPT 类型 + handler 返回 PromptResult
- render_active_section 输出格式(空 / 一项 / 多项 / 顺序)
- allowed_tools dedup + history_bubbles 边界
- 与 v0.9 CommandRegistry 的双向同步(active ↔ _index)
"""

from __future__ import annotations

import pytest

from baozicode.commands.registry import (
    CommandRegistry,
    CommandType,
    PromptResult,
)
from baozicode.skills.activation import ActiveSkill, SkillActivation
from baozicode.skills.schema import MAX_HISTORY_BUBBLES


@pytest.fixture
def registry() -> CommandRegistry:
    return CommandRegistry()


@pytest.fixture
def activation(registry: CommandRegistry) -> SkillActivation:
    return SkillActivation(registry)


# ---- 基本行为 ----


class TestActivateBasics:
    def test_activate_marks_active(self, activation: SkillActivation) -> None:
        activation.activate("commit", "do commit SOP")
        assert activation.is_active("commit") is True
        assert activation.active_names() == ["commit"]

    def test_activate_registers_slash_in_registry(
        self,
        registry: CommandRegistry,
        activation: SkillActivation,
    ) -> None:
        activation.activate("commit", "do commit SOP")
        def_ = registry.lookup("commit")
        assert def_ is not None
        assert def_.name == "commit"
        assert def_.type == CommandType.PROMPT
        assert def_.handler is not None

    def test_activate_default_mode_is_shared(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("foo", "bar")
        entry = activation.get("foo")
        assert entry is not None
        assert entry.mode == "shared"

    def test_activate_independent_mode_with_history(
        self, activation: SkillActivation
    ) -> None:
        activation.activate(
            "review", "review SOP", mode="independent", history_bubbles=3
        )
        entry = activation.get("review")
        assert entry is not None
        assert entry.mode == "independent"
        assert entry.history_bubbles == 3


# ---- 幂等性 ----


class TestActivateIdempotent:
    def test_same_body_no_op(self, activation: SkillActivation) -> None:
        activation.activate("commit", "SOP body")
        activation.activate("commit", "SOP body")
        assert activation.active_names() == ["commit"]

    def test_different_body_reactivates(self, activation: SkillActivation) -> None:
        activation.activate("commit", "old")
        activation.activate("commit", "new")
        entry = activation.get("commit")
        assert entry is not None and entry.body == "new"

    def test_different_mode_reactivates(self, activation: SkillActivation) -> None:
        activation.activate("x", "b", mode="shared")
        activation.activate("x", "b", mode="independent")
        entry = activation.get("x")
        assert entry is not None and entry.mode == "independent"


# ---- deactivate / clear ----


class TestDeactivate:
    def test_deactivate_active_returns_true_and_unregisters(
        self,
        registry: CommandRegistry,
        activation: SkillActivation,
    ) -> None:
        activation.activate("commit", "body")
        assert activation.deactivate("commit") is True
        assert activation.is_active("commit") is False
        assert registry.lookup("commit") is None

    def test_deactivate_inactive_returns_false(
        self, activation: SkillActivation
    ) -> None:
        assert activation.deactivate("notthere") is False

    def test_deactivate_does_not_disturb_others(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("a", "a body")
        activation.activate("b", "b body")
        activation.deactivate("a")
        assert "a" not in activation.active_names()
        assert "b" in activation.active_names()


class TestClear:
    def test_clear_empties_all(
        self,
        registry: CommandRegistry,
        activation: SkillActivation,
    ) -> None:
        activation.activate("a", "a body")
        activation.activate("b", "b body")
        activation.clear()
        assert activation.active_names() == []
        assert registry.lookup("a") is None
        assert registry.lookup("b") is None


# ---- 斜杠命令 ----


class TestSlashCommand:
    @pytest.mark.asyncio
    async def test_handler_returns_prompt_result_with_body(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("commit", "the body text")
        def_ = activation._registry.lookup("commit")
        assert def_ is not None and def_.handler is not None
        result = await def_.handler()
        assert isinstance(result, PromptResult)
        assert result.text == "the body text"

    def test_handler_ignores_extra_args(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("x", "body text")
        def_ = activation._registry.lookup("x")
        assert def_ is not None and def_.handler is not None
        # 调用 _handler 的异步结果,不能 await 在 sync test — 仅同步取出协程对象
        coro = def_.handler("arg1", "arg2", focus="security")
        # 关掉协程避免 RuntimeWarning(用 .close())
        coro.close()


# ---- render_active_section ----


class TestRenderActiveSection:
    def test_empty_returns_empty_string(self, activation: SkillActivation) -> None:
        assert activation.render_active_section() == ""

    def test_one_active_includes_name_and_body(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("commit", "do commit SOP")
        text = activation.render_active_section()
        assert "commit" in text
        assert "do commit SOP" in text

    def test_section_has_sticky_attr(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("commit", "body")
        assert 'sticky="true"' in activation.render_active_section()

    def test_section_uses_active_skills_type(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("x", "y")
        text = activation.render_active_section()
        assert text.startswith('<system-reminder type="active_skills"')
        assert text.rstrip().endswith("</system-reminder>")

    def test_multiple_active_preserves_activation_order(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("a", "A body")
        activation.activate("b", "B body")
        activation.activate("c", "C body")
        text = activation.render_active_section()
        assert text.find("### a") < text.find("### b") < text.find("### c")

    def test_each_skill_wrapped_in_h3(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("commit", "the body")
        activation.activate("review", "the other body")
        text = activation.render_active_section()
        assert "### commit" in text
        assert "### review" in text


# ---- 字段规整 ----


class TestAllowedToolsDedup:
    def test_dedup_preserves_first_seen_order(
        self, activation: SkillActivation
    ) -> None:
        activation.activate(
            "x",
            "body",
            allowed_tools=["Bash", "Read", "Bash", "Grep", "Read"],
        )
        entry = activation.get("x")
        assert entry is not None
        assert list(entry.allowed_tools) == ["Bash", "Read", "Grep"]

    def test_none_treated_as_empty(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("x", "body", allowed_tools=None)
        assert list(activation.get("x").allowed_tools) == []


class TestHistoryBubblesBounds:
    def test_negative_clamps_to_zero(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("x", "body", history_bubbles=-1)
        assert activation.get("x").history_bubbles == 0

    def test_above_max_clamps_to_max(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("x", "body", history_bubbles=9999)
        assert activation.get("x").history_bubbles == MAX_HISTORY_BUBBLES

    def test_normal_passes_through(
        self, activation: SkillActivation
    ) -> None:
        activation.activate("x", "body", history_bubbles=5)
        assert activation.get("x").history_bubbles == 5
