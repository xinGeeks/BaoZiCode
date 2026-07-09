"""v1.2 SubAgent Delegation — role-based sub-Agent system.

公开 API:

- `AgentFrontmatter` / `AgentDef` / `parse_agent` — Markdown + YAML 解析
- `AgentRegistry` / `ScanError` / `emit_scan_warnings` — 4 级扫描合并
- `substitute_placeholders` — `{var}` / `{var:default}` 替换
- `ToolFilter` / `ToolFilterEmptyError` / `GLOBAL_DENY` — 4 层工具过滤
- `SubAgentRuntime` — 隔离 sub-Agent 构造器
- `SubAgentManager` / `TaskInfo` / `TASK_TOOL` / `task_executor` — 中央编排
- `fetch_plugin_agents` — MCP 拉取 Agent 定义

每个 Agent 是 `<root>/.baozicode/agents/<name>/AGENT.md` 文件,
frontmatter 定义角色 (tools / model / max-iterations / permission-mode 等),
body 是 system prompt 的身份段。

加载优先级: project > user > builtin > plugin (MCP 驱动动态注入)。
"""

from __future__ import annotations

from .filter import GLOBAL_DENY, ToolFilter, ToolFilterEmptyError
from .loader import MissingPlaceholderError, substitute_placeholders
from .manager import (
    TASK_TOOL,
    MaxConcurrentReachedError,
    SubAgentManager,
    TaskInfo,
    task_executor,
)
from .plugin import fetch_plugin_agents
from .registry import AgentRegistry, ScanError, emit_scan_warnings
from .runtime import SubAgentRuntime
from .schema import AgentDef, AgentFrontmatter, parse_agent

__all__ = [
    "AgentDef",
    "AgentFrontmatter",
    "AgentRegistry",
    "GLOBAL_DENY",
    "MaxConcurrentReachedError",
    "MissingPlaceholderError",
    "ScanError",
    "SubAgentManager",
    "SubAgentRuntime",
    "TASK_TOOL",
    "TaskInfo",
    "ToolFilter",
    "ToolFilterEmptyError",
    "emit_scan_warnings",
    "fetch_plugin_agents",
    "parse_agent",
    "substitute_placeholders",
    "task_executor",
]