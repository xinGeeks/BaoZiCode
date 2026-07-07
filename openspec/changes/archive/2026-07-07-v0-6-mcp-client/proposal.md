## Why

BaoZiCode 现在只有 7 个内置工具（Read/Write/Edit/Bash/Grep/Glob/WebFetch），用户要扩展能力只能 fork 代码。Model Context Protocol（MCP）已经成为 LLM 应用对接外部工具的事实标准 — 大量现成 server（filesystem、github、postgres、playwright 等）已经以 MCP 形式发布。补一个 MCP 客户端，配置即用，能让用户立刻拿到整个 MCP 生态的工具，无需 BaoZiCode 自己实现。

## What Changes

- 新增 `baozicode/mcp/` 包：JSON-RPC 2.0 调度、stdio 子进程传输、Streamable HTTP 传输、per-server session、tool 适配层、多 server 生命周期管理。
- 在 `config.yaml` 顶层新增 `mcp_servers:` 块，按现有 user + project 两层规则合并（project 覆盖 user），`${VAR}` 沿用现成 `_substitute_env`。
- `tools/registry.py` 从静态模块改成 `ToolRegistry` 类，支持运行时 `register_mcp_tool()`，`get_all_tools()` 合并内置 + MCP 工具。
- `Agent._v5_executor` 自动覆盖 MCP 工具（L1 黑名单 / L2 沙箱 / L3 规则 / L4 mode / L5 user 全部生效），无需修改。
- `BaoZiCodeApp` 启动时多跑一步 MCP bootstrap（连接所有 server + 列出工具 + 注入 registry），单 server 失败降级，banner 警告。
- TUI 新增 `/mcp` slash 命令，展示 server 状态、工具数、最近错误、`/mcp reconnect <name>` 重连。
- `PlanMode` 自动过滤 `side_effect=True` 的 MCP 工具（默认全部 MCP 工具 side_effect=True，所以默认全部不进 plan mode）。

无破坏性改动 — 不修改任何现有 tool 的行为；只新增一种工具来源。

## Capabilities

### New Capabilities

- `mcp-client`: 启动时通过 MCP 协议发现外部 server 工具并注册到工具中心；提供 stdio 与 Streamable HTTP 两种传输；支持多 server 并发管理；工具命名强制 `mcp__<server>__<tool>` 命名空间；自动接入五层防御权限。

### Modified Capabilities

无。现有 7 个内置工具的行为不变，权限规则、PlanMode、PromptBuilder 对 MCP 工具的处理是新增路径而非修改要求。

## Impact

- **新代码**：`baozicode/mcp/` 整包（types、jsonrpc、transport_stdio、transport_http、client、adapter、manager、__init__），约 6-8 个新文件。
- **改代码**：
  - `config/schema.py` — 新增 `McpServerConfig`（Pydantic discriminated union on `type: stdio|http`）与 `AppConfig.mcp_servers`
  - `config/loader.py` — 两层 merge `mcp_servers`，复用 `_substitute_env`
  - `tools/registry.py` — 改 `ToolRegistry` 类（保留模块级单例 `_default` 以兼容现有调用点）
  - `app.py` — `__init__` 多跑 `mcp.bootstrap()` + 持有 `mcp_manager`
  - `tui/chat_screen.py` — 新增 `/mcp` slash 命令
  - `cli.py` — 启动 banner 输出 server 状态
- **新依赖**：无。`httpx` 已在 pyproject 里（`httpx>=0.27.0`），Streamable HTTP 走 `httpx.AsyncClient.stream()`；stdio 走标准库 `asyncio.create_subprocess_exec`。
- **新配置块**：`mcp_servers: { <name>: McpServerConfig }`，每 server 含 `type`/`command`/`args`/`env`/`url`/`headers`/`timeout_s` 字段。
- **测试**：单元测试 mock transport 测 JSON-RPC 调度；集成测试用 in-process 子进程启 fake server 测端到端握手。
