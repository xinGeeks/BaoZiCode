"""MCP client 包(v0.6)。

让 BaoZiCode 通过 Model Context Protocol 自动发现并注册外部 server 提供的
工具,Agent 调用时完全无感 — MCP 工具自动走五层防御权限。

依赖方向(单向):
    mcp/  ──→  config/  +  tools/base.py
"""
from __future__ import annotations

# 类型与 JSON-RPC
from baozicode.mcp.jsonrpc import JsonRpcDispatcher
from baozicode.mcp.types import (
    JSONRPC_VERSION,
    MCP_PROTOCOL_VERSION,
    JsonRpcError,
    JsonRpcNotification,
    JsonRpcRequest,
    McpCallResult,
    McpError,
    McpServerStatus,
    McpTool,
)

__all__ = [
    "JSONRPC_VERSION",
    "JsonRpcDispatcher",
    "JsonRpcError",
    "JsonRpcNotification",
    "JsonRpcRequest",
    "MCP_PROTOCOL_VERSION",
    "McpCallResult",
    "McpError",
    "McpServerStatus",
    "McpTool",
    "bootstrap",
    "McpClientManager",
    "McpSession",
]


def __getattr__(name: str):
    """延迟 import — manager/client 在它们自己的文件里引用 tools/registry,
    若上层先 import mcp 会触发循环。Agent 启动时才需要。"""
    if name == "bootstrap":
        from baozicode.mcp.manager import bootstrap
        return bootstrap
    if name == "McpClientManager":
        from baozicode.mcp.manager import McpClientManager
        return McpClientManager
    if name == "McpSession":
        from baozicode.mcp.client import McpSession
        return McpSession
    raise AttributeError(f"module 'baozicode.mcp' has no attribute {name!r}")
