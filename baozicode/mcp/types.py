"""MCP client 公共类型定义。

所有跨模块传递的数据结构都集中在这一文件,避免循环 import。
JSON-RPC 2.0 字段名直接对齐 spec,不做驼峰转换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

McpServerStatus = Literal["connected", "failed", "broken"]

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-11-25"


@dataclass
class JsonRpcRequest:
    """客户端发起的请求(带 id,期望响应)。"""

    id: int
    method: str
    params: dict[str, Any] | None = None

    def to_frame(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": self.id,
            "method": self.method,
        }
        if self.params is not None:
            frame["params"] = self.params
        return frame


@dataclass
class JsonRpcNotification:
    """客户端发起的通知(无 id,不期望响应)。"""

    method: str
    params: dict[str, Any] | None = None

    def to_frame(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": self.method,
        }
        if self.params is not None:
            frame["params"] = self.params
        return frame


@dataclass
class JsonRpcError:
    """JSON-RPC 错误响应 — 由服务端发起或客户端发起以拒绝服务端反向请求。"""

    code: int
    message: str
    data: Any | None = None

    METHOD_NOT_FOUND = -32601
    INVALID_REQUEST = -32600
    INTERNAL_ERROR = -32603
    PARSE_ERROR = -32700


@dataclass
class McpTool:
    """MCP `tools/list` 返回的单条工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "McpTool":
        return cls(
            name=raw["name"],
            description=raw.get("description", ""),
            input_schema=raw.get("inputSchema", {}),
            annotations=raw.get("annotations"),
        )


@dataclass
class McpCallResult:
    """MCP `tools/call` 响应。

    `content` 是 MCP content block 的原始列表(text / image / resource 等),
    适配层负责把它压成单字符串 ToolResult.content。
    """

    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "McpCallResult":
        return cls(
            content=list(raw.get("content", []) or []),
            is_error=bool(raw.get("isError", False)),
        )


class McpError(RuntimeError):
    """MCP 协议错误 — 握手失败、超时、服务端 error 响应等。

    `code` 是 JSON-RPC error code(若有);`data` 是额外上下文。
    """

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        data: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


__all__ = [
    "JSONRPC_VERSION",
    "JsonRpcError",
    "JsonRpcNotification",
    "JsonRpcRequest",
    "MCP_PROTOCOL_VERSION",
    "McpCallResult",
    "McpError",
    "McpServerStatus",
    "McpTool",
]
