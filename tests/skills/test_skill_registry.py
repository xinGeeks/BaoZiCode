"""v1.0 Skills — registry 扫描 + 3 级优先级 + reload 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.skills.registry import (
    ScanError,
    SkillRegistry,
    emit_scan_warnings,
)


# ---- helpers ----


def write_skill(root: Path, name: str, body_yaml: str, body_md: str = "do something") -> Path:
    """在 root/<name>/SKILL.md 写一个 Skill 文件。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(
        f"---\n{body_yaml}\n---\n\n{body_md}\n",
        encoding="utf-8",
    )
    return p


VALID_TOOLS = {"Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch"}


# ---- scan 基础 ----


class TestScanBasics:
    def test_no_dirs_returns_empty(self, tmp_path: Path) -> None:
        reg = SkillRegistry.scan()
        assert len(reg) == 0
        assert reg.scan_errors == []
        assert reg.list_visible() == []

    def test_nonexistent_dirs_silent_skip(self, tmp_path: Path) -> None:
        reg = SkillRegistry.scan(
            builtin_dir=tmp_path / "missing-builtin",
            user_dir=tmp_path / "missing-user",
            project_dir=tmp_path / "missing-project",
        )
        assert len(reg) == 0

    def test_single_builtin_skill(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        write_skill(builtin, "review", "name: review\ndescription: 审查代码")
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        assert len(reg) == 1
        sd = reg.lookup("review")
        assert sd is not None
        assert sd.source == "builtin"
        assert sd.body == "do something\n"

    def test_scan_sort_alphabetical(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        for n in ["zeta", "alpha", "mike"]:
            write_skill(builtin, n, f"name: {n}\ndescription: x")
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        visible = reg.list_visible()
        names = [n for n, _, _ in visible]
        assert names == ["alpha", "mike", "zeta"]


# ---- 3 级优先级 ----


class TestThreeLevelPriority:
    def test_project_overrides_builtin(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        project = tmp_path / "project"
        write_skill(builtin, "review", "name: review\ndescription: builtin 版")
        write_skill(project, "review", "name: review\ndescription: project 版")
        reg = SkillRegistry.scan(
            builtin_dir=builtin, project_dir=project, valid_tools=VALID_TOOLS
        )
        assert len(reg) == 1
        sd = reg.lookup("review")
        assert sd is not None
        assert sd.source == "project"
        assert sd.description == "project 版"

    def test_user_overrides_builtin(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        write_skill(builtin, "test", "name: test\ndescription: builtin")
        write_skill(user, "test", "name: test\ndescription: user")
        reg = SkillRegistry.scan(
            builtin_dir=builtin, user_dir=user, valid_tools=VALID_TOOLS
        )
        assert len(reg) == 1
        assert reg.lookup("test").description == "user"  # type: ignore[union-attr]
        assert reg.lookup("test").source == "user"  # type: ignore[union-attr]

    def test_project_beats_user_beats_builtin(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        project = tmp_path / "project"
        write_skill(builtin, "x", "name: x\ndescription: b")
        write_skill(user, "x", "name: x\ndescription: u")
        write_skill(project, "x", "name: x\ndescription: p")
        reg = SkillRegistry.scan(
            builtin_dir=builtin,
            user_dir=user,
            project_dir=project,
            valid_tools=VALID_TOOLS,
        )
        assert reg.lookup("x").description == "p"  # type: ignore[union-attr]
        assert reg.lookup("x").source == "project"  # type: ignore[union-attr]

    def test_disjoint_skills_merge(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        write_skill(builtin, "alpha", "name: alpha\ndescription: a")
        write_skill(user, "beta", "name: beta\ndescription: b")
        reg = SkillRegistry.scan(
            builtin_dir=builtin, user_dir=user, valid_tools=VALID_TOOLS
        )
        assert len(reg) == 2
        assert "alpha" in reg
        assert "beta" in reg


# ---- 解析失败 ----


class TestParseFailure:
    def test_single_failure_skipped(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        write_skill(builtin, "good", "name: good\ndescription: x")
        write_skill(builtin, "bad", "name: BAD NAME\ndescription: x")  # uppercase
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        assert "good" in reg
        assert "bad" not in reg
        assert len(reg.scan_errors) == 1
        assert "BAD NAME" in reg.scan_errors[0].reason

    def test_all_failures_empty_registry(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        write_skill(builtin, "a", "name: A\ndescription: x")  # uppercase
        write_skill(builtin, "b", "name: B\ndescription: x")  # uppercase
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        assert len(reg) == 0
        assert len(reg.scan_errors) == 2

    def test_emit_scan_warnings_single(self, capsys) -> None:
        e = [ScanError(path=Path("/tmp/a.md"), reason="bad name")]
        emit_scan_warnings(e)
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert "a.md" in captured.err

    def test_emit_scan_warnings_multi(self, capsys) -> None:
        errors = [
            ScanError(path=Path("/tmp/a.md"), reason="r1"),
            ScanError(path=Path("/tmp/b.md"), reason="r2"),
        ]
        emit_scan_warnings(errors)
        captured = capsys.readouterr()
        assert "2 个 skill 解析失败" in captured.err
        assert "a.md" in captured.err
        assert "b.md" in captured.err

    def test_emit_scan_warnings_empty(self, capsys) -> None:
        emit_scan_warnings([])
        captured = capsys.readouterr()
        assert captured.err == ""


# ---- allowed-tools 校验 ----


class TestAllowedToolsValidation:
    def test_valid_tools_passes(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        write_skill(
            builtin,
            "review",
            "name: review\ndescription: x\nallowed-tools: [Read, Grep]",
        )
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        assert "review" in reg

    def test_unknown_tool_panics(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        write_skill(
            builtin,
            "review",
            "name: review\ndescription: x\nallowed-tools: [Read, NonExistentTool]",
        )
        with pytest.raises(SystemExit, match="NonExistentTool"):
            SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)

    def test_unknown_tool_error_mentions_skill_name(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        write_skill(
            builtin,
            "broken",
            "name: broken\ndescription: x\nallowed-tools: [FooBar]",
        )
        with pytest.raises(SystemExit, match="broken"):
            SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)

    def test_no_valid_tools_no_validation(self, tmp_path: Path) -> None:
        # valid_tools=None → 跳过校验(测试或 bootstrap 阶段)
        builtin = tmp_path / "builtin"
        write_skill(
            builtin,
            "review",
            "name: review\ndescription: x\nallowed-tools: [AnythingGoes]",
        )
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=None)
        assert "review" in reg


# ---- list_visible ----


class TestListVisible:
    def test_excludes_hidden(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        write_skill(builtin, "show", "name: show\ndescription: visible")
        write_skill(builtin, "hide", "name: hide\ndescription: hidden\nhidden: true")
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        names = [n for n, _, _ in reg.list_visible()]
        assert "show" in names
        assert "hide" not in names

    def test_hidden_still_loadable(self, tmp_path: Path) -> None:
        # hidden 仍能 lookup / load_skill(只是不显示)
        builtin = tmp_path / "builtin"
        write_skill(builtin, "hide", "name: hide\ndescription: x\nhidden: true")
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        assert "hide" in reg  # __contains__ 仍可见
        assert reg.lookup("hide") is not None  # lookup 也行

    def test_source_in_output(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        write_skill(builtin, "a", "name: a\ndescription: x")
        write_skill(user, "b", "name: b\ndescription: y")
        reg = SkillRegistry.scan(builtin_dir=builtin, user_dir=user, valid_tools=VALID_TOOLS)
        result = {(n, d): s for n, d, s in reg.list_visible()}
        assert result[("a", "x")] == "builtin"
        assert result[("b", "y")] == "user"


# ---- reload ----


class TestReload:
    def test_reload_updates_body(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        path = write_skill(builtin, "review", "name: review\ndescription: v1")
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        assert reg.lookup("review").description == "v1"  # type: ignore[union-attr]
        # 改文件
        path.write_text(
            "---\nname: review\ndescription: v2\n---\n\nnew body\n",
            encoding="utf-8",
        )
        reg.reload("review", valid_tools=VALID_TOOLS)
        assert reg.lookup("review").description == "v2"  # type: ignore[union-attr]
        assert reg.lookup("review").body == "new body\n"  # type: ignore[union-attr]

    def test_reload_failure_keeps_old(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        path = write_skill(builtin, "review", "name: review\ndescription: v1")
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        # 改坏文件
        path.write_text(
            "---\nname: BAD NAME\ndescription: x\n---\n",
            encoding="utf-8",
        )
        result = reg.reload("review", valid_tools=VALID_TOOLS)
        # reload 失败时返回旧版本
        assert result.description == "v1"
        # registry 里的也是旧的
        assert reg.lookup("review").description == "v1"  # type: ignore[union-attr]
        # scan_errors 增加一条
        assert any("reload 失败" in e.reason for e in reg.scan_errors)

    def test_reload_unknown_skill_raises(self, tmp_path: Path) -> None:
        reg = SkillRegistry.scan()
        with pytest.raises(KeyError, match="nonexistent"):
            reg.reload("nonexistent")

    def test_reload_preserves_source(self, tmp_path: Path) -> None:
        # 即使文件被搬到别的目录,reload 用 path 重读,source 不变
        builtin = tmp_path / "builtin"
        path = write_skill(builtin, "review", "name: review\ndescription: v1")
        reg = SkillRegistry.scan(builtin_dir=builtin, valid_tools=VALID_TOOLS)
        path.write_text(
            "---\nname: review\ndescription: v2\n---\n\n",
            encoding="utf-8",
        )
        reg.reload("review", valid_tools=VALID_TOOLS)
        assert reg.lookup("review").source == "builtin"  # type: ignore[union-attr]
