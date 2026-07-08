"""v1.0 Skills — SkillLoader + 占位符替换 + load_skill tool 单元测试。

覆盖:
- substitute_placeholders:基础 var / 默认值 / 缺值留字面 / 多占位符 / 无占位符
- SkillLoader.load_skill:成功 / 找不到 / 幂等 / 占位符生效 / 模式 + 白名单透传
- load_skill tool:ToolDefinition shape + execute() 路由
- ToolRegistry.register_tool:接受非 MCP 内部工具,与 MCP 入口共存
"""

from __future__ import annotations

import pytest

from baozicode.commands.registry import CommandRegistry
from baozicode.skills.activation import SkillActivation
from baozicode.skills.loader import (
    LOAD_SKILL_TOOL,
    LoadSkillResult,
    SkillLoader,
    substitute_placeholders,
)
from baozicode.skills.registry import SkillRegistry
from baozicode.skills.schema import SkillFrontmatter, parse_frontmatter
from baozicode.tools.base import ToolCall, ToolResult
from baozicode.tools.registry import ToolRegistry as ToolsReg


# ---- 工具:同步构造临时 SkillRegistry + SkillLoader 的 fixture ----


def _make_skill(text: str, *, name: str = "demo", body_override: str | None = None):
    """Helper:用 text 解析出 frontmatter + body,装进 min SkillRegistry。"""
    fm, body = parse_frontmatter(text)
    if body_override is not None:
        body = body_override
    sd = type(
        "SD",
        (),
        {
            "frontmatter": fm,
            "body": body,
            "name": fm.name,
            "description": fm.description,
            "mode": fm.mode,
            "allowed_tools": list(fm.allowed_tools or []),
            "history_bubbles": fm.history_bubbles,
            "model": fm.model,
            "hidden": fm.hidden,
        },
    )()
    return sd


class _StubRegistry:
    """一个能装 1 个 SkillDef 的最小 registry(避开 setup 三级目录)。"""

    def __init__(self, sd):
        self._sd = sd

    def lookup(self, name):
        if self._sd and self._sd.name == name:
            return self._sd
        return None


# ---- substitute_placeholders ----


class TestSubstitutePlaceholders:
    def test_basic_var(self) -> None:
        out = substitute_placeholders("hello {name}", {"name": "world"})
        assert out == "hello world"

    def test_default_used_when_missing(self) -> None:
        out = substitute_placeholders(
            "hello {name:anon}", {"other": "x"}
        )
        assert out == "hello anon"

    def test_explicit_overrides_default(self) -> None:
        out = substitute_placeholders(
            "hello {name:anon}", {"name": "alice"}
        )
        assert out == "hello alice"

    def test_missing_keeps_literal(self) -> None:
        out = substitute_placeholders("use {tool} first", None)
        assert out == "use {tool} first"

    def test_missing_with_default_passes(self) -> None:
        out = substitute_placeholders(
            "use {tool:bash} first", None
        )
        assert out == "use bash first"

    def test_no_placeholders_passthrough(self) -> None:
        out = substitute_placeholders("just plain text", {"x": "y"})
        assert out == "just plain text"

    def test_multi_occurrence(self) -> None:
        out = substitute_placeholders("{a}-{a}-{b}", {"a": "1", "b": "2"})
        assert out == "1-1-2"

    def test_args_value_coerced_to_string(self) -> None:
        out = substitute_placeholders("{n}", {"n": 42})  # type: ignore[dict-item]
        assert out == "42"

    def test_empty_args_same_as_none(self) -> None:
        a = substitute_placeholders("use {tool:bash}", {})
        b = substitute_placeholders("use {tool:bash}", None)
        assert a == b == "use bash"


# ---- SkillLoader.load_skill ----


class TestLoaderLoadSkill:
    def _fm_text(self, **overrides) -> str:
        base = (
            "---\n"
            "name: testskill\n"
            "description: A demo\n"
        )
        if "mode" in overrides:
            base += f"mode: {overrides['mode']}\n"
        if "tools" in overrides:
            tools = ", ".join(overrides["tools"])
            base += f"allowed-tools: [{tools}]\n"
        base += "---\n"
        body = overrides.get("body", "stub body {var:default}")
        base += body
        return base

    def _loader(self, sd) -> SkillLoader:
        reg = _StubRegistry(sd)
        cmd_reg = CommandRegistry()
        activation = SkillActivation(cmd_reg)
        return SkillLoader(reg, activation), activation, cmd_reg

    def test_success(self) -> None:
        sd = _make_skill(self._fm_text())
        loader, activation, _ = self._loader(sd)
        result = loader.load_skill("testskill")
        assert isinstance(result, LoadSkillResult)
        assert result.ok is True
        assert result.name == "testskill"
        assert activation.is_active("testskill") is True

    def test_not_found_returns_failure(self) -> None:
        loader, _, _ = self._loader(None)
        result = loader.load_skill("ghost")
        assert result.ok is False
        assert result.name == "ghost"
        assert "未找到 Skill" in result.summary

    def test_empty_name_returns_failure(self) -> None:
        loader, _, _ = self._loader(None)
        result = loader.load_skill("")
        assert result.ok is False

    def test_args_substitute_into_body(self) -> None:
        sd = _make_skill(
            self._fm_text(body="focus on {area}"),
        )
        loader, activation, _ = self._loader(sd)
        result = loader.load_skill("testskill", args={"area": "security"})
        assert result.ok is True
        entry = activation.get("testskill")
        assert entry is not None
        assert "focus on security" in entry.body

    def test_missing_var_left_literal(self) -> None:
        sd = _make_skill(self._fm_text(body="use {tool} now"))
        loader, activation, _ = self._loader(sd)
        loader.load_skill("testskill", args={})
        entry = activation.get("testskill")
        assert "{tool}" in entry.body

    def test_mode_passthrough(self) -> None:
        sd = _make_skill(self._fm_text(mode="independent"))
        loader, activation, _ = self._loader(sd)
        loader.load_skill("testskill")
        entry = activation.get("testskill")
        assert entry.mode == "independent"

    def test_allowed_tools_passthrough(self) -> None:
        sd = _make_skill(self._fm_text(tools=["Bash", "Read"]))
        loader, activation, _ = self._loader(sd)
        loader.load_skill("testskill")
        entry = activation.get("testskill")
        assert set(entry.allowed_tools) == {"Bash", "Read"}

    def test_idempotent_same_args(self) -> None:
        sd = _make_skill(self._fm_text())
        loader, _, _ = self._loader(sd)
        loader.load_skill("testskill")
        loader.load_skill("testskill")
        # 不重复抛、不重复设置(active_names 仍只 1 条)
        assert loader._activation.active_names() == ["testskill"]


