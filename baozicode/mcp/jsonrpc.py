"""JSON-RPC 2.0 消息调度器。

每个 per-server 持有一个 JsonRpcDispatcher 实例,负责:
- 出站:`make_request()` 给上层分配 id + Future,等 transport 写出去
- 入站:`dispatch_incoming()` 把 transport 读到的 frame 解析,按 id 找到
  对应 Future resolve;若是服务端反向请求则生成错误响应让 transport 回写;
  若是服务端通知则 log DEBUG
- 取消:`fail_pending()` 把所有未完成 Future 标为异常(断开时清理)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from baozicode.mcp.types import (
    JSONRPC_VERSION,
    JsonRpcError,
    JsonRpcNotification,
    JsonRpcRequest,
)

log = logging.getLogger("baozicode.mcp.jsonrpc")


class JsonRpcDispatcher:
    """异步 JSON-RPC 2.0 调度器。

    单实例 per-server,不跨 server 共享 id 空间(id 冲突的可能性无)。
    """

    def __init__(self) -> None:
        self._next_id: int = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    def _create_future(self) -> asyncio.Future[dict[str, Any]]:
        """创建一个不绑定具体事件循环的 Future。

        优先用 `loop.create_future()`(能拿到 running loop 时);否则用
        `asyncio.Future()`。后者在 Python 3.10+ 会尝试绑当前 thread 的 loop,
        若没有 running loop 则退化到 `_get_loop()` fallback。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 不在 async 上下文(测试 setup / 同步路径),退化方案:
            # 拿到当前线程的事件循环(没有则新建)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError("loop closed")
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        return loop.create_future()

    # ---- 出站 ----

    def make_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[JsonRpcRequest, asyncio.Future[dict[str, Any]]]:
        """分配新 id,注册 Future,返回 (request, future)。

        上层负责把 request 序列化为帧通过 transport 写出,然后 await future。
        """
        self._next_id += 1
        req_id = self._next_id
        future = self._create_future()
        self._pending[req_id] = future
        return JsonRpcRequest(id=req_id, method=method, params=params), future

    def make_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> JsonRpcNotification:
        """构造一个通知帧。无需 id / future。"""
        return JsonRpcNotification(method=method, params=params)

    # ---- 入站 ----

    def dispatch_incoming(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        """处理一帧服务端消息。

        Returns:
            - `None` 当 frame 是响应(已 resolve future)或服务端通知(无响应)
            - 一个 dict 表示需要 transport 回写的错误响应(用于服务端反向请求)
        """
        if not isinstance(frame, dict):
            log.warning("jsonrpc: non-dict frame received: %r", frame)
            return None

        if frame.get("jsonrpc") != JSONRPC_VERSION:
            log.warning("jsonrpc: frame missing/wrong jsonrpc version: %r", frame)
            return None

        has_id = "id" in frame
        has_method = "method" in frame

        # ---- 服务端响应(有 id,无 method) ----
        if has_id and not has_method:
            return self._handle_response(frame)

        # ---- 服务端通知(有 method,无 id) ----
        if has_method and not has_id:
            self._handle_notification(frame)
            return None

        # ---- 服务端反向请求(有 id 又有 method) ----
        if has_id and has_method:
            return self._handle_server_request(frame)

        log.warning("jsonrpc: unrecognizable frame: %r", frame)
        return None

    def _handle_response(self, frame: dict[str, Any]) -> None:
        req_id = frame["id"]
        future = self._pending.pop(req_id, None)
        if future is None or future.done():
            # 未知 id 或已完成的 future — 可能是 stale 响应,忽略
            log.debug("jsonrpc: response for unknown/done id=%s", req_id)
            return

        if "error" in frame:
            err = frame["error"]
            future.set_exception(
                _make_protocol_error(
                    err.get("message", "unknown error"),
                    code=err.get("code"),
                    data=err.get("data"),
                )
            )
        else:
            future.set_result(frame.get("result", {}))

        return None

    def _handle_notification(self, frame: dict[str, Any]) -> None:
        method = frame.get("method", "<unknown>")
        log.debug("jsonrpc: server notification received: %s params=%r", method, frame.get("params"))

    def _handle_server_request(self, frame: dict[str, Any]) -> dict[str, Any]:
        method = frame.get("method", "<unknown>")
        req_id = frame["id"]
        log.debug("jsonrpc: server-initiated request rejected: %s id=%s", method, req_id)
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "error": {
                "code": JsonRpcError.METHOD_NOT_FOUND,
                "message": "Method not found",
            },
        }

    # ---- 生命周期 ----

    def fail_pending(self, reason: str) -> None:
        """断开时把所有未完成 future 标为失败。"""
        if not self._pending:
            return
        pending = self._pending
        self._pending = {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(_make_protocol_error(reason))

    def pending_count(self) -> int:
        return len(self._pending)


def _make_protocol_error(
    message: str,
    *,
    code: int | None = None,
    data: Any | None = None,
) -> RuntimeError:
    """构造一个携带 JSON-RPC error code 的异常(用 RuntimeError 子类 or 普通 Exception)。

    为了不引入循环 import,这里直接构造一个匿名异常类型挂载 code/data 属性。
    """
    err = RuntimeError(message)
    err.code = code  # type: ignore[attr-defined]
    err.data = data  # type: ignore[attr-defined]
    return err


__all__ = ["JsonRpcDispatcher"]
