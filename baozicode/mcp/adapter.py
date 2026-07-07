"""MCP tool ↔ BaoZiCode ToolDefinition / ToolResult 适配。

设计决策(见 design.md D2):
- 名称:`mcp__<server>__<tool>` 强制 namespace 前缀
- side_effect:默认 True;annotations.readOnlyHint=True → False
- risk:默认 "high";annotations.readOnlyHint=True → "low"
- path_args:启发式扫 inputSchema.properties 找 string-typed 且名字匹配
  (?i).*(path|file|dir|root).* 的字段
"""

from __future__ import annotations

import re
from typing import Any

from baozicode.mcp.types import McpCallResult, McpTool
from baozicode.tools.base import ToolDefinition, ToolResult

_PATH_HINT_RE = re.compile(r"(?i).*(path|file|dir|root).*")

_PREFIX = "mcp__"


def make_namespaced_name(server_name: str, tool_name: str) -> str:
    """构造 MCP 工具的全限定名。

    把 server_name 和 tool_name 都清洗为单下划线连接(避免名字里含
    `__` 引起解析歧义)。
    """
    safe_server = server_name.replace("__", "_")
    safe_tool = tool_name.replace("__", "_")
    return f"{_PREFIX}{safe_server}__{safe_tool}"


def parse_namespaced_name(full_name: str) -> tuple[str, str] | None:
    """反向解析 `mcp__<server>__<tool>` → (server, tool);非 MCP 名字返回 None。"""
    if not full_name.startswith(_PREFIX):
        return None
    rest = full_name[len(_PREFIX):]
    if "__" not in rest:
        return None
    server, tool = rest.split("__", 1)
    if not server or not tool:
        return None
    return server, tool


def _infer_path_args(input_schema: dict[str, Any]) -> list[str]:
    """扫 inputSchema.properties,挑 string-typed 且名字像路径的参数。

    只看顶层 properties(不递归 $defs/oneOf — 保守即可,误判的代价是
    沙箱拦不到某些深嵌套路径;漏掉的代价是 false positive 极少)。
    """
    if not isinstance(input_schema, dict):
        return []
    props = input_schema.get("properties")
    if not isinstance(props, dict):
        return []
    out: list[str] = []
    for name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        # 简单 string 类型(含 nullable + 简单 string 数组)
        type_field = spec.get("type")
        types: list[str] = []
        if isinstance(type_field, str):
            types.append(type_field)
        elif isinstance(type_field, list):
            types.extend(t for t in type_field if isinstance(t, str))
        if "string" not in types:
            continue
        if _PATH_HINT_RE.match(name):
            out.append(name)
    return out


def _read_bool_annotation(annotations: dict[str, Any] | None, key: str) -> bool | None:
    if not isinstance(annotations, dict):
        return None
    val = annotations.get(key)
    if isinstance(val, bool):
        return val
    return None


def adapt_tool(server_name: str, mcp_tool: McpTool) -> ToolDefinition:
    """把 MCP 工具转成 BaoZiCode ToolDefinition。"""
    name = make_namespaced_name(server_name, mcp_tool.name)

    read_only = _read_bool_annotation(mcp_tool.annotations, "readOnlyHint")
    side_effect = False if read_only else True
    risk = "low" if read_only else "high"

    path_args = _infer_path_args(mcp_tool.input_schema)

    return ToolDefinition(
        name=name,
        description=mcp_tool.description or "",
        parameters=mcp_tool.input_schema,
        risk=risk,  # type: ignore[arg-type]
        side_effect=side_effect,
        path_args=path_args,
    )


def adapt_call_result(call: McpCallResult) -> ToolResult:
    """把 MCP `tools/call` 响应转成 ToolResult。

    - text 块顺序拼接为单字符串 content
    - 非 text 块渲染为 `[<type>: <truncated repr>]`
    - isError → is_error
    """
    parts: list[str] = []
    for block in call.content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        else:
            # 非 text 块(image/audio/resource)标签化,保留 raw 字段供调试
            data_repr = str(block)[:200]
            parts.append(f"[{btype}: {data_repr}]")
    return ToolResult(
        tool_call_id="",
        content="\n".join(parts),
        is_error=call.is_error,
    )


__all__ = [
    "adapt_call_result",
    "adapt_tool",
    "make_namespaced_name",
    "parse_namespaced_name",
]