# ---- LOAD_SKILL_TOOL ----


class TestLoadSkillTool:
    def test_tool_name(self) -> None:
        assert LOAD_SKILL_TOOL.name == "load_skill"

    def test_tool_marked_internal(self) -> None:
        # 白名单豁免需要 tool_type="internal"
        assert LOAD_SKILL_TOOL.tool_type == "internal"

    def test_tool_has_side_effect_false(self) -> None:
        assert LOAD_SKILL_TOOL.side_effect is False

    def test_tool_risk_low(self) -> None:
        assert LOAD_SKILL_TOOL.risk == "low"

    def test_tool_parameters_require_name(self) -> None:
        params = LOAD_SKILL_TOOL.parameters
        assert params["required"] == ["name"]
        assert "name" in params["properties"]
        assert "args" in params["properties"]


# ---- execute() ----


class TestLoaderExecute:
    def _make_loader(self) -> SkillLoader:
        sd = _make_skill(
            "---\nname: commit\ndescription: ok\n---\nuse {msg:default}",
        )
        reg = _StubRegistry(sd)
        activation = SkillActivation(CommandRegistry())
        return SkillLoader(reg, activation)

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        loader = self._make_loader()
        result = await loader.execute({"name": "commit"})
        assert isinstance(result, ToolResult)
        assert result.is_error is False
        assert "commit" in result.content

    @pytest.mark.asyncio
    async def test_success_with_args(self) -> None:
        loader = self._make_loader()
        result = await loader.execute({"name": "commit", "args": {"msg": "fix"}})
        assert "fix" in loader._activation.get("commit").body

    @pytest.mark.asyncio
    async def test_missing_name_error(self) -> None:
        loader = self._make_loader()
        result = await loader.execute({})
        assert result.is_error is True
        assert "name" in result.content

    @pytest.mark.asyncio
    async def test_args_wrong_type_error(self) -> None:
        loader = self._make_loader()
        result = await loader.execute({"name": "commit", "args": "not-a-dict"})
        assert result.is_error is True
        assert "args" in result.content

    @pytest.mark.asyncio
    async def test_ghost_skill_error(self) -> None:
        loader = self._make_loader()
        result = await loader.execute({"name": "ghost"})
        assert result.is_error is True
        assert "未找到" in result.content


# ---- ToolRegistry.register_tool(MCP / 内部工具统一入口) ----


class TestToolRegistryRegister:
    @pytest.mark.asyncio
    async def test_register_tool(self) -> None:
        reg = ToolsReg()
        tool = LOAD_SKILL_TOOL  # 已有 tool_type="internal"
        await reg.register_tool(tool, lambda args: ToolResult.success("", ""))
        assert "load_skill" in reg.get_tool_names()

    @pytest.mark.asyncio
    async def test_collision_with_builtin_rejected(self) -> None:
        from baozicode.tools.base import ToolDefinition

        reg = ToolsReg()
        with pytest.raises(ValueError, match="collides with built-in"):
            await reg.register_tool(
                ToolDefinition(name="Read", description="x", parameters={}),
                lambda args: ToolResult.success("", ""),
            )

    @pytest.mark.asyncio
    async def test_duplicate_registration_rejected(self) -> None:
        reg = ToolsReg()
        await reg.register_tool(LOAD_SKILL_TOOL, lambda args: ToolResult.success("", ""))
        with pytest.raises(ValueError, match="already registered"):
            await reg.register_tool(
                LOAD_SKILL_TOOL, lambda args: ToolResult.success("", "")
            )

    @pytest.mark.asyncio
    async def test_register_tool_routes_via_executor(self) -> None:
        reg = ToolsReg()
        seen: dict = {}

        async def executor(args):
            seen.update(args)
            return ToolResult.success("", "ok")

        await reg.register_tool(LOAD_SKILL_TOOL, executor)
        call = ToolCall(id="t1", name="load_skill", arguments={"name": "x"})
        result = await reg.execute_tool_call(call)
        assert result.content == "ok"
        assert seen == {"name": "x"}
