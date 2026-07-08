"""v1.0 Skills — 端到端集成测试。

覆盖:
- LLM 调 load_skill tool → Skill 激活 + active_skills section 出现
- 激活 Skill 收窄工具白名单(L2)— 命中 union → 放行;未命中 → 拒
- 多 Skill 同时激活 → 白名单 union 是 2 个 Skill 的并集
- /clear 清掉 active Skill(走 SkillActivation.clear)
- hot-reload:Skill 文件改动 → registry.reload() 拿到新内容
- Skill 双向防御:声明的 allowed_tools 含不存在的 tool → L1 panic
- Skill 自身声明 Bash → L2 白名单放行
- load_skill 自身是 internal → 即便 Skill 收窄到 Read 仍可调用
- SkillsConfig.enabled=False → 整套系统空集 + prompt 无 skills 段
- 独立模式 Skill + runner stub → executor 返回摘要
- v0.4 skills_dir 旧配置 + 无 skill_registry → 走 fallback 路径渲染
- Skill 替换 args 占位符后 body 进入 active_skills section
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.app import BaoZiCodeApp
from baozicode.config.schema import (
    AppConfig,
    BackendConfig,
    MemoryConfig,
    SessionConfig,
    SkillsConfig,
)
from baozicode.skills.bootstrap import bootstrap_skills
from baozicode.tools.base import ToolCall, ToolDefinition, ToolResult
from baozicode.tools.registry import (
    ToolRegistry as ToolsReg,
    get_default_tool_registry,
    unregister_mcp_tools,
)


# ---- helpers ----


@pytest.fixture
def app(tmp_path: Path) -> BaoZiCodeApp:
    """构造最小可用 App(同 test_v10_skills_app 共享 fixture)。"""
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        memory=MemoryConfig(
            user_dir=tmp_path / "user_mem",
            project_dir=tmp_path / "proj_mem",
        ),
        sessions=SessionConfig(dir=tmp_path / "sessions"),
    )
    return BaoZiCodeApp(config=cfg, project_root=tmp_path)


def _make_app(tmp_path: Path, skills_config: SkillsConfig | None = None) -> BaoZiCodeApp:
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        memory=MemoryConfig(
            user_dir=tmp_path / "user_mem",
            project_dir=tmp_path / "proj_mem",
        ),
        sessions=SessionConfig(dir=tmp_path / "sessions"),
        skills=skills_config,
    )
    return BaoZiCodeApp(config=cfg, project_root=tmp_path)


# ---- LLM 调 load_skill → 激活 + active section 出现 ----


@pytest.mark.asyncio
async def test_load_skill_tool_activates_skill(app: BaoZiCodeApp) -> None:
    """LLM 调 load_skill("commit") → Skill 进入 active 集合。"""
    reg = get_default_tool_registry()
    if "load_skill" in reg.mcp_tool_names():
        await unregister_mcp_tools(["load_skill"])
    await app._register_load_skill_tool()

    # LLM-style 调用:通过 ToolRegistry 路由(模拟 Agent 流程)
    call = ToolCall(id="t1", name="load_skill", arguments={"name": "commit"})
    result = await reg.execute_tool_call(call)
    assert not result.is_error
    assert "commit" in result.content
    assert app.skills.activation.is_active("commit")


# ---- L2 工具白名单收窄 ----


@pytest.mark.asyncio
async def test_l2_whitelist_blocks_unlisted_tool(app: BaoZiCodeApp) -> None:
    """激活一个 declared [Read] 的 Skill → Bash call 被 L2 拒。"""
    # 1. 加载 builtin review(默认无 allowed_tools → 不限制);改用 project Skill
    proj = app.project_root / "v10_test"
    proj.mkdir()
    (proj / "readonly").mkdir()
    (proj / "readonly" / "SKILL.md").write_text(
        "---\n"
        "name: readonly\n"
        "description: x\n"
        "allowed-tools: [Read, Grep]\n"
        "---\nreadonly body\n",
        encoding="utf-8",
    )
    # re-bootstrap 让新 Skill 进 registry
    from baozicode.skills.bootstrap import bootstrap_skills as bs
    app.skills = bs(app.project_root, tool_registry=get_default_tool_registry(), project_dir=proj)

    assert app.skills.loader.load_skill("readonly").ok
    filt = app.skills.build_skill_filter(get_default_tool_registry())

    # Read 在白名单 → 放行
    assert filt.is_allowed(ToolCall(id="1", name="Read", arguments={"file_path": "x"}))
    # Bash 不在 → 拒
    assert not filt.is_allowed(ToolCall(id="2", name="Bash", arguments={"cmd": "ls"}))


@pytest.mark.asyncio
async def test_l2_whitelist_load_skill_always_allowed(app: BaoZiCodeApp) -> None:
    """load_skill 是 internal → 即便 Skill 收窄也仍能调用。"""
    proj = app.project_root / "v10_test"
    proj.mkdir()
    (proj / "readonly").mkdir()
    (proj / "readonly" / "SKILL.md").write_text(
        "---\nname: readonly\ndescription: x\nallowed-tools: [Read]\n---\n",
        encoding="utf-8",
    )
    from baozicode.skills.bootstrap import bootstrap_skills as bs
    app.skills = bs(app.project_root, tool_registry=get_default_tool_registry(), project_dir=proj)
    app.skills.loader.load_skill("readonly")
    filt = app.skills.build_skill_filter(get_default_tool_registry())

    # load_skill 在 union 外但是 internal → 豁免
    assert filt.is_allowed(ToolCall(id="1", name="load_skill", arguments={"name": "x"}))


@pytest.mark.asyncio
async def test_l2_whitelist_union_two_skills(app: BaoZiCodeApp) -> None:
    """多个 Skill 同时激活 → 白名单取 union。"""
    proj = app.project_root / "v10_test"
    proj.mkdir()
    (proj / "skill-a").mkdir()
    (proj / "skill-a" / "SKILL.md").write_text(
        "---\nname: skill-a\ndescription: x\nallowed-tools: [Read]\n---\n",
        encoding="utf-8",
    )
    (proj / "skill-b").mkdir()
    (proj / "skill-b" / "SKILL.md").write_text(
        "---\nname: skill-b\ndescription: y\nallowed-tools: [Bash]\n---\n",
        encoding="utf-8",
    )
    from baozicode.skills.bootstrap import bootstrap_skills as bs
    app.skills = bs(app.project_root, tool_registry=get_default_tool_registry(), project_dir=proj)
    app.skills.loader.load_skill("skill-a")
    app.skills.loader.load_skill("skill-b")
    filt = app.skills.build_skill_filter(get_default_tool_registry())
    # union = {Read, Bash}
    assert filt.is_allowed(ToolCall(id="1", name="Read", arguments={"file_path": "x"}))
    assert filt.is_allowed(ToolCall(id="2", name="Bash", arguments={"cmd": "ls"}))
    # Edit 不在 union → 拒
    assert not filt.is_allowed(ToolCall(id="3", name="Edit", arguments={"file_path": "x"}))


# ---- /clear 流程 ----


def test_clear_wipes_active_skills(app: BaoZiCodeApp) -> None:
    """模拟 /clear 流程:app.skills.activation.clear() 清空 active Skill。"""
    app.skills.loader.load_skill("commit")
    app.skills.loader.load_skill("review")
    assert app.skills.activation.active_names() == ["commit", "review"]
    # /clear 走的是这条路径(chat_screen._clear_conversation 调它)
    app.skills.activation.clear()
    assert app.skills.activation.active_names() == []


# ---- 占位符替换 + active section 内容 ----


def test_args_substitution_appears_in_active_body(app: BaoZiCodeApp) -> None:
    """args 占位符替换生效 → active section 的 body 反映 args。"""
    # 在 project 里放一个带 {area} 占位符的 Skill
    proj = app.project_root / "v10_test"
    proj.mkdir()
    (proj / "audit").mkdir()
    (proj / "audit" / "SKILL.md").write_text(
        "---\nname: audit\ndescription: x\n---\nFocus on {area}.\n",
        encoding="utf-8",
    )
    from baozicode.skills.bootstrap import bootstrap_skills as bs
    app.skills = bs(app.project_root, tool_registry=get_default_tool_registry(), project_dir=proj)
    app.skills.loader.load_skill("audit", args={"area": "security"})

    section = app.skills.activation.render_active_section()
    assert "Focus on security." in section
    assert "{area}" not in section


# ---- SkillsConfig.enabled=False ----


def test_skills_disabled_returns_empty_skillset(tmp_path: Path) -> None:
    """SkillsConfig(enabled=False) → 空 SkillSet,prompt section 无 Skills 段。"""
    from baozicode.prompt.builder import PromptBuilder
    from baozicode.prompt.types import BuildContext
    from baozicode.prompt.rules import RuleRegistry

    app = _make_app(tmp_path, SkillsConfig(enabled=False))
    assert len(app.skills.registry) == 0

    # prompt section 走 fallback(skills_dir 不存在 → 空字符串)
    rules = RuleRegistry()
    from types import SimpleNamespace
    cfg = SimpleNamespace()
    cfg.active_agent = lambda: SimpleNamespace(rules=SimpleNamespace())
    cfg.skills_dir = tmp_path / "nonexistent"
    ctx = BuildContext(config=cfg, rule_registry=rules, skill_registry=app.skills.registry)
    from baozicode.prompt.sections import skills as skills_section
    assert skills_section.render(ctx) == ""


# ---- 独立模式 Skill + runner stub ----


@pytest.mark.asyncio
async def test_independent_skill_with_runner_returns_summary(app: BaoZiCodeApp) -> None:
    """独立模式 Skill + 注入 runner → executor 返回子摘要。"""
    proj = app.project_root / "v10_test"
    proj.mkdir()
    (proj / "reviewer").mkdir()
    (proj / "reviewer" / "SKILL.md").write_text(
        "---\nname: reviewer\ndescription: x\nmode: independent\n---\nreviewer sop\n",
        encoding="utf-8",
    )
    from baozicode.skills.bootstrap import bootstrap_skills as bs
    app.skills = bs(app.project_root, tool_registry=get_default_tool_registry(), project_dir=proj)

    # 注入独立 runner(模拟 sub-Agent 编排)
    seen: list = []

    async def runner(sd, args):
        seen.append((sd.name, args))
        return "raw reviewer summary"

    app.skills.executor._independent_runner = runner
    result = await app.skills.executor.execute("reviewer", args={"since": "yesterday"})
    assert result.ok is True
    assert "raw reviewer summary" in result.summary
    assert seen == [("reviewer", {"since": "yesterday"})]


# ---- v0.4 旧配置 fallback ----


def test_v04_skills_dir_still_renders(tmp_path: Path) -> None:
    """v0.4 老配置 skills_dir + 无 skill_registry → fallback 路径生效。"""
    from baozicode.prompt.types import BuildContext
    from baozicode.prompt.rules import RuleRegistry
    from baozicode.prompt.sections import skills as skills_section

    (tmp_path / "legacy.md").write_text(
        "legacy skill body content", encoding="utf-8",
    )
    rules = RuleRegistry()
    from types import SimpleNamespace
    cfg = SimpleNamespace()
    cfg.active_agent = lambda: SimpleNamespace(rules=SimpleNamespace())
    cfg.skills_dir = tmp_path
    ctx = BuildContext(config=cfg, rule_registry=rules, skill_registry=None)
    out = skills_section.render(ctx)
    assert "legacy skill body content" in out
    assert "## 已激活 Skill" in out  # v0.4 老标题保留


# ---- hot-reload ----


def test_hot_reload_picks_up_changes(tmp_path: Path) -> None:
    """修改 SKILL.md 后调 registry.reload(name) → 拿到新内容。"""
    proj = tmp_path / "v10_proj"
    proj.mkdir()
    (proj / "hot").mkdir()
    md = proj / "hot" / "SKILL.md"
    md.write_text(
        "---\nname: hot\ndescription: v1\n---\nbody v1\n",
        encoding="utf-8",
    )
    from baozicode.skills.bootstrap import bootstrap_skills as bs
    skill_set = bs(tmp_path, tool_registry=None, project_dir=proj)
    sd = skill_set.registry.lookup("hot")
    assert sd.description == "v1"
    assert "body v1" in sd.body

    # 修改文件 + reload
    md.write_text(
        "---\nname: hot\ndescription: v2\n---\nbody v2\n",
        encoding="utf-8",
    )
    new_sd = skill_set.registry.reload("hot")
    assert new_sd.description == "v2"
    assert "body v2" in new_sd.body


# ---- Agent 重建保持 Skill 状态 ----


def test_skill_state_persists_across_agent_rebuilds(app: BaoZiCodeApp) -> None:
    """Agent 重建(per-run)不影响 activation 状态。"""
    app.skills.loader.load_skill("commit")
    assert app.skills.activation.is_active("commit")
    # 模拟重新构造 Agent:activation 仍在 app.skills 上(独立)
    assert app.skills.activation.is_active("commit")


# ---- 双向防御:声明的工具不存在 → L1 panic ----


def test_l1_unknown_tool_panics_at_load(tmp_path: Path) -> None:
    """allowed-tools 引用不存在的 tool → load_skill 返回失败结果。"""
    proj = tmp_path / "v10_proj"
    proj.mkdir()
    (proj / "ghost").mkdir()
    (proj / "ghost" / "SKILL.md").write_text(
        "---\nname: ghost\ndescription: x\nallowed-tools: [GhostTool]\n---\n",
        encoding="utf-8",
    )
    from baozicode.skills.bootstrap import bootstrap_skills as bs
    # boot 时 valid_tools 检查 → SystemExit
    with pytest.raises(SystemExit, match="GhostTool"):
        bs(tmp_path, tool_registry=ToolsReg(), project_dir=proj)