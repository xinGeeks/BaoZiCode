"""McpSession 单元测试 — 用 fake transport 测 handshake / list_tools / call_tool。

fake transport 直接调 dispatcher 模拟 server 行为:
- `start()` 不做事(无 subprocess)
- `send(frame)` 解析帧,如果是 initialize 返回响应,等等
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from baozicode.mcp.client import McpSession
from baozicode.mcp.types import McpError


class FakeStdioLike:
    """模拟 stdio transport 的最小行为:send 写一行,recv_loop 跑 generator。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._to_yield: asyncio.Queue[dict] = asyncio.Queue()
        self._closed = False

    async def start(self) -> None:
        return None

    async def send(self, frame_str: str) -> None:
        self.sent.append(json.loads(frame_str))

    async def push_response(self, frame: dict) -> None:
        """test 用 — 把响应塞回 client 的 recv_loop。"""
        await self._to_yield.put(frame)

    async def push_eof(self) -> None:
        await self._to_yield.put(None)  # 哨兵:让 recv_loop 自然退出

    async def recv_loop(self):
        while True:
            item = await self._to_yield.get()
            if item is None:
                return
            yield item

    def is_alive(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        self._closed = True
        # 推 None 让 recv_loop 退出
        try:
            self._to_yield.put_nowait(None)
        except Exception:
            pass


async def _respond_to_initialize(t: FakeStdioLike) -> None:
    """读最近的 initialize 请求并 push 响应。"""
    init_req = t.sent[-1]
    assert init_req["method"] == "initialize"
    await t.push_response({
        "jsonrpc": "2.0",
        "id": init_req["id"],
        "result": {
            "protocolVersion": "2025-11-25",
            "serverInfo": {"name": "fake", "version": "1.0"},
            "capabilities": {"tools": {"listChanged": False}},
        },
    })


class TestSessionLifecycle:
    async def test_initialize_awaits_response(self) -> None:
        t = FakeStdioLike()
        session = McpSession(name="test", transport=t)

        async def responder():
            # 等 initialize 请求出现
            while not t.sent:
                await asyncio.sleep(0.01)
            await _respond_to_initialize(t)

        responder_task = asyncio.create_task(responder())
        await session.start()
        try:
            result = await asyncio.wait_for(session.initialize(), timeout=2.0)
            await responder_task
            assert result["serverInfo"]["name"] == "fake"
            assert "tools" in session.server_capabilities
        finally:
            await session.disconnect()

    async def test_initialize_timeout(self) -> None:
        t = FakeStdioLike()
        session = McpSession(name="test", transport=t, init_timeout_s=0.1)
        await session.start()
        try:
            # 不响应 → 0.1s 后 timeout
            with pytest.raises(McpError, match="timed out"):
                await asyncio.wait_for(session.initialize(), timeout=1.0)
        finally:
            await session.disconnect()

    async def test_send_initialized_notification_does_not_await(self) -> None:
        t = FakeStdioLike()
        session = McpSession(name="test", transport=t)
        await session.start()
        try:
            await session.send_initialized_notification()
            # sent 列表应有一个 notification(无 id)
            notif = t.sent[-1]
            assert "id" not in notif
            assert notif["method"] == "notifications/initialized"
            assert session.initialized is True
        finally:
            await session.disconnect()


class TestListTools:
    async def test_list_tools_returns_mcp_tools(self) -> None:
        t = FakeStdioLike()
        session = McpSession(name="test", transport=t)
        await session.start()
        try:
            # 后台 responder:把 initialize 和 tools/list 都响应掉
            async def responder():
                # 等 initialize 请求
                while not any(s.get("method") == "initialize" for s in t.sent):
                    await asyncio.sleep(0.01)
                init_req = next(s for s in t.sent if s["method"] == "initialize")
                await t.push_response({
                    "jsonrpc": "2.0", "id": init_req["id"],
                    "result": {"serverInfo": {"name": "fake"}, "capabilities": {}},
                })
                # 等 tools/list
                while not any(s.get("method") == "tools/list" for s in t.sent):
                    await asyncio.sleep(0.01)
                tl_req = next(s for s in t.sent if s["method"] == "tools/list")
                await t.push_response({
                    "jsonrpc": "2.0", "id": tl_req["id"],
                    "result": {"tools": [
                        {"name": "echo", "description": "echo",
                         "inputSchema": {"type": "object"}},
                    ]},
                })

            responder_task = asyncio.create_task(responder())
            await session.initialize()
            tools = await asyncio.wait_for(session.list_tools(), timeout=2.0)
            await responder_task
            assert len(tools) == 1
            assert tools[0].name == "echo"
            assert tools[0].input_schema == {"type": "object"}
        finally:
            await session.disconnect()

    async def test_list_tools_timeout(self) -> None:
        t = FakeStdioLike()
        session = McpSession(name="test", transport=t, tools_list_timeout_s=0.1)
        await session.start()
        try:
            with pytest.raises(McpError, match="tools/list timed out"):
                await asyncio.wait_for(session.list_tools(), timeout=1.0)
        finally:
            await session.disconnect()


class TestCallTool:
    async def test_call_tool_returns_mcp_call_result(self) -> None:
        t = FakeStdioLike()
        session = McpSession(name="test", transport=t)
        await session.start()
        try:
            async def responder():
                while not any(s.get("method") == "tools/call" for s in t.sent):
                    await asyncio.sleep(0.01)
                tc_req = next(s for s in t.sent if s["method"] == "tools/call")
                await t.push_response({
                    "jsonrpc": "2.0", "id": tc_req["id"],
                    "result": {
                        "content": [{"type": "text", "text": "hello"}],
                        "isError": False,
                    },
                })

            responder_task = asyncio.create_task(responder())
            result = await asyncio.wait_for(
                session.call_tool("echo", {"text": "hi"}),
                timeout=2.0,
            )
            await responder_task
            assert result.is_error is False
            assert result.content == [{"type": "text", "text": "hello"}]
        finally:
            await session.disconnect()

    async def test_call_tool_timeout(self) -> None:
        t = FakeStdioLike()
        session = McpSession(name="test", transport=t, call_timeout_s=0.1)
        await session.start()
        try:
            with pytest.raises(McpError, match="timed out"):
                await asyncio.wait_for(
                    session.call_tool("echo", {}), timeout=1.0,
                )
        finally:
            await session.disconnect()


class TestDisconnectCleanup:
    async def test_disconnect_fails_pending_futures(self) -> None:
        t = FakeStdioLike()
        session = McpSession(name="test", transport=t, init_timeout_s=10.0)
        await session.start()
        try:
            # 启动 initialize 但不响应
            init_coro = asyncio.create_task(
                asyncio.wait_for(session.initialize(), timeout=1.0)
            )
            await asyncio.sleep(0.05)  # 让请求发出
            await session.disconnect()
            with pytest.raises((McpError, asyncio.TimeoutError, RuntimeError)):
                await init_coro
        finally:
            # session.disconnect 已 await recv_task,close transport
            pass
