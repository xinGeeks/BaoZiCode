"""v0.6 MCP 测试 fixtures — 共享 in-process fake server。

fake_stdio_server fixture:启动一个 Python 子进程,实现 MCP 四个核心方法,
通过 `MCP_BEHAVIOR` 环境变量切换行为(normal/hang_init/fail_call/
server_request_mid_session)。

fake_http_server fixture:用 aiohttp.web 起一个 localhost:0 HTTP server,
同协议但走 Streamable HTTP。

每个 server 自己注册要暴露的 tools,通过 env `MCP_TOOLS_JSON` 传入 JSON。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import textwrap
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio


FAKE_STDIO_TEMPLATE = textwrap.dedent(
    """
    import json, os, sys, time
    tools = json.loads(os.environ.get("MCP_TOOLS_JSON", "[]"))
    behavior = os.environ.get("MCP_BEHAVIOR", "normal")
    call_delay_s = float(os.environ.get("MCP_CALL_DELAY_S", "0"))

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
            # notification — 不响应
            continue

        if method == "initialize":
            if behavior == "hang_init":
                time.sleep(30)  # 触发 caller 端超时
                continue
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "fake-stdio", "version": "0.6.0"},
                    "capabilities": {},
                },
            }
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "result": {"tools": tools}}
        elif method == "tools/call":
            if call_delay_s > 0:
                time.sleep(call_delay_s)
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


@pytest.fixture
def fake_stdio_server(tmp_path: Path):
    """返回 (script_path, env_factory) — 用来生成 McpServerStdioConfig。

    用法:
        script, env = fake_stdio_server(tools=[...], behavior="fail_call")
        cfg = McpServerStdioConfig(command=sys.executable, args=[script], env=env)
    """
    script = tmp_path / "fake_stdio.py"
    script.write_text(FAKE_STDIO_TEMPLATE, encoding="utf-8")

    def _build(tools: list[dict], *, behavior: str = "normal",
               call_delay_s: float = 0.0) -> tuple[str, dict[str, str]]:
        env = {
            "MCP_TOOLS_JSON": json.dumps(tools),
            "MCP_BEHAVIOR": behavior,
            "MCP_CALL_DELAY_S": str(call_delay_s),
        }
        return str(script), env

    return _build


@pytest_asyncio.fixture
async def fake_http_server() -> AsyncIterator[str]:
    """起一个 aiohttp HTTP server,实现 MCP Streamable HTTP 协议。

    Yields: base URL (e.g., "http://127.0.0.1:54321")
    """
    import aiohttp.web

    session_id = "fake-session-123"

    async def mcp_post(request: aiohttp.web.Request) -> aiohttp.web.Response:
        msg = await request.json()
        req_id = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            return aiohttp.web.json_response({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "fake-http"},
                    "capabilities": {},
                },
            }, headers={"Mcp-Session-Id": session_id})
        elif method == "tools/list":
            return aiohttp.web.json_response({
                "jsonrpc": "2.0", "id": req_id,
                "result": {"tools": [
                    {"name": "echo", "description": "echo",
                     "inputSchema": {"type": "object"}},
                ]},
            }, headers={"Mcp-Session-Id": session_id})
        elif method == "tools/call":
            return aiohttp.web.json_response({
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": "ok"}],
                           "isError": False},
            }, headers={"Mcp-Session-Id": session_id})
        else:
            return aiohttp.web.json_response({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": "Method not found"},
            })

    app = aiohttp.web.Application()
    app.router.add_post("/mcp", mcp_post)

    # 找空闲端口
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        await runner.cleanup()


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """保证每个测试有 event loop(python -m pytest 走 sync fixture 也能用)。"""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


# 让 autouse fixture 名字简短,避免 -W error 误伤
__all__ = ["fake_stdio_server", "fake_http_server"]