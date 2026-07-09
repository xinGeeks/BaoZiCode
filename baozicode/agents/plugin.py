"""v1.2 SubAgent Delegation — MCP Plugin 拉取 Agent 定义。

公开 API:
- `fetch_plugin_agents(mcp_manager) -> tuple[list[AgentDef], list[ScanError]]`
  遍历所有 `status="connected"` 的 MCP server,通过 `resources/read` 协议
  拉 Agent 目录 + 详情。返回 (成功解析的 AgentDef 列表, 失败记录)。

协议约定(与 MCP 客户端约定):
- 目录资源 URI:`agents://list` — 返回的 `text` 字段是 JSON 列表,
  每个元素 `{"name": "...", "description": "...", "version": "..."}`
- 详情资源 URI:`agents://<name>` — 返回的 `text` 字段是 YAML frontmatter
  + Markdown body(格式跟 builtin AGENT.md 一致)

任一 server 抛异常 → 该 server 整个跳过,不影响其他 server。
任一 Agent 详情 parse 失败 → 记 scan_error,不阻断其他 agent。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from baozicode.agents.registry import ScanError
from baozicode.agents.schema import AgentDef, parse_agent

if TYPE_CHECKING:
    from baozicode.mcp.manager import McpClientManager

log = logging.getLogger(__name__)


# 目录资源 URI(MCP server 端约定)
_LIST_URI = "agents://list"


def _detail_uri(name: str) -> str:
    """MCP server 端的 Agent 详情 URI 约定。"""
    return f"agents://{name}"


def _virtual_path(server_name: str, name: str) -> Path:
    """plugin Agent 的 path 字段(虚拟路径,标识来源是 MCP server)。"""
    return Path(f"<mcp://{server_name}/{name}>")


async def fetch_plugin_agents(
    mcp_manager: "McpClientManager | None",
) -> tuple[list[AgentDef], list[ScanError]]:
    """从 MCP manager 拉取所有 connected server 暴露的 Agent。

    Args:
        mcp_manager: McpClientManager 实例(可以为 None,返回空)

    Returns:
        (agent_defs, scan_errors) 元组
        - agent_defs:成功解析的 AgentDef 列表,source="plugin"
        - scan_errors:每个失败的 server 或 agent 一条,boot 时 emit 警告
    """
    if mcp_manager is None:
        return [], []

    defs: list[AgentDef] = []
    errors: list[ScanError] = []

    for server_name, state in mcp_manager.states.items():
        if state.status != "connected" or state.session is None:
            continue
        try:
            server_defs = await _fetch_from_server(server_name, state.session)
            defs.extend(server_defs)
        except Exception as exc:  # noqa: BLE001
            # 整个 server 失败 — 记一条 scan_error,跳过
            errors.append(
                ScanError(
                    path=_virtual_path(server_name, "<list>"),
                    reason=f"MCP server {server_name!r} 拉取 agents 失败: "
                    f"{type(exc).__name__}: {exc}",
                )
            )
    return defs, errors


async def _fetch_from_server(
    server_name: str,
    session: Any,  # McpSession,避免循环 import
) -> list[AgentDef]:
    """从单个 MCP server 拉 Agent 列表 + 详情。"""
    # Step 1: 拉目录
    list_result = await session.read_resource(_LIST_URI)
    contents = list_result.get("contents", [])
    if not contents:
        return []
    # contents[0].text 是 JSON 字符串(按 MCP 协议约定)
    text = contents[0].get("text", "")
    if not text:
        return []
    try:
        catalog = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"server {server_name!r} agents://list 不是合法 JSON: {exc}"
        ) from exc
    if not isinstance(catalog, list):
        raise ValueError(
            f"server {server_name!r} agents://list 顶层必须是 list"
        )

    # Step 2: 逐个拉详情
    defs: list[AgentDef] = []
    for entry in catalog:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        try:
            detail = await session.read_resource(_detail_uri(name))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "plugin agent %s/%s 详情拉取失败: %s: %s",
                server_name, name, type(exc).__name__, exc,
            )
            continue
        detail_contents = detail.get("contents", [])
        if not detail_contents:
            continue
        md_text = detail_contents[0].get("text", "")
        if not md_text:
            continue
        # 解析 frontmatter + body
        from baozicode.agents.schema import AgentFrontmatter
        try:
            fm, body = _parse_frontmatter(md_text)
        except ValueError as exc:
            log.warning(
                "plugin agent %s/%s frontmatter 解析失败: %s",
                server_name, name, exc,
            )
            continue
        # name 必须匹配(防 server 端乱填)
        if fm.name != name:
            log.warning(
                "plugin agent %s/%s name 不一致(资源 %r vs frontmatter %r),跳过",
                server_name, name, name, fm.name,
            )
            continue
        defs.append(
            AgentDef(
                frontmatter=fm,
                body=body,
                source="plugin",
                path=_virtual_path(server_name, name),
            )
        )
    return defs


def _parse_frontmatter(md_text: str) -> tuple[Any, str]:
    """复用 baozicode.agents.schema.parse_agent,避免重复实现。"""
    return parse_agent(md_text)


__all__ = ["fetch_plugin_agents"]