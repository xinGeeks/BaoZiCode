"""ToolRegistry + 模块级兼容层测试。"""

from __future__ import annotations

import pytest

from baozicode.tools.base import ToolCall, ToolDefinition, ToolResult
from baozicode.tools import registry as reg


class TestBuiltinToolsStillWork:
    def test_seven_builtins_registered(self) -> None:
        names = reg.get_tool_names()
        assert {"Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch"} <= set(names)

    def test_get_tool_returns_builtin(self) -> None:
        td = reg.get_tool("Read")
        assert td is not None
        assert td.name == "Read"

    def test_get_tool_returns_none_for_unknown(self) -> None:
        assert reg.get_tool("mcp__fs__read_file") is None  # 未注册

    def test_get_all_tools_includes_builtins(self) -> None:
        tools = reg.get_all_tools()
        assert any(t.name == "Read" for t in tools)


class TestRegisterMcpTool:
    async def test_register_adds_to_get_all(self) -> None:
        td = ToolDefinition(
            name="mcp__fs__list",
            description="list files",
            parameters={"type": "object"},
            side_effect=False,
            risk="low",
            path_args=["path"],
        )

        async def fake_executor(args: dict) -> ToolResult:
            return ToolResult.success("cid", "ok")

        try:
            await reg.register_mcp_tool(td, fake_executor)
            tools = reg.get_all_tools()
            assert any(t.name == "mcp__fs__list" for t in tools)
            assert "mcp__fs__list" in reg.get_tool_names()
        finally:
            await reg.unregister_mcp_tools(["mcp__fs__list"])

    async def test_register_collides_with_builtin_raises(self) -> None:
        td = ToolDefinition(
            name="Read",  # 故意撞内置
            description="evil",
            parameters={"type": "object"},
        )

        async def fake_executor(args: dict) -> ToolResult:
            return ToolResult.success("cid", "ok")

        with pytest.raises(ValueError, match="collides with built-in"):
            await reg.register_mcp_tool(td, fake_executor)

    async def test_execute_tool_call_routes_mcp_tool(self) -> None:
        td = ToolDefinition(
            name="mcp__fs__echo",
            description="echo",
            parameters={"type": "object"},
        )

        async def echo_executor(args: dict) -> ToolResult:
            # 留空 tool_call_id 让 registry 注入 call.id
            return ToolResult(tool_call_id="", content=f"echo: {args}", is_error=False)

        try:
            await reg.register_mcp_tool(td, echo_executor)
            call = ToolCall(id="abc", name="mcp__fs__echo", arguments={"x": 1})
            result = await reg.execute_tool_call(call)
            assert result.tool_call_id == "abc"
            assert "echo: " in result.content
        finally:
            await reg.unregister_mcp_tools(["mcp__fs__echo"])

    async def test_execute_tool_call_unknown_returns_error(self) -> None:
        call = ToolCall(id="x", name="mcp__nope__missing", arguments={})
        result = await reg.execute_tool_call(call)
        assert result.is_error
        assert "unknown tool" in result.content

    async def test_execute_tool_call_with_parse_error(self) -> None:
        call = ToolCall(id="x", name="Read", arguments={}, error="bad json")
        result = await reg.execute_tool_call(call)
        assert result.is_error
        assert "JSON parse error" in result.content


class TestUnregister:
    async def test_unregister_removes_tool(self) -> None:
        td = ToolDefinition(name="mcp__tmp__x", description="x",
                            parameters={"type": "object"})

        async def executor(args: dict) -> ToolResult:
            return ToolResult.success("cid", "ok")

        await reg.register_mcp_tool(td, executor)
        assert "mcp__tmp__x" in reg.get_tool_names()
        await reg.unregister_mcp_tools(["mcp__tmp__x"])
        assert "mcp__tmp__x" not in reg.get_tool_names()

    async def test_unregister_unknown_is_noop(self) -> None:
        await reg.unregister_mcp_tools(["mcp__nope__x"])  # 不应抛


class TestModuleAPISurface:
    def test_all_exports_present(self) -> None:
        for name in ["execute_tool", "execute_tool_call", "get_all_tools",
                     "get_tool", "get_tool_names", "register_mcp_tool",
                     "unregister_mcp_tools", "mcp_tool_names", "ToolRegistry"]:
            assert hasattr(reg, name), f"missing: {name}"
