"""工具注册表 — 聚合 7 个内置工具 + 运行时注册的 MCP 工具。

v0.6 改造:从模块级常量改成 `ToolRegistry` 类以支持运行时 MCP 工具注入。
模块级 `_default = ToolRegistry()` 单例 + 顶层函数都委托给单例,
现有 12 个调用点零修改。

固定顺序:Read, Write, Edit, Bash, Grep, Glob, WebFetch(LLM 偏好习惯)。
"""

from __future__ import annotations

import asyncio
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

Executor = Callable[[dict], Awaitable[ToolResult]]


def _load_executor(module_name: str) -> Executor:
    mod = importlib.import_module(module_name)
    return mod.execute


_BUILTIN_EXECUTORS: dict[str, Executor] = {
    "Read": _load_executor("baozicode.tools.read"),
    "Write": _load_executor("baozicode.tools.write"),
    "Edit": _load_executor("baozicode.tools.edit"),
    "Bash": _load_executor("baozicode.tools.bash"),
    "Grep": _load_executor("baozicode.tools.grep"),
    "Glob": _load_executor("baozicode.tools.glob"),
    "WebFetch": _load_executor("baozicode.tools.webfetch"),
}

_BUILTIN_TOOLS: list[ToolDefinition] = [
    READ, WRITE, EDIT, BASH, GREP, GLOB, WEBFETCH,
]


class ToolRegistry:
    """可变工具注册表 — 内置工具固定,MCP 工具可运行时注入。"""

    def __init__(self) -> None:
        self._builtin_tools: list[ToolDefinition] = list(_BUILTIN_TOOLS)
        self._builtin_names: frozenset[str] = frozenset(t.name for t in _BUILTIN_TOOLS)
        self._mcp_tools: dict[str, ToolDefinition] = {}
        self._executors: dict[str, Executor] = dict(_BUILTIN_EXECUTORS)
        self._lock = asyncio.Lock()

    def get_all_tools(
        self,
        role: str | None = None,
    ) -> list[ToolDefinition]:
        """返回所有工具(内置在前,固定顺序;MCP 在后)。

        v1.4 新增 `role` 参数:
        - `role=None`(默认) — 返全部工具,完全等同 v1.3 行为(向后兼容)
        - `role='lead' | 'member' | 'subagent' | 'coordinator'` —
          只返 `tool.role_visibility is None` 或 `role in
          tool.role_visibility` 的工具;Lead 才看得到 team_*,member /
          subagent 看不到。
        - v1-4-team-coordinator:`role='coordinator'` 额外显式剔除
          Write/Edit/Bash(写类工具),不论其 role_visibility;
          `load_skill` / `task`(`tool_type='internal'`) 不受影响。
        """
        all_tools = list(self._builtin_tools) + list(self._mcp_tools.values())
        if role is None:
            return all_tools
        result = [
            t for t in all_tools
            if t.role_visibility is None or role in t.role_visibility
        ]
        if role == "coordinator":
            return [
                t for t in result
                if t.name not in {"Write", "Edit", "Bash"}
            ]
        return result

    def get_tool(self, name: str) -> ToolDefinition | None:
        for t in self._builtin_tools:
            if t.name == name:
                return t
        return self._mcp_tools.get(name)

    def get_tool_names(self) -> list[str]:
        return [t.name for t in self._builtin_tools] + list(self._mcp_tools.keys())

    async def register_mcp_tool(
        self,
        tool: ToolDefinition,
        executor: Executor,
    ) -> None:
        """注册一个 MCP 工具。

        Args:
            tool: 已适配的 ToolDefinition(名字已是 `mcp__<server>__<tool>` 形式)
            executor: 实际调用的 async callable — 通常是 manager 的
                `invoke_tool(name, arguments)` 闭包

        Raises:
            ValueError: 名字与内置工具冲突
        """
        await self.register_tool(tool, executor, source_label="MCP")

    async def register_tool(
        self,
        tool: ToolDefinition,
        executor: Executor,
        *,
        source_label: str = "runtime",
    ) -> None:
        """注册一个运行时工具(MCP / 内部工具都用此入口)。

        v1.0 新增:ToolDefinition 多了 `tool_type` 字段,但注册逻辑不区分类型——
        统一走 `_mcp_tools` 这个 dict 存储,key 是 tool.name。`source_label` 仅
        用于错误消息提示来源。

        Raises:
            ValueError: 名字与内置工具冲突,或已注册过同名 runtime tool
        """
        async with self._lock:
            if tool.name in self._builtin_names:
                raise ValueError(
                    f"{source_label} tool name {tool.name!r} collides with "
                    f"built-in tool; this registration is rejected to avoid "
                    f"masking built-ins."
                )
            if tool.name in self._mcp_tools:
                raise ValueError(
                    f"{source_label} tool name {tool.name!r} already registered"
                )
            self._mcp_tools[tool.name] = tool
            self._executors[tool.name] = executor

    async def unregister_mcp_tools(self, names: list[str]) -> None:
        """批量移除 MCP 工具(reconnect 时清旧工具用)。"""
        async with self._lock:
            for n in names:
                self._mcp_tools.pop(n, None)
                self._executors.pop(n, None)

    def mcp_tool_names(self) -> list[str]:
        return list(self._mcp_tools.keys())

    async def execute_tool_call(self, call: ToolCall) -> ToolResult:
        """路由到具体工具的 execute(arguments),并把 tool_call_id 附到 result。

        找不到工具时返回 error_result,带可用工具列表。
        """
        if call.error:
            return ToolResult.error_result(
                call.id,
                f"tool arguments JSON parse error: {call.error}",
            )
        executor = self._executors.get(call.name)
        if executor is None:
            return ToolResult.error_result(
                call.id,
                f"unknown tool: {call.name!r}. Available: {self.get_tool_names()}",
            )
        result = await executor(call.arguments)
        if result.tool_call_id == "":
            result.tool_call_id = call.id
        return result


