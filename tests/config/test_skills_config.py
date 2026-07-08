"""v1.0 Skills — `SkillsConfig` + prompt section 兼容测试。

覆盖:
- SkillsConfig Pydantic 字段默认值
- SkillsConfig 字段覆盖(builtin_dir / user_dir / project_dir)
- enabled=False → bootstrap_skills 返回空 SkillSet
- skills_config 提供 dir 覆盖 → bootstrap 用覆盖值
- prompt section 在 skill_registry=None 时走 v0.4 回退路径
- prompt section 在 skill_registry 提供时走 v1.0 两阶段列表
- v0.4 skills_dir 旧配置仍可工作(无破坏性)
- AppConfig.skills 字段 Optional + 默认 None
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.config.schema import AppConfig, SkillsConfig
from baozicode.prompt.builder import PromptBuilder
from baozicode.prompt.rules import RuleRegistry
from baozicode.prompt.sections import skills as skills_section
from baozicode.prompt.types import BuildContext
from baozicode.skills.bootstrap import bootstrap_skills


# ---- SkillsConfig Pydantic ----


class TestSkillsConfigSchema:
    def test_default_all_optional(self) -> None:
        """默认所有 dir / model / history_bubbles 字段都是 None / 0。"""
        sc = SkillsConfig()
        assert sc.enabled is True
        assert sc.builtin_dir is None
        assert sc.user_dir is None
        assert sc.project_dir is None
        assert sc.summary_model is None
        assert sc.history_bubbles_default == 0

    def test_extra_ignored(self) -> None:
        """未声明字段 → 静默忽略(extra=ignore)。"""
        sc = SkillsConfig(unknown_field="x")  # type: ignore[call-arg]
        assert not hasattr(sc, "unknown_field")

    def test_history_bubbles_non_negative(self) -> None:
        """history_bubbles_default 不能为负。"""
        with pytest.raises(Exception):
            SkillsConfig(history_bubbles_default=-1)

    def test_disabled_explicit(self) -> None:
        sc = SkillsConfig(enabled=False)
        assert sc.enabled is False


class TestAppConfigWiring:
    def test_appconfig_default_skills_none(self) -> None:
        """AppConfig.skills 默认 None(走 bootstrap 默认)。"""
        from baozicode.config.schema import BackendConfig

        cfg = AppConfig(
            backend="anthropic",
            anthropic=BackendConfig(api_key="k", model="m"),
            openai=BackendConfig(api_key="k", model="m"),
            minimax=BackendConfig(api_key="k", model="m"),
            deepseek=BackendConfig(api_key="k", model="m"),
        )
        assert cfg.skills is None

    def test_appconfig_explicit_skills(self) -> None:
        from baozicode.config.schema import BackendConfig

        cfg = AppConfig(
            backend="anthropic",
            anthropic=BackendConfig(api_key="k", model="m"),
            openai=BackendConfig(api_key="k", model="m"),
            minimax=BackendConfig(api_key="k", model="m"),
            deepseek=BackendConfig(api_key="k", model="m"),
            skills=SkillsConfig(enabled=False),
        )
        assert cfg.skills is not None
        assert cfg.skills.enabled is False


# ---- bootstrap_skills + SkillsConfig ----


class TestBootstrapSkillsConfig:
    def test_enabled_false_returns_empty(self, tmp_path: Path) -> None:
        """SkillsConfig(enabled=False) → 空 SkillSet。"""
        from baozicode.skills.registry import SkillRegistry

        sc = SkillsConfig(enabled=False)
        ss = bootstrap_skills(tmp_path, tool_registry=None, skills_config=sc)
        assert isinstance(ss.registry, SkillRegistry)
        assert len(ss.registry) == 0

    def test_dir_override_uses_config_value(self, tmp_path: Path) -> None:
        """SkillsConfig.user_dir 覆盖 → bootstrap 从覆盖目录加载。"""
        custom_user = tmp_path / "my-user-skills"
        custom_user.mkdir()
        (custom_user / "alpha").mkdir()
        (custom_user / "alpha" / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: x\n---\nbody\n",
            encoding="utf-8",
        )
        sc = SkillsConfig(user_dir=custom_user)
        ss = bootstrap_skills(tmp_path, tool_registry=None, skills_config=sc)
        # alpha 必须出现;default ~/.config/baozicode/skills 不存在,所以没别的污染
        assert ss.registry.lookup("alpha") is not None

    def test_kwargs_override_config(self, tmp_path: Path) -> None:
        """显式 kwargs 优先于 SkillsConfig 字段。"""
        from_user_cfg = tmp_path / "from-cfg"
        from_user_cfg.mkdir()
        (from_user_cfg / "cfg-skill").mkdir()
        (from_user_cfg / "cfg-skill" / "SKILL.md").write_text(
            "---\nname: cfg-skill\ndescription: x\n---\n",
            encoding="utf-8",
        )
        from_kwarg = tmp_path / "from-kwarg"
        from_kwarg.mkdir()
        (from_kwarg / "kwarg-skill").mkdir()
        (from_kwarg / "kwarg-skill" / "SKILL.md").write_text(
            "---\nname: kwarg-skill\ndescription: y\n---\n",
            encoding="utf-8",
        )
        sc = SkillsConfig(user_dir=from_user_cfg)
        ss = bootstrap_skills(
            tmp_path, tool_registry=None,
            skills_config=sc,
            user_dir=from_kwarg,
        )
        assert ss.registry.lookup("kwarg-skill") is not None
        assert ss.registry.lookup("cfg-skill") is None


# ---- prompt section: v1.0 vs v0.4 兼容 ----


def _make_ctx(*, skill_registry=None, skills_dir=None) -> BuildContext:
    """构造一个最小 BuildContext。"""
    from types import SimpleNamespace

    rules = RuleRegistry()
    cfg = SimpleNamespace()
    cfg.active_agent = lambda: SimpleNamespace(rules=SimpleNamespace())
    if skills_dir is not None:
        cfg.skills_dir = skills_dir
    return BuildContext(config=cfg, rule_registry=rules, skill_registry=skill_registry)


class TestSkillsPromptSectionV10:
    def test_v10_section_lists_names_descriptions(self) -> None:
        """v1.0:skill_registry 提供 → section 列出 name + description + 来源。"""
        ss = bootstrap_skills(Path("/tmp"), tool_registry=None)
        ctx = _make_ctx(skill_registry=ss.registry)
        out = skills_section.render(ctx)
        assert "可用 Skill" in out
        assert "commit" in out
        assert "review" in out
        assert "test" in out
        assert "load_skill" in out  # 提示 LLM 调 load_skill 加载

    def test_v10_section_hides_hidden_skills(self) -> None:
        """hidden=True 的 Skill 不进 v1.0 prompt section。"""
        from baozicode.skills.schema import SkillDef, SkillFrontmatter

        fm = SkillFrontmatter.model_validate(
            {"name": "secret", "description": "hidden one", "hidden": True}
        )
        from baozicode.skills.registry import SkillRegistry
        reg = SkillRegistry()
        # 直接构造 def 并注入(测试用;正常路径走 scan)
        from baozicode.skills.registry import SkillRegistry as SR
        reg._defs["secret"] = SkillDef(
            frontmatter=fm, body="x", source="builtin",
            path=Path("/tmp/secret/SKILL.md"),
        )
        ctx = _make_ctx(skill_registry=reg)
        out = skills_section.render(ctx)
        assert "secret" not in out

    def test_empty_registry_returns_empty(self) -> None:
        from baozicode.skills.registry import SkillRegistry
        ctx = _make_ctx(skill_registry=SkillRegistry())
        assert skills_section.render(ctx) == ""


class TestSkillsPromptSectionV04Fallback:
    def test_v04_fallback_uses_skills_dir(self, tmp_path: Path) -> None:
        """v1.0:skill_registry=None → 退回 v0.4 旧路径(扫 skills_dir/*.md)。"""
        # 模拟 v0.4 老配置:一个 *.md 文件
        (tmp_path / "old-skill.md").write_text(
            "old skill body", encoding="utf-8",
        )
        ctx = _make_ctx(skills_dir=tmp_path)
        out = skills_section.render(ctx)
        assert "old-skill" in out
        assert "old skill body" in out
        assert "## 已激活 Skill" in out

    def test_v04_fallback_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        """v0.4:skills_dir 不存在 → 空字符串。"""
        ctx = _make_ctx(skills_dir=tmp_path / "nope")
        assert skills_section.render(ctx) == ""

    def test_v04_fallback_no_skills_dir_field(self) -> None:
        """v0.4:连 skills_dir 字段都没有 → 空字符串。"""
        ctx = _make_ctx()  # 不传 skills_dir
        assert skills_section.render(ctx) == ""


# ---- PromptBuilder.build() 透传 ----


class TestPromptBuilderSkillRegistryPassthrough:
    def test_prompt_includes_v10_section(self, tmp_path: Path) -> None:
        """PromptBuilder.build(skill_registry=...) → stable_system 含 Skill 列表。"""
        from baozicode.config.schema import BackendConfig

        ss = bootstrap_skills(tmp_path, tool_registry=None)
        cfg = AppConfig(
            backend="anthropic",
            anthropic=BackendConfig(api_key="k", model="m"),
            openai=BackendConfig(api_key="k", model="m"),
            minimax=BackendConfig(api_key="k", model="m"),
            deepseek=BackendConfig(api_key="k", model="m"),
        )
        prompt = PromptBuilder().build(
            cfg, plan_mode=False, tools=[],
            skill_registry=ss.registry,
        )
        assert "可用 Skill" in prompt.stable_system
        assert "commit" in prompt.stable_system

    def test_prompt_without_registry_skips_v10_section(self) -> None:
        """PromptBuilder.build(skill_registry=None) → 不含「可用 Skill」段。"""
        from baozicode.config.schema import BackendConfig

        cfg = AppConfig(
            backend="anthropic",
            anthropic=BackendConfig(api_key="k", model="m"),
            openai=BackendConfig(api_key="k", model="m"),
            minimax=BackendConfig(api_key="k", model="m"),
            deepseek=BackendConfig(api_key="k", model="m"),
        )
        prompt = PromptBuilder().build(cfg, plan_mode=False, tools=[])
        # skill_registry=None → v0.4 fallback(扫 skills_dir;默认不存在 → 跳过)
        assert "可用 Skill" not in prompt.stable_system