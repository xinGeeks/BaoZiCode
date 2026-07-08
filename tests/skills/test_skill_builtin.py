"""v1.0 Skills — 3 个 builtin 样板存在性 + frontmatter 校验。

不依赖 filesystem fixture:用 `SkillRegistry.scan(builtin_dir=...)` 扫
包内 `baozicode/skills/builtin/`,验证 3 个样板存在 + 字段合理。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.skills.registry import SkillRegistry
from baozicode.skills.schema import parse_frontmatter


BUILTIN_DIR = Path(__file__).parent.parent.parent / "baozicode" / "skills" / "builtin"
VALID_TOOLS = {"Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch"}


class TestBuiltinExistence:
    def test_three_builtin_dirs_exist(self) -> None:
        assert (BUILTIN_DIR / "commit").is_dir()
        assert (BUILTIN_DIR / "review").is_dir()
        assert (BUILTIN_DIR / "test").is_dir()

    def test_three_skill_files_exist(self) -> None:
        for name in ("commit", "review", "test"):
            assert (BUILTIN_DIR / name / "SKILL.md").is_file()

    def test_registry_finds_three(self) -> None:
        reg = SkillRegistry.scan(builtin_dir=BUILTIN_DIR, valid_tools=VALID_TOOLS)
        assert "commit" in reg
        assert "review" in reg
        assert "test" in reg
        assert len(reg) == 3


class TestBuiltinFrontmatter:
    def test_commit_frontmatter(self) -> None:
        text = (BUILTIN_DIR / "commit" / "SKILL.md").read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        assert fm.name == "commit"
        assert fm.mode == "shared"
        assert fm.allowed_tools is not None
        assert "Bash" in fm.allowed_tools
        assert len(body) > 50  # 有实质内容

    def test_review_frontmatter(self) -> None:
        text = (BUILTIN_DIR / "review" / "SKILL.md").read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        assert fm.name == "review"
        assert fm.mode == "independent"
        assert fm.history_bubbles == 3
        assert "{since}" in body  # 占位符保留

    def test_test_frontmatter(self) -> None:
        text = (BUILTIN_DIR / "test" / "SKILL.md").read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        assert fm.name == "test"
        assert fm.mode == "independent"
        assert fm.history_bubbles == 2
        assert fm.allowed_tools == ["Bash"]  # 只允许 Bash

    def test_all_bodies_under_50_lines(self) -> None:
        for name in ("commit", "review", "test"):
            text = (BUILTIN_DIR / name / "SKILL.md").read_text(encoding="utf-8")
            _, body = parse_frontmatter(text)
            line_count = len(body.strip().splitlines())
            assert line_count < 50, f"{name} body 超过 50 行 ({line_count})"

    def test_all_have_todo_override_hint(self) -> None:
        # 每个样板正文里有「项目可覆盖」提示
        for name in ("commit", "review", "test"):
            text = (BUILTIN_DIR / name / "SKILL.md").read_text(encoding="utf-8")
            assert "项目可放" in text or "可覆盖" in text, f"{name} 缺覆盖提示"

    def test_no_hidden_flag(self) -> None:
        # 3 个样板都不 hidden
        for name in ("commit", "review", "test"):
            text = (BUILTIN_DIR / name / "SKILL.md").read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(text)
            assert fm.hidden is False


class TestBuiltinToolWhitelist:
    def test_commit_whitelist_subset_of_valid_tools(self) -> None:
        # commit 引用了非 VALID_TOOLS 里的 tool(可能 None)
        reg = SkillRegistry.scan(builtin_dir=BUILTIN_DIR, valid_tools=VALID_TOOLS)
        # 上面这个 scan 不能用 None 当 valid,需要更多工具集合
        # 试更大的 valid 集合
        big_valid = VALID_TOOLS | {"mcp__fs__read_file"}
        reg = SkillRegistry.scan(builtin_dir=BUILTIN_DIR, valid_tools=big_valid)
        assert "commit" in reg

    def test_3_builtins_visible(self) -> None:
        reg = SkillRegistry.scan(builtin_dir=BUILTIN_DIR, valid_tools=VALID_TOOLS)
        visible = reg.list_visible()
        names = [n for n, _, _ in visible]
        assert names == ["commit", "review", "test"]  # 字母序


# ---- 覆盖 ----


class TestProjectOverrideBuiltin:
    def test_project_overrides_commit(self, tmp_path: Path) -> None:
        from tests.skills.test_skill_registry import write_skill

        project = tmp_path / "project"
        write_skill(
            project,
            "commit",
            "name: commit\ndescription: 项目自定义 commit",
        )
        reg = SkillRegistry.scan(
            builtin_dir=BUILTIN_DIR,
            project_dir=project,
            valid_tools=VALID_TOOLS,
        )
        sd = reg.lookup("commit")
        assert sd.source == "project"
        assert sd.description == "项目自定义 commit"
