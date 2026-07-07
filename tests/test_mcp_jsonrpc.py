"""JsonRpcDispatcher 单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from baozicode.mcp.jsonrpc import JsonRpcDispatcher


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestOutboundRequest:
    def test_make_request_assigns_unique_ids(self) -> None:
        d = JsonRpcDispatcher()
        req1, fut1 = d.make_request("initialize")
        req2, fut2 = d.make_request("tools/list")
        assert req1.id != req2.id
        assert req1.id < req2.id  # monotonic
        assert not fut1.done()
        assert not fut2.done()
        assert d.pending_count() == 2

    def test_make_request_serializes_frame(self) -> None:
        d = JsonRpcDispatcher()
        req, _ = d.make_request("initialize", params={"x": 1})
        frame = req.to_frame()
        assert frame == {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"x": 1},
        }

    def test_make_notification_has_no_id(self) -> None:
        d = JsonRpcDispatcher()
        n = d.make_notification("notifications/initialized")
        frame = n.to_frame()
        assert "id" not in frame
        assert frame["method"] == "notifications/initialized"
        assert d.pending_count() == 0


class TestInboundResponse:
    def test_response_resolves_future_with_result(self) -> None:
        d = JsonRpcDispatcher()
        req, fut = d.make_request("ping")
        d.dispatch_incoming({
            "jsonrpc": "2.0",
            "id": req.id,
            "result": {"ok": True},
        })
        assert fut.done()
        assert fut.result() == {"ok": True}

    def test_response_with_error_raises(self) -> None:
        d = JsonRpcDispatcher()
        _, fut = d.make_request("ping")
        d.dispatch_incoming({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        })
        with pytest.raises(RuntimeError) as exc_info:
            fut.result()
        assert "Method not found" in str(exc_info.value)

    def test_response_for_unknown_id_ignored(self) -> None:
        d = JsonRpcDispatcher()
        # No pending request, just send a response with id=99
        result = d.dispatch_incoming({
            "jsonrpc": "2.0",
            "id": 99,
            "result": {"foo": "bar"},
        })
        assert result is None
        assert d.pending_count() == 0

    def test_response_removes_from_pending(self) -> None:
        d = JsonRpcDispatcher()
        _, fut = d.make_request("ping")
        assert d.pending_count() == 1
        d.dispatch_incoming({"jsonrpc": "2.0", "id": 1, "result": {}})
        assert d.pending_count() == 0


class TestInboundNotification:
    def test_server_notification_does_not_return_response(self) -> None:
        d = JsonRpcDispatcher()
        result = d.dispatch_incoming({
            "jsonrpc": "2.0",
            "method": "notifications/log",
            "params": {"level": "info", "message": "hi"},
        })
        assert result is None

    def test_server_notification_does_not_touch_pending(self) -> None:
        d = JsonRpcDispatcher()
        _, fut = d.make_request("a")
        d.dispatch_incoming({"jsonrpc": "2.0", "method": "notifications/x"})
        assert not fut.done()
        assert d.pending_count() == 1


class TestServerInitiatedRequest:
    def test_returns_method_not_found_response(self) -> None:
        d = JsonRpcDispatcher()
        result = d.dispatch_incoming({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "sampling/createMessage",
            "params": {"messages": []},
        })
        assert result is not None
        assert result["jsonrpc"] == "2.0"
        assert result["id"] == 5
        assert result["error"]["code"] == -32601
        assert result["error"]["message"] == "Method not found"


class TestConcurrentRequests:
    def test_concurrent_pending_requests(self) -> None:
        """并发发出 N 个请求,乱序到达的响应能正确 resolve。"""
        async def scenario() -> None:
            d = JsonRpcDispatcher()
            futs = [d.make_request(f"req-{i}")[1] for i in range(5)]

            # out-of-order: id=3, id=1, id=5, id=2, id=4
            for req_id in [3, 1, 5, 2, 4]:
                d.dispatch_incoming({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"echo": req_id},
                })

            results = await asyncio.gather(*futs)
            assert results == [{"echo": i} for i in range(1, 6)]
            assert d.pending_count() == 0

        _run(scenario())


class TestFailPending:
    def test_fail_pending_marks_all_unresolved(self) -> None:
        d = JsonRpcDispatcher()
        _, fut1 = d.make_request("a")
        _, fut2 = d.make_request("b")
        d.dispatch_incoming({"jsonrpc": "2.0", "id": 1, "result": {}})  # resolve 1
        d.fail_pending("server disconnected")
        # fut1 already done; fut2 should now be failed
        assert fut1.done()
        with pytest.raises(RuntimeError, match="disconnected"):
            fut2.result()

    def test_fail_pending_empty_is_noop(self) -> None:
        d = JsonRpcDispatcher()
        d.fail_pending("nope")  # should not raise
        assert d.pending_count() == 0


class TestMalformedFrames:
    def test_non_dict_frame_warns(self) -> None:
        d = JsonRpcDispatcher()
        assert d.dispatch_incoming("not a dict") is None  # type: ignore[arg-type]

    def test_wrong_jsonrpc_version_warns(self) -> None:
        d = JsonRpcDispatcher()
        assert d.dispatch_incoming({"jsonrpc": "1.0", "id": 1, "result": {}}) is None

    def test_no_id_no_method_warns(self) -> None:
        d = JsonRpcDispatcher()
        assert d.dispatch_incoming({"jsonrpc": "2.0"}) is None
