"""McpClientManager 单元测试。

每个测试启一个 in-process Python 子进程当 fake MCP server:
- handle_initialize / handle_tools_list / handle_tools_call
- 通过 stdio 帧通信

覆盖:
- 启动:多 server 并发,失败降级
- 工具调用:成功 / broken 状态
- 重连
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest

from baozicode.config.schema import (
    AppConfig,
    McpServerHttpConfig,
    McpServerStdioConfig,
)
from baozicode.mcp.manager import McpClientManager
from baozicode.tools import registry as tool_registry


# ---- fake MCP server 脚本模板 ----
# 每个 server 自己定义 tools list,通过环境变量传给子进程
FAKE_SERVER_TEMPLATE = textwrap.dedent(
    """
    import json
    import os
    import sys

    # TOOLS_JSON 由 caller 通过 env 注入,描述这个 server 暴露的工具
    tools = json.loads(os.environ.get("MCP_TOOLS_JSON", "[]"))
    behavior = os.environ.get("MCP_BEHAVIOR", "normal")  # normal|hang_init|fail_call

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        req_id = msg.get("id")
        method = msg.get("method")
        if req_id is None:
            continue  # 通知,不响应
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "fake"},
                    "capabilities": {},
                },
            }
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        elif method == "tools/call":
            if behavior == "fail_call":
                resp = {"jsonrpc": "2.0", "id": req_id,
                        "error": {"code": -32603, "message": "fake failure"}}
            else:
                call_name = msg.get("params", {}).get("name", "?")
                known = {t["name"] for t in tools}
                if call_name not in known:
                    resp = {"jsonrpc": "2.0", "id": req_id,
                            "error": {"code": -32602,
                                      "message": f"unknown tool: {call_name}"}}
                else:
                    resp = {"jsonrpc": "2.0", "id": req_id,
                            "result": {"content": [{"type": "text",
                                                    "text": f"called {call_name}"}],
                                       "isError": False}}
        else:
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": "Method not found"}}
        print(json.dumps(resp), flush=True)
    """
).strip()


def _write_fake_server(tmp_path: Path, tools: list[dict], behavior: str = "normal") -> str:
    script = tmp_path / f"fake_{behavior}.py"
    import json as _json
    script.write_text(FAKE_SERVER_TEMPLATE, encoding="utf-8")
    return str(script)


# ---- 构造 AppConfig / McpClientManager 的小工具 ----

def _stdio_config(tmp_path: Path, *, name_tools: dict[str, list[dict]], behavior: str = "normal") -> dict:
    cfgs = {}
    for name, tools in name_tools.items():
        script = _write_fake_server(tmp_path, tools, behavior)
        cfgs[name] = McpServerStdioConfig(
            command=sys.executable,
            args=[script],
            env={"MCP_TOOLS_JSON": __import__("json").dumps(tools),
                 "MCP_BEHAVIOR": behavior},
        )
    return cfgs


def _http_config_bad() -> dict:
    """一个故意连不上的 HTTP server(端口 1)。"""
    return {"bad": McpServerHttpConfig(url="http://127.0.0.1:1/mcp", headers={})}


@pytest.fixture(autouse=True)
async def cleanup_registry():
    """每个测试后清理注册的 MCP 工具。"""
    yield
    mcp_names = tool_registry.mcp_tool_names()
    if mcp_names:
        await tool_registry.unregister_mcp_tools(mcp_names)


class TestBootstrap:
    async def test_no_servers_configured(self) -> None:
        m = McpClientManager({})
        await m.bootstrap()
        assert m.states == {}

    async def test_single_server_connects_and_registers_tools(self, tmp_path) -> None:
        tools = [{"name": "echo", "description": "echo",
                  "inputSchema": {"type": "object",
                                  "properties": {"text": {"type": "string"}}}}]
        cfgs = _stdio_config(tmp_path, name_tools={"fs": tools})
        m = McpClientManager(cfgs)
        await m.bootstrap()
        assert "fs" in m.states
        assert m.states["fs"].status == "connected"
        assert len(m.states["fs"].tools) == 1
        # 工具注册到全局 registry
        assert "mcp__fs__echo" in tool_registry.mcp_tool_names()

    async def test_two_servers_one_fails_other_succeeds(self, tmp_path) -> None:
        cfgs = _stdio_config(tmp_path, name_tools={"good": [
            {"name": "ping", "description": "ping", "inputSchema": {"type": "object"}},
        ]})
        cfgs["bad"] = McpServerHttpConfig(url="http://127.0.0.1:1/mcp", headers={})
        m = McpClientManager(cfgs)
        await m.bootstrap()
        assert m.states["good"].status == "connected"
        assert m.states["bad"].status == "failed"
        assert m.states["bad"].error  # 有错误信息
        # 只有 good 的工具被注册
        assert "mcp__good__ping" in tool_registry.mcp_tool_names()
        # bad 没注册
        for n in tool_registry.mcp_tool_names():
            assert not n.startswith("mcp__bad__")


class TestInvokeTool:
    async def test_invoke_routes_to_session(self, tmp_path) -> None:
        cfgs = _stdio_config(tmp_path, name_tools={"fs": [
            {"name": "echo", "description": "echo", "inputSchema": {"type": "object"}},
        ]})
        m = McpClientManager(cfgs)
        await m.bootstrap()
        result = await m.invoke_tool("mcp__fs__echo", {"text": "hi"})
        assert not result.is_error
        assert "called echo" in result.content

    async def test_invoke_unknown_tool_returns_error(self, tmp_path) -> None:
        cfgs = _stdio_config(tmp_path, name_tools={"fs": [
            {"name": "echo", "description": "x", "inputSchema": {"type": "object"}},
        ]})
        m = McpClientManager(cfgs)
        await m.bootstrap()
        result = await m.invoke_tool("mcp__fs__missing", {})
        assert result.is_error
        # server 暴露 echo 而不是 missing,server 应把错误传回

    async def test_invoke_on_broken_server_returns_error(self, tmp_path) -> None:
        # Server 启动后,模拟断开 — 通过让 fake server 失败 call
        cfgs = _stdio_config(tmp_path, name_tools={"fs": [
            {"name": "x", "description": "x", "inputSchema": {"type": "object"}},
        ]}, behavior="fail_call")
        m = McpClientManager(cfgs)
        await m.bootstrap()
        # call 触发 fail_call behavior,但 session 仍 connected(protocol error 不是连接断开)
        result = await m.invoke_tool("mcp__fs__x", {})
        # MCP 端返回 error,我们的 invoke_tool 应该把它转成 ToolResult error
        assert result.is_error
        # 但 server 还 connected(我们的 mark_broken 只在连接挂时触发)

    async def test_invoke_on_unknown_server_returns_error(self) -> None:
        m = McpClientManager({})
        await m.bootstrap()
        result = await m.invoke_tool("mcp__nope__x", {})
        assert result.is_error
        assert "unknown MCP server" in result.content

    async def test_invoke_with_malformed_name_returns_error(self) -> None:
        m = McpClientManager({})
        await m.bootstrap()
        result = await m.invoke_tool("Read", {})
        assert result.is_error
        assert "not an MCP tool" in result.content


class TestReconnect:
    async def test_reconnect_failed_server(self, tmp_path) -> None:
        cfgs = _http_config_bad()  # 启动会 fail
        m = McpClientManager(cfgs)
        await m.bootstrap()
        assert m.states["bad"].status == "failed"

        # 换成 stdio 可以连的
        cfgs2 = _stdio_config(tmp_path, name_tools={"bad": [
            {"name": "echo", "description": "x", "inputSchema": {"type": "object"}},
        ]})
        # 替换 config 然后 reconnect
        m._configs["bad"] = cfgs2["bad"]
        state = await m.reconnect("bad")
        assert state.status == "connected"
        assert "mcp__bad__echo" in tool_registry.mcp_tool_names()


class TestShutdown:
    async def test_shutdown_clears_state_and_tools(self, tmp_path) -> None:
        cfgs = _stdio_config(tmp_path, name_tools={"fs": [
            {"name": "x", "description": "x", "inputSchema": {"type": "object"}},
        ]})
        m = McpClientManager(cfgs)
        await m.bootstrap()
        assert "mcp__fs__x" in tool_registry.mcp_tool_names()
        await m.shutdown()
        assert "mcp__fs__x" not in tool_registry.mcp_tool_names()
        assert m.states["fs"].status == "broken"
