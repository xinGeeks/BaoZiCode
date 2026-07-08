"""v1.0 Skills — `bootstrap_skills()` 启动装配测试。

覆盖:
- 路径解析(builtin/user/project)默认值正确
- 三级目录合并优先级(project > user > builtin)
- 路径覆盖(custom builtin_dir/user_dir/project_dir)生效
- valid_tools 校验(allowed-tools 引用不存在的 tool → SystemExit)
- SkillSet 4 组件齐全 + build_skill_filter 工厂
- 解析失败的文件 → 跳过 + scan_errors 收集,不阻断整体
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.skills.activation import SkillActivation
from baozicode.skills.bootstrap import SkillSet, bootstrap_skills
from baozicode.skills.execution import SkillExecutor
from baozicode.skills.loader import SkillLoader
from baozicode.skills.registry import SkillRegistry
from baozicode.skills.whitelist import SkillWhitelistFilter


# ---- 路径覆盖测试 ----


class TestPathDefaults:
    def test_builtin_dir_default_points_to_pkg(self) -> None:
        """默认 builtin_dir 指向包内 builtin/ 目录,3 个样板存在。"""
        from baozicode.skills.bootstrap import _builtin_dir
        bdir = _builtin_dir()
        assert bdir.is_dir()
        # commit / review / test 三个样板
        names = sorted(p.name for p in bdir.iterdir() if p.is_dir())
        assert "commit" in names
        assert "review" in names
        assert "test" in names

    def test_user_dir_default_points_to_config(self) -> None:
        """默认 user_dir 是 `~/.config/baozicode/skills/`。"""
        from baozicode.skills.bootstrap import _default_user_dir
        udir = _default_user_dir()
        assert udir == Path.home() / ".config" / "baozicode" / "skills"

    def test_project_dir_default_points_to_baozicode_subdir(self) -> None:
        from baozicode.skills.bootstrap import _default_project_dir
        root = Path("/tmp/proj-root")
        pdir = _default_project_dir(root)
        assert pdir == root / ".baozicode" / "skills"


# ---- 三级合并 + 覆盖 ----


class TestBootstrapScan:
    def test_minimal_returns_skillset(self, tmp_path: Path) -> None:
        """最小调用:无 project 目录时也能返回 SkillSet(空 registry)。"""
        ss = bootstrap_skills(tmp_path, tool_registry=None)
        assert isinstance(ss, SkillSet)
        assert isinstance(ss.registry, SkillRegistry)
        assert isinstance(ss.activation, SkillActivation)
        assert isinstance(ss.loader, SkillLoader)
        assert isinstance(ss.executor, SkillExecutor)

    def test_builtin_skills_loaded_by_default(self, tmp_path: Path) -> None:
        """包内 3 个 builtin Skill 自动可见。"""
        ss = bootstrap_skills(tmp_path, tool_registry=None)
        names = {sd.name for sd in ss.registry.list_all()}
        assert {"commit", "review", "test"}.issubset(names)

    def test_project_overrides_user_overrides_builtin(self, tmp_path: Path) -> None:
        """project > user > builtin,同名 Skill 后扫描覆盖先扫描。"""
        # 三级目录里各放一个同名 Skill(不同 description)
        builtin = tmp_path / "b"
        user = tmp_path / "u"
        project = tmp_path / "p"
        for d in (builtin, user, project):
            d.mkdir()
            (d / "commit").mkdir()
            (d / "commit" / "SKILL.md").write_text(
                f"---\nname: commit\n"
                f"description: from_{d.name}\n---\nbody_{d.name}\n",
                encoding="utf-8",
            )

        ss = bootstrap_skills(
            tmp_path, tool_registry=None,
            builtin_dir=builtin, user_dir=user, project_dir=project,
        )
        sd = ss.registry.lookup("commit")
        assert sd is not None
        # project 胜出
        assert sd.source == "project"
        assert "from_p" in sd.description

    def test_invalid_skill_skipped_not_panic(self, tmp_path: Path) -> None:
        """解析失败的单文件 → 跳过,scan_errors 收集,不阻断整体。"""
        d = tmp_path / "proj"
        d.mkdir()
        # 第一个 Skill 解析失败(name 不合法)
        bad = d / "bad"
        bad.mkdir()
        (bad / "SKILL.md").write_text(
            "---\nname: 9invalid\ndescription: x\n---\n",
            encoding="utf-8",
        )
        # 第二个 Skill 正常
        good = d / "ok"
        good.mkdir()
        (good / "SKILL.md").write_text(
            "---\nname: ok\ndescription: works\n---\n",
            encoding="utf-8",
        )
        ss = bootstrap_skills(
            tmp_path, tool_registry=None,
            builtin_dir=None, user_dir=None, project_dir=d,
        )
        assert ss.registry.lookup("ok") is not None
        assert ss.registry.lookup("bad") is None
        assert any("9invalid" in e.reason or "不合法" in e.reason
                   for e in ss.registry.scan_errors)


# ---- valid_tools 校验 ----


class TestValidTools:
    def test_unknown_tool_in_allowed_tools_panics(self, tmp_path: Path) -> None:
        """allowed-tools 引用不存在 tool → SystemExit。"""
        from baozicode.tools.registry import ToolRegistry as ToolsReg

        d = tmp_path / "proj"
        d.mkdir()
        skill = d / "mystery"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: mystery\n"
            "description: x\n"
            "allowed-tools: [GhostTool]\n"
            "---\n",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="GhostTool"):
            bootstrap_skills(
                tmp_path,
                tool_registry=ToolsReg(),  # 内置工具不含 GhostTool → 触发校验
                builtin_dir=None, user_dir=None, project_dir=d,
            )

    def test_allowed_tools_in_valid_set_passes(self, tmp_path: Path) -> None:
        """allowed-tools 全部命中 valid_tools → 不 panic。"""
        from baozicode.tools.registry import ToolRegistry as ToolsReg

        d = tmp_path / "proj"
        d.mkdir()
        skill = d / "real"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: real\n"
            "description: x\n"
            "allowed-tools: [Read, Bash]\n"
            "---\n",
            encoding="utf-8",
        )
        reg = ToolsReg()  # 含 Read/Bash/Grep/Glob/...
        ss = bootstrap_skills(
            tmp_path, tool_registry=reg,
            builtin_dir=None, user_dir=None, project_dir=d,
        )
        assert ss.registry.lookup("real") is not None

    def test_no_tool_registry_skips_validation(self, tmp_path: Path) -> None:
        """tool_registry=None → valid_tools=None → 不校验,Skill 加载。"""
        d = tmp_path / "proj"
        d.mkdir()
        skill = d / "mystery"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: mystery\n"
            "description: x\n"
            "allowed-tools: [GhostTool]\n"
            "---\n",
            encoding="utf-8",
        )
        ss = bootstrap_skills(
            tmp_path,
            tool_registry=None,
            builtin_dir=None, user_dir=None, project_dir=d,
        )
        assert ss.registry.lookup("mystery") is not None


# ---- SkillSet 工厂 ----


class TestSkillSetFactory:
    def test_build_skill_filter_returns_whitelist(self, tmp_path: Path) -> None:
        from baozicode.tools.registry import ToolRegistry as ToolsReg

        ss = bootstrap_skills(tmp_path, tool_registry=ToolsReg())
        filt = ss.build_skill_filter(ToolsReg())
        assert isinstance(filt, SkillWhitelistFilter)

    def test_executor_independent_runner_none_by_default(self, tmp_path: Path) -> None:
        """默认 independent_runner=None(executor 仍可用,但独立模式会失败提示)。"""
        ss = bootstrap_skills(tmp_path, tool_registry=None)
        assert ss.executor._independent_runner is None