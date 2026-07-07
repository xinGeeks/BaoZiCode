"""HttpTransport 单元测试 — 用 httpx.MockTransport 注入 fake server 行为。"""

from __future__ import annotations

import json

import httpx
import pytest

from baozicode.mcp.transport_http import HttpTransport, _parse_sse_stream


class TestParseSseStream:
    def test_single_data_line(self) -> None:
        text = 'data: {"jsonrpc": "2.0", "id": 1, "result": {}}\n\n'
        frames = _parse_sse_stream(text)
        assert len(frames) == 1
        assert frames[0]["id"] == 1

    def test_multiple_data_lines(self) -> None:
        text = (
            'data: {"id": 1, "result": "a"}\n'
            "\n"
            'data: {"id": 2, "result": "b"}\n'
        )
        frames = _parse_sse_stream(text)
        assert len(frames) == 2

    def test_invalid_json_line_skipped(self) -> None:
        text = (
            "data: not json\n"
            'data: {"id": 1, "result": "ok"}\n'
        )
        frames = _parse_sse_stream(text)
        assert len(frames) == 1

    def test_empty_data_line_skipped(self) -> None:
        text = "data:\ndata: {\"id\": 1}\n"
        frames = _parse_sse_stream(text)
        assert len(frames) == 1

    def test_no_data_lines(self) -> None:
        text = "event: ping\n: comment\n"
        frames = _parse_sse_stream(text)
        assert frames == []


class TestSendRequestJson:
    async def test_json_response(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Content-Type"] == "application/json"
            assert "application/json, text/event-stream" in request.headers["Accept"]
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"echo": body.get("method")}},
            )
        t = HttpTransport(url="http://test/mcp", headers={})
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        try:
            frames = await t.send_request({"jsonrpc": "2.0", "id": 1, "method": "x"})
            assert len(frames) == 1
            assert frames[0]["result"]["echo"] == "x"
        finally:
            await t.close()


class TestSendRequestSessionId:
    async def test_session_id_captured_and_resent(self) -> None:
        sent_session_ids: list[str | None] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            sent_session_ids.append(request.headers.get("Mcp-Session-Id"))
            if not sent_session_ids[0]:
                return httpx.Response(
                    200,
                    headers={"Mcp-Session-Id": "abc123"},
                    json={"jsonrpc": "2.0", "id": 1, "result": {"init": True}},
                )
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 2, "result": {"ok": True}},
            )

        t = HttpTransport(url="http://test/mcp", headers={})
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        try:
            await t.send_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            assert t._session_id == "abc123"
            await t.send_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            # 第二个请求应带 session id
            assert sent_session_ids == [None, "abc123"]
        finally:
            await t.close()


class TestSendRequestSse:
    async def test_sse_response_parsed(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            sse = (
                'data: {"jsonrpc": "2.0", "id": 1, "result": {"step": 1}}\n\n'
                'data: {"jsonrpc": "2.0", "id": 1, "result": {"step": 2}}\n\n'
            )
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=sse)

        t = HttpTransport(url="http://test/mcp", headers={})
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        try:
            frames = await t.send_request({"jsonrpc": "2.0", "id": 1, "method": "x"})
            assert len(frames) == 2
            assert frames[0]["result"]["step"] == 1
            assert frames[1]["result"]["step"] == 2
        finally:
            await t.close()


class TestSendRequest202:
    async def test_202_accepted_returns_empty(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(202)

        t = HttpTransport(url="http://test/mcp", headers={})
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        try:
            frames = await t.send_request({"jsonrpc": "2.0", "method": "x"})
            assert frames == []
        finally:
            await t.close()


class TestSendRequestErrors:
    async def test_500_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        t = HttpTransport(url="http://test/mcp", headers={})
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError, match="500"):
                await t.send_request({"jsonrpc": "2.0", "id": 1, "method": "x"})
        finally:
            await t.close()


class TestCustomHeaders:
    async def test_custom_headers_sent(self) -> None:
        seen: dict[str, str] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization", "")
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

        t = HttpTransport(url="http://test/mcp", headers={"Authorization": "Bearer xyz"})
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        try:
            await t.send_request({"jsonrpc": "2.0", "id": 1, "method": "x"})
            assert seen["auth"] == "Bearer xyz"
        finally:
            await t.close()


class TestNotificationSend:
    async def test_notification_returns_202(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(202)

        t = HttpTransport(url="http://test/mcp", headers={})
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        try:
            # 不应抛错
            await t.send_notification_frame({"jsonrpc": "2.0", "method": "x"})
        finally:
            await t.close()

    async def test_notification_4xx_logs_warning(self, caplog) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="bad")

        logger = __import__("logging").getLogger("baozicode.mcp.http")
        logger.setLevel(__import__("logging").DEBUG)
        logger.propagate = True
        caplog.set_level(__import__("logging").DEBUG, logger="baozicode.mcp.http")

        t = HttpTransport(url="http://test/mcp", headers={})
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        try:
            await t.send_notification_frame({"jsonrpc": "2.0", "method": "x"})
            assert any("notification" in r.getMessage() for r in caplog.records)
        finally:
            await t.close()


class TestLifecycle:
    async def test_close_prevents_further_use(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        t = HttpTransport(url="http://test/mcp", headers={})
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        await t.close()
        assert t._closed is True
        assert t.is_alive() is False

    async def test_lazy_client_creation(self) -> None:
        t = HttpTransport(url="http://test/mcp", headers={})
        # before send, no client yet
        assert t._client is None
        # send ensures client
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        # replace _ensure_client's natural creation by directly testing send_request
        t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        await t.send_request({"jsonrpc": "2.0", "id": 1, "method": "x"})
        await t.close()
