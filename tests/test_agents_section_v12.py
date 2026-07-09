"""v1.2 PromptSection `agents` — 两阶段加载「可用 SubAgent 列表」单元测试。

覆盖:
- agent_registry=None → render 返回空字符串
- 全 hidden registry → render 返回空字符串
- 1 个普通 agent → render 含 name + description + tools + source
- 多个 agent → render 按 list_visible 顺序枚举
- tools=None / tools_deny=None / tools=[] 三态展示
- 与 skills section 风格一致(都基于 list_visible + lookup)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from baozicode.prompt.sections.agents import render
from baozicode.prompt.types import BuildContext


# ---- helpers ----


@dataclass
class _StubFm:
    name: str = ""
    description: str = ""
    tools: list[str] | None = None
    tools_deny: list[str] | None = None
    model: str | None = None
    max_iterations: int = 20
    permission_mode: str | None = None
    nesting_depth: int = 0
    hidden: bool = False


@dataclass
class _StubAgentDef:
    frontmatter: _StubFm
    body: str = ""
    source: str = "builtin"
    path: Any = None


@dataclass
class _StubRegistry:
    agents: dict[str, _StubAgentDef]
    visible: list[tuple[str, str, str]] = field(default_factory=list)

    def list_visible(self) -> list[tuple[str, str, str]]:
        return self.visible

    def lookup(self, name: str) -> _StubAgentDef | None:
        return self.agents.get(name)


def _make_ctx(*, agent_registry: Any = None) -> BuildContext:
    """构造一个最小 BuildContext,只 agent_registry 字段是关心的。"""
    return BuildContext(
        config=type("Cfg", (), {})(),  # type: ignore[arg-type,return-value]
        rule_registry=type(
            "R", (), {"rules": ()}
        )(),  # type: ignore[arg-type,return-value]
        plan_mode=False,
        cwd=".",
        os_name="linux",
        python_version="3.11",
        git_branch="",
        git_commit="",
        project_name="x",
        now_iso="",
        instructions_text="",
        memory_index_user=None,
        memory_index_project=None,
        skill_registry=None,
        agent_registry=agent_registry,
    )


def _make_fm(**kw) -> _StubFm:
    return _StubFm(**kw)


# ---- 测试 ----


class TestAgentsSectionNoneRegistry:
    def test_none_registry_returns_empty(self) -> None:
        ctx = _make_ctx(agent_registry=None)
        assert render(ctx) == ""

    def test_empty_visible_returns_empty(self) -> None:
        """registry 接了但 list_visible 是空 → 跳过 section(节省 token)。"""
        reg = _StubRegistry(agents={}, visible=[])
        ctx = _make_ctx(agent_registry=reg)
        assert render(ctx) == ""


class TestAgentsSectionSingleAgent:
    def test_minimal_agent_visible(self) -> None:
        fm = _make_fm(
            name="explorer", description="只读探索仓库",
        )
        agent = _StubAgentDef(frontmatter=fm, body="long body...")
        reg = _StubRegistry(
            agents={"explorer": agent},
            visible=[("explorer", fm.description, "builtin")],
        )
        ctx = _make_ctx(agent_registry=reg)
        text = render(ctx)
        assert "## 可用 SubAgent(两阶段加载)" in text
        assert "explorer" in text
        assert "只读探索仓库" in text
        assert "builtin" in text
        # body 不应该出现(两阶段加载)
        assert "long body" not in text
        # tools 全允许时显式列
        assert "tools=全允许" in text

    def test_tools_whitelist_explicit(self) -> None:
        fm = _make_fm(
            name="reviewer", description="审查代码",
            tools=["Read", "Grep"],
        )
        agent = _StubAgentDef(frontmatter=fm)
        reg = _StubRegistry(
            agents={"reviewer": agent},
            visible=[("reviewer", "审查代码", "user")],
        )
        text = render(_make_ctx(agent_registry=reg))
        assert "tools=['Read', 'Grep']" in text or 'tools=[Read, Grep]' in text

    def test_tools_deny_only(self) -> None:
        fm = _make_fm(
            name="writer", description="Writes", tools_deny=["Bash"],
        )
        agent = _StubAgentDef(frontmatter=fm)
        reg = _StubRegistry(
            agents={"writer": agent},
            visible=[("writer", "Writes", "project")],
        )
        text = render(_make_ctx(agent_registry=reg))
        assert "禁止 ['Bash']" in text or "禁止 [Bash]" in text

    def test_model_and_mode_annotated(self) -> None:
        fm = _make_fm(
            name="summarizer", description="S",
            model="haiku", permission_mode="permissive",
            max_iterations=8,
        )
        agent = _StubAgentDef(frontmatter=fm)
        reg = _StubRegistry(
            agents={"summarizer": agent},
            visible=[("summarizer", "S", "builtin")],
        )
        text = render(_make_ctx(agent_registry=reg))
        assert "model=haiku" in text
        assert "mode=permissive" in text
        assert "max_iter=8" in text

    def test_default_max_iterations_omitted(self) -> None:
        fm = _make_fm(name="x", description="d", max_iterations=20)
        agent = _StubAgentDef(frontmatter=fm)
        reg = _StubRegistry(
            agents={"x": agent},
            visible=[("x", "d", "builtin")],
        )
        text = render(_make_ctx(agent_registry=reg))
        # max_iter 默认 20 省略以省 token
        assert "max_iter" not in text

    def test_meta_with_only_model(self) -> None:
        fm = _make_fm(name="x", description="d", model="opus")
        agent = _StubAgentDef(frontmatter=fm)
        reg = _StubRegistry(
            agents={"x": agent},
            visible=[("x", "d", "builtin")],
        )
        text = render(_make_ctx(agent_registry=reg))
        assert "[model=opus]" in text


class TestAgentsSectionMultipleAgents:
    def test_renders_each_visible(self) -> None:
        agents = {
            "a": _StubAgentDef(frontmatter=_make_fm(name="a", description="A")),
            "b": _StubAgentDef(frontmatter=_make_fm(name="b", description="B")),
            "c": _StubAgentDef(frontmatter=_make_fm(name="c", description="C")),
        }
        reg = _StubRegistry(
            agents=agents,
            visible=[
                ("a", "A", "builtin"),
                ("b", "B", "user"),
                ("c", "C", "project"),
            ],
        )
        text = render(_make_ctx(agent_registry=reg))
        # 顺序保留
        assert text.find("`a`") < text.find("`b`") < text.find("`c`")

    def test_lookup_failure_skips_silently(self) -> None:
        """visible 里列了名,但 lookup 返回 None → 跳过,不崩。"""
        reg = _StubRegistry(
            agents={},
            visible=[("ghost", "描述", "plugin")],
        )
        text = render(_make_ctx(agent_registry=reg))
        # ghost 应该不出现(lookup 失败跳过)
        assert "ghost" not in text or "## 可用 SubAgent" in text and text.count("`") == 0


class TestAgentsSectionToolRouting:
    def test_section_mentions_task_tool(self) -> None:
        """spec 要求 section 提示 LLM 调 task 工具(派任务入口)。"""
        fm = _make_fm(name="x", description="d")
        agent = _StubAgentDef(frontmatter=fm)
        reg = _StubRegistry(
            agents={"x": agent},
            visible=[("x", "d", "builtin")],
        )
        text = render(_make_ctx(agent_registry=reg))
        assert "task(" in text
        # 两条路径都有提及
        assert 'type="definition"' in text or 'type=\\"definition\\"' in text or "type=\"definition\"" in text
        assert "fork" in text


class TestAgentsSectionNotInOptionalExclusion:
    def test_section_in_section_list(self) -> None:
        """`_OPTIONAL_SECTIONS` 包含 agents,否则 PromptBuilder 不会调它。"""
        from baozicode.prompt.builder import _OPTIONAL_SECTIONS
        from baozicode.prompt.sections import agents as agents_section
        assert agents_section in _OPTIONAL_SECTIONS
