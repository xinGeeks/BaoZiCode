"""McpClientManager — 多 server 生命周期编排。

职责:
1. `bootstrap(config)`:并发连接所有 server,跑握手,register 工具到 registry
2. 单 server 失败降级(per-server try/except),banner 警告
3. 持有 `statuses: dict[name, ServerState]`,供 /mcp slash 命令展示
4. `invoke_tool(name, args)`:在 connected server 上路由工具调用
5. 断开检测:session 的 recv_loop 退出时把 server 标 broken
6. `reconnect(name)`:重跑单 server 握手

依赖:
- baozicode.tools.registry — 注册/取消 MCP 工具
- baozicode.mcp.client.McpSession — per-server 会话
- baozicode.mcp.transport_* — 传输层
- baozicode.mcp.adapter — MCP ↔ ToolDefinition 转换
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from baozicode.config.schema import AppConfig, McpServerConfig, McpServerHttpConfig, McpServerStdioConfig
from baozicode.mcp.adapter import adapt_call_result, adapt_tool, parse_namespaced_name
from baozicode.mcp.client import McpSession
from baozicode.mcp.transport_http import HttpTransport
from baozicode.mcp.transport_stdio import StdioTransport
from baozicode.mcp.types import McpServerStatus
from baozicode.tools import registry as tool_registry
from baozicode.tools.base import ToolDefinition, ToolResult

log = logging.getLogger("baozicode.mcp.manager")


class ServerState:
    """单个 server 的运行状态。"""

    __slots__ = ("status", "error", "tools", "executor", "session")

    def __init__(
        self,
        *,
        status: McpServerStatus,
        error: str = "",
        tools: list[ToolDefinition] | None = None,
        executor: Any | None = None,
        session: McpSession | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.tools: list[ToolDefinition] = tools or []
        # executor: async fn(name, args) -> ToolResult;None 当 server 非 connected
        self.executor = executor
        self.session = session

    def __repr__(self) -> str:
        return (
            f"ServerState(status={self.status!r}, error={self.error!r}, "
            f"tools={len(self.tools)})"
        )


def _build_transport(
    config: McpServerConfig,
    server_name: str,
) -> StdioTransport | HttpTransport:
    """根据 config.type 构造对应 transport。"""
    if isinstance(config, McpServerStdioConfig):
        return StdioTransport(
            command=config.command,
            args=config.args,
            env=config.env,
            cwd=config.cwd,
            logger_name=f"baozicode.mcp.{server_name}",
        )
    if isinstance(config, McpServerHttpConfig):
        return HttpTransport(
            url=config.url,
            headers=config.headers,
            timeout_s=max(config.call_timeout_s, config.init_timeout_s),
        )
    raise TypeError(f"unsupported MCP transport config type: {type(config).__name__}")


async def _connect_one(name: str, config: McpServerConfig) -> ServerState:
    """单 server 完整握手流程;失败抛异常由 caller 捕获。"""
    transport = _build_transport(config, name)
    session = McpSession(
        name=name,
        transport=transport,
        init_timeout_s=config.init_timeout_s,
        tools_list_timeout_s=config.tools_list_timeout_s,
        call_timeout_s=config.call_timeout_s,
    )
    await session.start()
    try:
        await asyncio.wait_for(
            asyncio.shield(session.initialize()),
            timeout=config.startup_total_timeout_s,
        )
        await session.send_initialized_notification()
        mcp_tools = await session.list_tools()
    except Exception:
        # 启动失败 → 清理 transport/session
        await session.disconnect()
        raise

    tools: list[ToolDefinition] = []
    for mcp_tool in mcp_tools:
        td = adapt_tool(name, mcp_tool)
        tools.append(td)

    return ServerState(
        status="connected",
        tools=tools,
        session=session,
    )


async def bootstrap(config: AppConfig) -> "McpClientManager":
    """v0.6 启动钩子:从 AppConfig 构建 McpClientManager 并并发 bootstrap。

    失败降级 — 任何 server 的失败都不阻止其它 server。
    """
    manager = McpClientManager(config.mcp_servers)
    await manager.bootstrap()
    return manager


class McpClientManager:
    """多 MCP server 生命周期编排器。"""

    def __init__(self, configs: dict[str, McpServerConfig] | None = None) -> None:
        self._configs: dict[str, McpServerConfig] = dict(configs or {})
        self._states: dict[str, ServerState] = {}
        self._lock = asyncio.Lock()

    # ---- 公开状态查询 ----

    @property
    def states(self) -> dict[str, ServerState]:
        return dict(self._states)

    def get_state(self, name: str) -> ServerState | None:
        return self._states.get(name)

    def get_all_tools(self) -> list[ToolDefinition]:
        """返回所有 connected server 的工具(已注册到全局 registry,这里为方便)。"""
        out: list[ToolDefinition] = []
        for state in self._states.values():
            if state.status == "connected":
                out.extend(state.tools)
        return out

    # ---- 启动 / 重启 ----

    async def bootstrap(self) -> None:
        """并发连接所有 server,失败降级,register 工具到全局 registry。"""
        if not self._configs:
            log.info("mcp: no servers configured, skipping bootstrap")
            return

        names = list(self._configs.keys())
        results = await asyncio.gather(
            *(self._connect_with_isolation(n, self._configs[n]) for n in names),
            return_exceptions=False,  # 每个 task 自己处理异常
        )
        for name, state in zip(names, results, strict=True):
            self._states[name] = state
            if state.status == "connected":
                # 注册到全局 tool registry,每个 tool 一个独立 executor(捕获 tool_name)
                for tool in state.tools:
                    bare_tool_name = _strip_prefix(tool.name, name)
                    executor = _make_per_tool_executor(self, name, bare_tool_name)
                    try:
                        await tool_registry.register_mcp_tool(tool, executor)
                    except ValueError as exc:
                        # 与内置冲突 — 跳过(理论上 adapter 已过滤,但保险)
                        log.warning("mcp: skipping tool %s: %s", tool.name, exc)

        # 汇总
        connected = sum(1 for s in self._states.values() if s.status == "connected")
        failed = sum(1 for s in self._states.values() if s.status == "failed")
        log.info(
            "mcp: bootstrap done — connected=%d failed=%d (total configured=%d)",
            connected, failed, len(self._configs),
        )

    async def _connect_with_isolation(
        self, name: str, config: McpServerConfig
    ) -> ServerState:
        """包一层 try/except,失败返回 ServerState(failed)。"""
        try:
            return await _connect_one(name, config)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            log.warning("mcp: server %r failed to connect: %s", name, msg)
            return ServerState(status="failed", error=msg)

    async def reconnect(self, name: str) -> ServerState:
        """重连指定 server(无论当前状态)。"""
        config = self._configs.get(name)
        if config is None:
            return ServerState(status="failed", error=f"unknown server: {name!r}")

        # 清理旧 session 和旧工具
        old = self._states.get(name)
        if old is not None:
            old_tool_names = [t.name for t in old.tools]
            if old_tool_names:
                await tool_registry.unregister_mcp_tools(old_tool_names)
            if old.session is not None:
                try:
                    await old.session.disconnect()
                except Exception:  # noqa: BLE001
                    pass

        # 重连
        state = await self._connect_with_isolation(name, config)
        self._states[name] = state
        if state.status == "connected":
            for tool in state.tools:
                executor = _make_per_tool_executor(self, name, _strip_prefix(tool.name, name))
                try:
                    await tool_registry.register_mcp_tool(tool, executor)
                except ValueError as exc:
                    log.warning("mcp: skipping tool %s: %s", tool.name, exc)
        return state

    # ---- 工具调用路由 ----

    async def invoke_tool(
        self,
        full_name: str,
        arguments: dict,
    ) -> ToolResult:
        """从 `mcp__<server>__<tool>` 反查 server,调用对应 session.call_tool。"""
        parsed = parse_namespaced_name(full_name)
        if parsed is None:
            return ToolResult.error_result(
                "", f"not an MCP tool: {full_name!r}",
            )
        server_name, tool_name = parsed
        state = self._states.get(server_name)
        if state is None:
            return ToolResult.error_result(
                "", f"unknown MCP server: {server_name!r}",
            )
        if state.status != "connected" or state.session is None:
            return ToolResult.error_result(
                "",
                f"MCP server {server_name!r} is {state.status}: {state.error or 'no error'}",
            )
        try:
            result = await state.session.call_tool(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001
            log.warning("mcp: call_tool failed for %s: %s", full_name, exc)
            # 标 broken(可能是连接挂了)
            await self._mark_broken(server_name)
            return ToolResult.error_result(
                "",
                f"MCP server {server_name!r} disconnected during call: {exc}",
            )
        return adapt_call_result(result)

    async def _mark_broken(self, name: str) -> None:
        """把 server 标 broken(由 recv_loop 异常 / call_tool 异常触发)。"""
        state = self._states.get(name)
        if state is None or state.status == "broken":
            return
        log.warning("mcp: server %r marked broken", name)
        # 先取消旧 session 避免泄漏
        if state.session is not None:
            try:
                await state.session.disconnect()
            except Exception:  # noqa: BLE001
                pass
        old_tool_names = [t.name for t in state.tools]
        if old_tool_names:
            await tool_registry.unregister_mcp_tools(old_tool_names)
        self._states[name] = ServerState(
            status="broken",
            error=state.error or "disconnected",
            tools=[],
            executor=None,
            session=None,
        )

    # ---- 关闭 ----

    async def shutdown(self) -> None:
        """关闭所有连接,清理注册的工具。"""
        for name, state in list(self._states.items()):
            tool_names = [t.name for t in state.tools]
            if tool_names:
                await tool_registry.unregister_mcp_tools(tool_names)
            if state.session is not None:
                try:
                    await state.session.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            self._states[name] = ServerState(status="broken", error="shutdown")


def _make_per_tool_executor(
    manager: "McpClientManager",
    server_name: str,
    tool_name: str,
):
    """为单个 MCP 工具构造 executor 闭包 — 闭包内捕获 server_name + tool_name。"""

    async def _exec(arguments: dict) -> ToolResult:
        return await manager.invoke_tool(
            f"mcp__{server_name}__{tool_name}",
            arguments,
        )
    return _exec


def _strip_prefix(full_name: str, server_name: str) -> str:
    """`mcp__<server>__<tool>` → `<tool>`,server 不匹配则返回原名。"""
    prefix = f"mcp__{server_name}__"
    if full_name.startswith(prefix):
        return full_name[len(prefix):]
    return full_name