# 模块级单例 — 现有调用点零修改
_default = ToolRegistry()


def get_default_tool_registry() -> ToolRegistry:
    """返回模块级 ToolRegistry 单例 — v1.0 Skills bootstrap 用。"""
    return _default


def get_all_tools(role: str | None = None) -> list[ToolDefinition]:
    return _default.get_all_tools(role=role)


def get_tool(name: str) -> ToolDefinition | None:
    return _default.get_tool(name)


def get_tool_names() -> list[str]:
    return _default.get_tool_names()


async def register_mcp_tool(tool: ToolDefinition, executor: Executor) -> None:
    await _default.register_mcp_tool(tool, executor)


async def register_tool(
    tool: ToolDefinition,
    executor: Executor,
    *,
    source_label: str = "runtime",
) -> None:
    """v1.0:通用 runtime tool 注册(MCP / 内部工具都用)。

    `source_label` 仅用于错误消息提示注册来源。
    """
    await _default.register_tool(tool, executor, source_label=source_label)


async def unregister_mcp_tools(names: list[str]) -> None:
    await _default.unregister_mcp_tools(names)


def mcp_tool_names() -> list[str]:
    return _default.mcp_tool_names()


async def execute_tool(
    name: str,
    arguments: dict,
    *,
    tool_call_id: str = "",
) -> ToolResult:
    """路由到具体工具的 execute(arguments),并把 tool_call_id 附到 result。"""
    executor = _default._executors.get(name)  # noqa: SLF001 — 内部访问保持兼容
    if executor is None:
        return ToolResult.error_result(
            tool_call_id, f"unknown tool: {name!r}. Available: {_default.get_tool_names()}"
        )
    result = await executor(arguments)
    if result.tool_call_id == "":
        result.tool_call_id = tool_call_id
    return result


async def execute_tool_call(call: ToolCall) -> ToolResult:
    """便利方法:从 ToolCall 直接执行(arguments 解析失败时返回 error_result)。"""
    return await _default.execute_tool_call(call)


__all__ = [
    "ToolRegistry",
    "execute_tool",
    "execute_tool_call",
    "get_all_tools",
    "get_default_tool_registry",
    "get_tool",
    "get_tool_names",
    "mcp_tool_names",
    "register_mcp_tool",
    "register_tool",
    "unregister_mcp_tools",
]