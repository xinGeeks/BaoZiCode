"""验证 tests/mcp/conftest.py 的 fixtures 自身能正确装配。"""

from __future__ import annotations

import pytest

from baozicode.config.schema import McpServerStdioConfig
from baozicode.mcp import bootstrap as mcp_bootstrap
from baozicode.config.schema import (
    AgentConfig, AppConfig, BackendConfig, PermissionsV5,
)
from baozicode.tools import registry as tool_registry


def _config(mcp_servers: dict) -> AppConfig:
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="a", model="m"),
        openai=BackendConfig(api_key="b", model="m"),
        minimax=BackendConfig(api_key="c", model="m"),
        deepseek=BackendConfig(api_key="d", model="m"),
        agent=AgentConfig(),
        permissions_v5=PermissionsV5(),
        mcp_servers=mcp_servers,
    )


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    names = tool_registry.mcp_tool_names()
    if names:
        await tool_registry.unregister_mcp_tools(names)


async def test_fake_stdio_server_supports_normal_behavior(fake_stdio_server, tmp_path):
    script, env = fake_stdio_server(
        tools=[{"name": "ping", "description": "ping",
                "inputSchema": {"type": "object"}}],
        behavior="normal",
    )
    import sys as _s
    cfg = {"fs": McpServerStdioConfig(
        command=_s.executable, args=[script], env=env,
    )}
    manager = await mcp_bootstrap(_config(cfg))
    try:
        assert manager.states["fs"].status == "connected"
        result = await manager.invoke_tool("mcp__fs__ping", {})
        assert not result.is_error
        assert "called ping" in result.content
    finally:
        await manager.shutdown()


async def test_fake_stdio_server_supports_fail_call(fake_stdio_server):
    script, env = fake_stdio_server(
        tools=[{"name": "x", "description": "x",
                "inputSchema": {"type": "object"}}],
        behavior="fail_call",
    )
    import sys as _s
    cfg = {"fs": McpServerStdioConfig(
        command=_s.executable, args=[script], env=env,
    )}
    manager = await mcp_bootstrap(_config(cfg))
    try:
        result = await manager.invoke_tool("mcp__fs__x", {})
        assert result.is_error
    finally:
        await manager.shutdown()


async def test_fake_http_server_returns_session_id(fake_http_server):
    from baozicode.config.schema import McpServerHttpConfig
    cfg = {"remote": McpServerHttpConfig(url=fake_http_server, headers={})}
    manager = await mcp_bootstrap(_config(cfg))
    try:
        assert manager.states["remote"].status == "connected"
        # session ID 应被 transport 捕获
        state = manager.states["remote"]
        assert state.session is not None
        assert state.session._transport._session_id == "fake-session-123"
    finally:
        await manager.shutdown()