"""v1.0 Skills — schema + frontmatter parser 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.skills.schema import (
    MAX_HISTORY_BUBBLES,
    SkillDef,
    SkillFrontmatter,
    parse_frontmatter,
)


# ---- SkillFrontmatter 直接构造 ----


class TestSkillFrontmatterValidation:
    def test_minimal_valid(self) -> None:
        fm = SkillFrontmatter(name="foo", description="bar")
        assert fm.name == "foo"
        assert fm.description == "bar"
        assert fm.mode == "shared"
        assert fm.allowed_tools is None
        assert fm.history_bubbles == 0
        assert fm.model is None
        assert fm.hidden is False

    def test_full_valid(self) -> None:
        fm = SkillFrontmatter(
            name="review",
            description="审查代码",
            mode="independent",
            allowed_tools=["Read", "Grep"],
            **{"history-bubbles": 5},
            model="claude-haiku-4-5",
        )
        assert fm.mode == "independent"
        assert fm.allowed_tools == ["Read", "Grep"]
        assert fm.history_bubbles == 5
        assert fm.model == "claude-haiku-4-5"

    @pytest.mark.parametrize(
        "bad_name",
        [
            "Foo",  # uppercase
            "9foo",  # digit start
            "foo_bar",  # underscore
            "foo bar",  # space
            "foo!",  # symbol
            "",  # empty
        ],
    )
    def test_invalid_name_rejected(self, bad_name: str) -> None:
        with pytest.raises(ValueError, match="skill name 不合法"):
            SkillFrontmatter(name=bad_name, description="x")

    def test_valid_name_formats(self) -> None:
        for n in ["a", "foo", "foo-bar", "foo-bar-baz", "a1", "x9-y8-z7"]:
            fm = SkillFrontmatter(name=n, description="x")
            assert fm.name == n

    def test_invalid_mode(self) -> None:
        with pytest.raises(ValueError):
            SkillFrontmatter(name="foo", description="x", mode="invalid")  # type: ignore[arg-type]

    def test_history_bubbles_zero_allowed(self) -> None:
        fm = SkillFrontmatter(name="foo", description="x", **{"history-bubbles": 0})
        assert fm.history_bubbles == 0

    def test_history_bubbles_max_allowed(self) -> None:
        fm = SkillFrontmatter(
            name="foo", description="x", **{"history-bubbles": MAX_HISTORY_BUBBLES}
        )
        assert fm.history_bubbles == MAX_HISTORY_BUBBLES

    def test_history_bubbles_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="history_bubbles 不能为负数"):
            SkillFrontmatter(name="foo", description="x", **{"history-bubbles": -1})

    def test_history_bubbles_too_large_rejected(self) -> None:
        with pytest.raises(ValueError, match="超过上限"):
            SkillFrontmatter(
                name="foo", description="x", **{"history-bubbles": MAX_HISTORY_BUBBLES + 1}
            )

    def test_allowed_tools_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="必须是非空字符串"):
            SkillFrontmatter(
                name="foo", description="x", **{"allowed-tools": ["Read", ""]}
            )

    def test_allowed_tools_duplicates_rejected(self) -> None:
        with pytest.raises(ValueError, match="有重复"):
            SkillFrontmatter(
                name="foo", description="x", **{"allowed-tools": ["Read", "Read"]}
            )

    def test_allowed_tools_none_passes(self) -> None:
        fm = SkillFrontmatter(name="foo", description="x", **{"allowed-tools": None})
        assert fm.allowed_tools is None

    def test_hidden_field(self) -> None:
        fm = SkillFrontmatter(name="foo", description="x", hidden=True)
        assert fm.hidden is True


# ---- parse_frontmatter ----


class TestParseFrontmatter:
    def test_full_document(self) -> None:
        text = (
            "---\n"
            "name: review\n"
            "description: 审查代码\n"
            "mode: shared\n"
            "allowed-tools: [Read, Grep]\n"
            "history-bubbles: 3\n"
            "---\n"
            "\n"
            "请审查自 {since} 起。\n"
        )
        fm, body = parse_frontmatter(text)
        assert fm.name == "review"
        assert fm.description == "审查代码"
        assert fm.mode == "shared"
        assert fm.allowed_tools == ["Read", "Grep"]
        assert fm.history_bubbles == 3
        assert body == "请审查自 {since} 起。\n"

    def test_no_frontmatter(self) -> None:
        # 无 `---` 开头 → 整段当 body,frontmatter 用默认值
        # 但 name 必填,缺 name 仍 ValueError
        with pytest.raises(ValueError, match="校验失败"):
            parse_frontmatter("just some body text\n")

    def test_empty_frontmatter_with_body(self) -> None:
        text = "---\n---\nbody content\n"
        with pytest.raises(ValueError, match="校验失败"):
            parse_frontmatter(text)

    def test_malformed_yaml(self) -> None:
        text = (
            "---\n"
            "name: foo\n"
            "description: [\n"  # 非法 YAML(未闭合 list)
            "---\n"
            "body\n"
        )
        with pytest.raises(ValueError, match="YAML 解析失败"):
            parse_frontmatter(text)

    def test_frontmatter_not_a_mapping(self) -> None:
        text = (
            "---\n"
            "- just\n"
            "- a\n"
            "- list\n"
            "---\n"
            "body\n"
        )
        with pytest.raises(ValueError, match="必须是 YAML mapping"):
            parse_frontmatter(text)

    def test_missing_closing_separator(self) -> None:
        text = "---\nname: foo\ndescription: bar\nno closing fence\n"
        with pytest.raises(ValueError, match="未正确以"):
            parse_frontmatter(text)

    def test_leading_dash_but_not_separator(self) -> None:
        # 文本以 `---` 开头但后面不是 `---` 行(只是 `---foo`)
        text = "---foo\nname: bar\ndescription: baz\n"
        # 不视为 frontmatter,整段当 body,缺 name 仍报错
        with pytest.raises(ValueError, match="校验失败"):
            parse_frontmatter(text)

    def test_file_path_in_error(self) -> None:
        text = "---\nname: INVALID NAME\n---\n"
        with pytest.raises(ValueError, match=r"/tmp/foo\.md:"):
            parse_frontmatter(text, file_path=Path("/tmp/foo.md"))

    def test_kebab_case_alias(self) -> None:
        # YAML 写 `history-bubbles` 和 `allowed-tools`(kebab),
        # Pydantic 应该认(alias)
        text = (
            "---\n"
            "name: x\n"
            "description: y\n"
            "history-bubbles: 7\n"
            "allowed-tools: [Bash, Read]\n"
            "---\n"
        )
        fm, _ = parse_frontmatter(text)
        assert fm.history_bubbles == 7
        assert fm.allowed_tools == ["Bash", "Read"]

    def test_snake_case_alias_also_accepted(self) -> None:
        # YAML 写 `history_bubbles` 和 `allowed_tools`(snake)也行
        text = (
            "---\n"
            "name: x\n"
            "description: y\n"
            "history_bubbles: 7\n"
            "allowed_tools: [Bash, Read]\n"
            "---\n"
        )
        fm, _ = parse_frontmatter(text)
        assert fm.history_bubbles == 7
        assert fm.allowed_tools == ["Bash", "Read"]

    def test_extra_fields_ignored(self) -> None:
        text = (
            "---\n"
            "name: x\n"
            "description: y\n"
            "unknown_field: ignored\n"
            "another: 42\n"
            "---\n"
        )
        fm, _ = parse_frontmatter(text)
        assert fm.name == "x"

    def test_placeholder_in_body_preserved(self) -> None:
        text = (
            "---\n"
            "name: x\n"
            "description: y\n"
            "---\n"
            "请审查自 {since} 起,关注 {focus}。\n"
        )
        fm, body = parse_frontmatter(text)
        assert "{since}" in body
        assert "{focus}" in body

    def test_body_with_multiple_paragraphs(self) -> None:
        text = (
            "---\n"
            "name: x\n"
            "description: y\n"
            "---\n"
            "\n"
            "## 段一\n"
            "para 1\n"
            "\n"
            "## 段二\n"
            "para 2\n"
        )
        _, body = parse_frontmatter(text)
        assert "## 段一" in body
        assert "## 段二" in body


# ---- SkillDef ----


class TestSkillDef:
    def _make(self, **overrides) -> SkillDef:
        fm = SkillFrontmatter(name="review", description="x", **overrides)
        return SkillDef(
            frontmatter=fm,
            body="请审查自 {since} 起。",
            source="user",
            path=Path("/tmp/review/SKILL.md"),
        )

    def test_property_accessors(self) -> None:
        sd = self._make()
        assert sd.name == "review"
        assert sd.description == "x"
        assert sd.mode == "shared"
        assert sd.allowed_tools is None
        assert sd.history_bubbles == 0
        assert sd.model is None
        assert sd.hidden is False

    def test_frozen(self) -> None:
        sd = self._make()
        with pytest.raises((AttributeError, Exception)):
            sd.frontmatter = None  # type: ignore[misc]

    def test_source_pass_through(self) -> None:
        sd = self._make()
        assert sd.source == "user"
        assert sd.path == Path("/tmp/review/SKILL.md")

    def test_body_preserved_as_is(self) -> None:
        sd = self._make()
        assert sd.body == "请审查自 {since} 起。"

    def test_independent_mode_properties(self) -> None:
        sd = self._make(mode="independent", **{"history-bubbles": 5, "model": "haiku"})
        assert sd.mode == "independent"
        assert sd.history_bubbles == 5
        assert sd.model == "haiku"
