"""工具注册表 — 聚合 7 个工具,提供 lookup + 路由执行。

固定顺序:Read, Write, Edit, Bash, Grep, Glob, WebFetch。
这个顺序会传给 LLM,所以保持稳定(LLM 偏好习惯的工具顺序)。
"""

from __future__ import annotations

import importlib
from typing import Awaitable, Callable

from baozicode.tools.base import ToolCall, ToolDefinition, ToolResult
from baozicode.tools.bash import TOOL as BASH
from baozicode.tools.edit import TOOL as EDIT
from baozicode.tools.glob import TOOL as GLOB
from baozicode.tools.grep import TOOL as GREP
from baozicode.tools.read import TOOL as READ
from baozicode.tools.webfetch import TOOL as WEBFETCH
from baozicode.tools.write import TOOL as WRITE

_TOOLS: list[ToolDefinition] = [READ, WRITE, EDIT, BASH, GREP, GLOB, WEBFETCH]
_BY_NAME: dict[str, ToolDefinition] = {t.name: t for t in _TOOLS}

Executor = Callable[[dict], Awaitable[ToolResult]]


def _load_executor(module_name: str) -> Executor:
    mod = importlib.import_module(module_name)
    return mod.execute


_EXECUTORS: dict[str, Executor] = {
    "Read": _load_executor("baozicode.tools.read"),
    "Write": _load_executor("baozicode.tools.write"),
    "Edit": _load_executor("baozicode.tools.edit"),
    "Bash": _load_executor("baozicode.tools.bash"),
    "Grep": _load_executor("baozicode.tools.grep"),
    "Glob": _load_executor("baozicode.tools.glob"),
    "WebFetch": _load_executor("baozicode.tools.webfetch"),
}


def get_all_tools() -> list[ToolDefinition]:
    """返回 7 个 ToolDefinition(固定顺序)。"""
    return list(_TOOLS)


def get_tool(name: str) -> ToolDefinition | None:
    """按名查找 ToolDefinition;找不到返回 None。"""
    return _BY_NAME.get(name)


def get_tool_names() -> list[str]:
    return list(_BY_NAME.keys())


async def execute_tool(
    name: str,
    arguments: dict,
    *,
    tool_call_id: str = "",
) -> ToolResult:
    """路由到具体工具的 execute(arguments),并把 tool_call_id 附到 result。"""
    executor = _EXECUTORS.get(name)
    if executor is None:
        return ToolResult.error_result(
            tool_call_id, f"unknown tool: {name!r}. Available: {get_tool_names()}"
        )

    result = await executor(arguments)
    if result.tool_call_id == "":
        result.tool_call_id = tool_call_id
    return result


async def execute_tool_call(call: ToolCall) -> ToolResult:
    """便利方法:从 ToolCall 直接执行(arguments 解析失败时返回 error_result)。"""
    if call.error:
        return ToolResult.error_result(
            call.id,
            f"tool arguments JSON parse error: {call.error}",
        )
    return await execute_tool(call.name, call.arguments, tool_call_id=call.id)


__all__ = [
    "execute_tool",
    "execute_tool_call",
    "get_all_tools",
    "get_tool",
    "get_tool_names",
]