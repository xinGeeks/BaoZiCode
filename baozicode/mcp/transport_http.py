"""Streamable HTTP MCP transport。

每次请求 POST 一个 JSON-RPC 帧到 server 的 endpoint,根据 response status +
content-type 分发:
- 202 Accepted → 服务端接受了 notification / response,无 body
- 200 + application/json → 单个 JSON-RPC 帧
- 200 + text/event-stream → SSE 流,每个 `data:` 行是一个 JSON-RPC 帧

服务器可在 init 响应里通过 `Mcp-Session-Id` header 分配 session id,后续请求
必须带回这个 header。

参考 MCP spec 2025-11-25 Streamable HTTP transport 章节。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger("baozicode.mcp.http")


def _parse_sse_stream(text: str) -> list[dict[str, Any]]:
    """从 SSE 流文本中提取所有 `data:` 行,每行解析成 JSON。

    不实现完整 SSE 协议(event id / retry / 多行 data) — MCP spec 只要求
    单行 JSON 帧,够用即可。
    """
    frames: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line.startswith("data:"):
            continue
        data = line[5:].lstrip()
        if not data:
            continue
        try:
            frames.append(json.loads(data))
        except json.JSONDecodeError as exc:
            log.warning("http: SSE data line not JSON: %s", exc)
    return frames


class HttpTransport:
    """单个 MCP server 的 Streamable HTTP transport。

    `send_request` 是阻塞式 + 返回响应帧;server-initiated request 由
    `consume_push_stream` 读 GET /mcp 的 SSE 流分发给 dispatcher。
    """

    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_s: float = 60.0,
    ) -> None:
        self._url = url
        self._headers = dict(headers)
        self._timeout_s = timeout_s
        self._session_id: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._closed = False

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    def _request_headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        h.update(self._headers)
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    async def send_request(
        self,
        frame: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """POST 一个 JSON-RPC 帧,返回 server 发回的所有帧(0 或多个)。

        副作用:第一次响应里有 `Mcp-Session-Id` header 时,缓存作为 session id。
        """
        client = await self._ensure_client()
        resp = await client.post(self._url, headers=self._request_headers(), json=frame)

        # 捕获 session id(若 server 分配了)
        sid = resp.headers.get("Mcp-Session-Id")
        if sid and not self._session_id:
            self._session_id = sid
            log.debug("http: session id assigned: %s", sid)

        if resp.status_code == 202:
            return []
        if resp.status_code >= 400:
            body = resp.text[:500]
            raise RuntimeError(
                f"http: server returned {resp.status_code}: {body}"
            )

        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            return _parse_sse_stream(resp.text)
        if "application/json" in ctype or not ctype:
            # 单个 JSON 帧
            try:
                return [resp.json()]
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"http: invalid JSON in response: {exc}") from exc
        # 未知 content-type 尝试按 JSON 解析
        try:
            return [resp.json()]
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"http: unexpected content-type {ctype!r}: {exc}"
            ) from exc

    async def send_notification_frame(self, frame: dict[str, Any]) -> None:
        """POST 一个通知帧,只关心 202 Accepted(对 4xx/5xx 不抛错)。"""
        client = await self._ensure_client()
        try:
            resp = await client.post(self._url, headers=self._request_headers(), json=frame)
            if resp.status_code >= 400:
                log.warning(
                    "http: notification %s got %s: %s",
                    frame.get("method"), resp.status_code, resp.text[:200],
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("http: notification POST failed: %s", exc)

    def is_alive(self) -> bool:
        return not self._closed

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["HttpTransport"]
