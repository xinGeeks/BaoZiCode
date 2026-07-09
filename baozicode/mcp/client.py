"""Per-server MCP session — handshake + 持续工具调用。

设计:
- McpSession 持有一个 transport(StdioTransport 或 HttpTransport)和一个
  JsonRpcDispatcher
- `start_recv_loop()` 起一个 background task 持续把 transport 帧分发给
  dispatcher;若 transport EOF / 抛错则调 `fail_pending` 并 exit
- `initialize()` 发 initialize 请求,await 响应,记 serverInfo / capabilities
- `send_initialized_notification()` 发通知,不等响应
- `list_tools()` 走 tools/list → 返回 list[McpTool]
- `call_tool()` 走 tools/call → 返回 McpCallResult

不依赖 tools/registry 或 manager — 让 manager 拼装它们。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from baozicode.mcp.jsonrpc import JsonRpcDispatcher
from baozicode.mcp.transport_http import HttpTransport
from baozicode.mcp.transport_stdio import StdioTransport
from baozicode.mcp.types import (
    MCP_PROTOCOL_VERSION,
    McpCallResult,
    McpError,
    McpTool,
)

log = logging.getLogger("baozicode.mcp.session")

APP_VERSION = "0.6.0"


class McpSession:
    """单个 MCP server 的逻辑会话。"""

    def __init__(
        self,
        *,
        name: str,
        transport: StdioTransport | HttpTransport,
        init_timeout_s: float = 5.0,
        tools_list_timeout_s: float = 8.0,
        call_timeout_s: float = 60.0,
    ) -> None:
        self.name = name
        self._transport = transport
        self._dispatcher = JsonRpcDispatcher()
        self._init_timeout_s = init_timeout_s
        self._tools_list_timeout_s = tools_list_timeout_s
        self._call_timeout_s = call_timeout_s
        self._recv_task: asyncio.Task[None] | None = None
        self._server_info: dict[str, Any] = {}
        self._server_capabilities: dict[str, Any] = {}
        self._initialized = False

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    @property
    def server_capabilities(self) -> dict[str, Any]:
        return dict(self._server_capabilities)

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动 transport + recv loop。必须先于 initialize()。"""
        if isinstance(self._transport, StdioTransport):
            await self._transport.start()
        # HTTP transport 无显式 start,client lazy 创建
        self._recv_task = asyncio.create_task(self._recv_loop(), name=f"mcp-recv-{self.name}")

    async def _recv_loop(self) -> None:
        """持续把 transport 帧分发给 dispatcher;遇到 EOF / 异常则退出。

        用 duck-typing(hasattr):有 `recv_loop` 是 stdio-like transport,
        否则(只有 HTTP)就 park 等待 disconnect。
        """
        recv_loop = getattr(self._transport, "recv_loop", None)
        try:
            if recv_loop is not None:
                async for frame in recv_loop():
                    self._handle_incoming_frame(frame)
            else:
                # HTTP transport:没有 server-push channel 在 v0.6 中实现
                # (Streamable HTTP GET 流需要 server 端支持,大多数 server 不实现)
                await asyncio.Event().wait()  # park forever until cancelled
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("session[%s]: recv loop exited: %s", self.name, exc)
        finally:
            self._dispatcher.fail_pending(f"MCP session '{self.name}' disconnected")

    def _handle_incoming_frame(self, frame: dict[str, Any]) -> None:
        """dispatch 一帧 + 必要时回写 server-initiated request 的错误响应。"""
        outgoing = self._dispatcher.dispatch_incoming(frame)
        if outgoing is not None:
            # HTTP 情况:回写错误响应
            if isinstance(self._transport, HttpTransport):
                asyncio.create_task(
                    self._transport.send_notification_frame(outgoing),
                    name=f"mcp-error-resp-{self.name}",
                )
            # stdio 情况:不能主动写错误响应回 server(spec 只要求 client 回应,但 stdio
            # 协议是 stdin/stdout pipe,这里 write 回去也算合理)。保守起见 log
            # 警告,不写回 — server 端会因为读不到响应卡住,但不影响 BaoZiCode 主流程。
            else:
                log.debug(
                    "session[%s]: server-initiated request %s rejected; "
                    "stdio transport does not write error response back",
                    self.name,
                    frame.get("method"),
                )

    async def disconnect(self) -> None:
        """取消 recv loop + 关闭 transport。"""
        if self._recv_task is not None and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._dispatcher.fail_pending(f"MCP session '{self.name}' disconnected")
        await self._transport.close()

    # ---- MCP 协议方法 ----

    async def initialize(self) -> dict[str, Any]:
        """发 initialize 请求 + 等响应;返回 server 报告的 capabilities。"""
        req, fut = self._dispatcher.make_request(
            "initialize",
            params={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "BaoZiCode", "version": APP_VERSION},
            },
        )
        await self._send_frame(req.to_frame())
        try:
            result = await asyncio.wait_for(fut, timeout=self._init_timeout_s)
        except asyncio.TimeoutError as exc:
            raise McpError(
                f"initialize timed out after {self._init_timeout_s}s",
                code=-32000,
            ) from exc
        except RuntimeError as exc:
            raise McpError(f"initialize failed: {exc}", code=getattr(exc, "code", None)) from exc

        self._server_info = result.get("serverInfo", {})
        self._server_capabilities = result.get("capabilities", {})
        return result

    async def send_initialized_notification(self) -> None:
        """发 `notifications/initialized` 通知 — 无 id 不等响应。"""
        notif = self._dispatcher.make_notification("notifications/initialized")
        await self._send_notification_frame(notif.to_frame())
        self._initialized = True

    async def list_tools(self) -> list[McpTool]:
        req, fut = self._dispatcher.make_request("tools/list")
        await self._send_frame(req.to_frame())
        try:
            result = await asyncio.wait_for(fut, timeout=self._tools_list_timeout_s)
        except asyncio.TimeoutError as exc:
            raise McpError(
                f"tools/list timed out after {self._tools_list_timeout_s}s",
                code=-32000,
            ) from exc
        except RuntimeError as exc:
            raise McpError(f"tools/list failed: {exc}", code=getattr(exc, "code", None)) from exc

        tools_raw = result.get("tools", [])
        if not isinstance(tools_raw, list):
            raise McpError("tools/list returned non-list 'tools' field")
        return [McpTool.from_raw(t) for t in tools_raw if isinstance(t, dict)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        req, fut = self._dispatcher.make_request(
            "tools/call",
            params={"name": name, "arguments": arguments},
        )
        await self._send_frame(req.to_frame())
        try:
            result = await asyncio.wait_for(fut, timeout=self._call_timeout_s)
        except asyncio.TimeoutError as exc:
            raise McpError(
                f"tools/call {name!r} timed out after {self._call_timeout_s}s",
                code=-32000,
            ) from exc
        except RuntimeError as exc:
            raise McpError(
                f"tools/call {name!r} failed: {exc}",
                code=getattr(exc, "code", None),
            ) from exc

        if not isinstance(result, dict):
            raise McpError(f"tools/call {name!r} returned non-dict result")
        return McpCallResult.from_raw(result)

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """v1.2:发 `resources/read` 请求,返回原始 result dict。

        MCP 的 `resources/read` 协议响应格式:
        ```json
        {"contents": [{"uri": "...", "mimeType": "...", "text": "..."}]}
        ```
        返回值不做 schema 校验 — caller(agent plugin loader)自行解析。

        Raises:
            McpError: 超时 / RPC 错误 / transport 异常
        """
        req, fut = self._dispatcher.make_request(
            "resources/read",
            params={"uri": uri},
        )
        await self._send_frame(req.to_frame())
        try:
            result = await asyncio.wait_for(fut, timeout=self._call_timeout_s)
        except asyncio.TimeoutError as exc:
            raise McpError(
                f"resources/read {uri!r} timed out after {self._call_timeout_s}s",
                code=-32000,
            ) from exc
        except RuntimeError as exc:
            raise McpError(
                f"resources/read {uri!r} failed: {exc}",
                code=getattr(exc, "code", None),
            ) from exc
        if not isinstance(result, dict):
            raise McpError(f"resources/read {uri!r} returned non-dict result")
        return result

    # ---- 内部 helpers ----

    async def _send_frame(self, frame: dict[str, Any]) -> None:
        """序列化并发送一个 JSON-RPC 请求/响应帧。

        Stdio transport 用 `send(text_str)`(newline-delimited);HTTP transport
        用 `send_request(frame)` 并 parse 响应。用 duck-typing(hasattr)而非
        isinstance 是为了让 fake transport 在测试里能注入。

        对 HTTP transport,send_request 是同步 round-trip,响应帧不会经过
        recv_loop — 必须在这里 dispatch 给 dispatcher。
        """
        text = json.dumps(frame, ensure_ascii=False)
        send_request = getattr(self._transport, "send_request", None)
        if send_request is not None:
            response_frames = await send_request(frame)
            for resp in response_frames:
                self._handle_incoming_frame(resp)
        else:
            await self._transport.send(text)

    async def _send_notification_frame(self, frame: dict[str, Any]) -> None:
        """序列化并发送一个 JSON-RPC 通知帧。"""
        send_notif = getattr(self._transport, "send_notification_frame", None)
        if send_notif is not None:
            await send_notif(frame)
        else:
            await self._transport.send(json.dumps(frame, ensure_ascii=False))


__all__ = ["McpSession"]
