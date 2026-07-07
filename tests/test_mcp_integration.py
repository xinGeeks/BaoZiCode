"""v0.6 MCP 集成测试 — App 启动 → MCP bootstrap → Agent 看见 MCP 工具。

不启动 TUI;直接构造 App、跑 bootstrap、把 manager 注入到 Agent 的 tools 路径上,
验证从 tool 发现到 LLM 可见的全链路。
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from baozicode.app import BaoZiCodeApp
from baozicode.config.schema import (
    AgentConfig,
    AppConfig,
    BackendConfig,
    McpServerStdioConfig,
    PermissionsV5,
)
from baozicode.tools import registry as tool_registry


FAKE_SERVER_TEMPLATE = textwrap.dedent(
    """
    import json, os, sys
    tools = json.loads(os.environ.get("MCP_TOOLS_JSON", "[]"))
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try: msg = json.loads(line)
        except json.JSONDecodeError: continue
        req_id = msg.get("id"); method = msg.get("method")
        if req_id is None: continue
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "result": {"protocolVersion": "2025-11-25",
                               "serverInfo": {"name": "fake"},
                               "capabilities": {}}}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "result": {"tools": tools}}
        elif method == "tools/call":
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text",
                                            "text": f"ok {msg.get('params', {}).get('name')}"}],
                               "isError": False}}
        else:
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": "Method not found"}}
        print(json.dumps(resp), flush=True)
    """
).strip()


def _fake_server_path(tmp_path: Path, suffix: str = "fake.py") -> str:
    p = tmp_path / suffix
    p.write_text(FAKE_SERVER_TEMPLATE, encoding="utf-8")
    return str(p)


def _minimal_config(mcp_servers: dict | None = None) -> AppConfig:
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="anthropic-test", model="claude-test"),
        openai=BackendConfig(api_key="openai-test", model="gpt-test"),
        minimax=BackendConfig(api_key="minimax-test", model="abab-test"),
        deepseek=BackendConfig(api_key="deepseek-test", model="deepseek-test"),
        agent=AgentConfig(),
        permissions_v5=PermissionsV5(),
        mcp_servers=mcp_servers or {},
    )


@pytest.fixture(autouse=True)
async def cleanup_registry():
    yield
    names = tool_registry.mcp_tool_names()
    if names:
        await tool_registry.unregister_mcp_tools(names)


class TestAppLevelBootstrap:
    async def test_app_with_mcp_manager_injects_tools_into_agent(self, tmp_path) -> None:
        """App 接受预 bootstrap 的 manager,Agent 通过 registry 看到 MCP 工具。"""
        server_script = _fake_server_path(tmp_path)
        mcp_servers = {
            "fs": McpServerStdioConfig(
                command=sys.executable,
                args=[server_script],
                env={"MCP_TOOLS_JSON": '[{"name":"echo","description":"echo",'
                                       '"inputSchema":{"type":"object"}}]'},
            ),
        }
        config = _minimal_config(mcp_servers)

        from baozicode.mcp import bootstrap as mcp_bootstrap

        manager = await mcp_bootstrap(config)
        try:
            assert manager.states["fs"].status == "connected"

            # 构造 App(注入 manager)—— 不 mount TUI
            app = BaoZiCodeApp(config, mcp_manager=manager)
            assert app.mcp_manager is manager

            # _get_tools 通过 registry 自然看到 MCP 工具
            tools = tool_registry.get_all_tools()
            tool_names = {t.name for t in tools}
            assert "mcp__fs__echo" in tool_names
            # 内置工具还在
            assert "Read" in tool_names
        finally:
            await manager.shutdown()

    async def test_app_without_mcp_servers_has_no_mcp_tools(self) -> None:
        """没配 MCP 时,manager 是 None,registry 里没有 mcp__ 工具。"""
        config = _minimal_config()
        app = BaoZiCodeApp(config, mcp_manager=None)
        assert app.mcp_manager is None
        assert tool_registry.mcp_tool_names() == []

    async def test_cli_bootstrap_failure_does_not_block_app_start(self, tmp_path) -> None:
        """CLI 收到一个连不上的 server → manager 标记 failed,App 仍能构造。"""
        bad_servers = {
            "bad": McpServerStdioConfig(
                command="nonexistent_command_xyz",
                args=[],
                env={},
                startup_total_timeout_s=1.0,
            ),
        }
        config = _minimal_config(bad_servers)

        from baozicode.mcp import bootstrap as mcp_bootstrap

        manager = await mcp_bootstrap(config)
        assert manager.states["bad"].status == "failed"
        # App 仍能拿到 manager(state=failed)
        app = BaoZiCodeApp(config, mcp_manager=manager)
        assert app.mcp_manager is not None
        assert app.mcp_manager.states["bad"].status == "failed"


class TestAgentSeesMcpTools:
    async def test_agent_tool_list_includes_mcp(self, tmp_path) -> None:
        """端到端:App 启动 → MCP bootstrap → Agent.tools 含 MCP 工具。"""
        server_script = _fake_server_path(tmp_path)
        mcp_servers = {
            "fs": McpServerStdioConfig(
                command=sys.executable,
                args=[server_script],
                env={"MCP_TOOLS_JSON": '[{"name":"list","description":"list",'
                                       '"inputSchema":{"type":"object"}}]'},
            ),
        }
        config = _minimal_config(mcp_servers)

        from baozicode.mcp import bootstrap as mcp_bootstrap

        manager = await mcp_bootstrap(config)
        try:
            app = BaoZiCodeApp(config, mcp_manager=manager)

            # 模拟 chat_screen._get_tools 的逻辑(直接调 registry)
            tools = tool_registry.get_all_tools()
            assert any(t.name == "mcp__fs__list" for t in tools)

            # 调用 MCP 工具验证端到端可达
            call = tool_registry.execute_tool_call
            from baozicode.tools.base import ToolCall

            result = await call(ToolCall(id="t1", name="mcp__fs__list", arguments={}))
            assert not result.is_error
            assert "ok list" in result.content
        finally:
            await manager.shutdown()