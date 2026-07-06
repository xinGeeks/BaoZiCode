## Why

v0.1 的 BaoZiCode 是个能聊天的 CLI，但只能"嘴上说"。v0.2 让它真正能"动手干"：模型可以自主调用 Read / Write / Edit / Bash / Grep / Glob / WebFetch 七个工具，读你项目、改你文件、跑你命令、抓你网页。这是 Claude Code 类工具的核心能力 — 没有它，TUI 再漂亮也只是一个聊天框。

## What Changes

- 新增 `baozicode/tools/` 模块：7 个工具实现 + 统一 `ToolDefinition / ToolCall / ToolResult` 内部数据类 + 注册表
- `LLMClient.stream()` 增加 `tools: list[ToolDefinition]` 参数；`ContentDelta.type="tool_use"` 落地（v0.1 已在枚举里预留）
- `Message.content` 升级为 `str | list[ContentBlock]`，支持 `text / tool_use / tool_result` 三种 block（保留 str 快速路径）
- 4 个 LLM 后端各自消化 SDK 差异：Anthropic 走 `tool_use` / `input_json_delta` / `tool_result` block；OpenAI 兼容后端走 `function calling` / `tool_calls` / `role:tool`
- `ChatScreen` 检测到 `tool_use` 时暂停流：挂载 🔧 卡片 → 高风险工具弹 `ModalScreen` 确认 → 执行 → 挂载 📄 结果卡片 → `tool_result` 喂回 `ConversationManager` → 恢复流
- 新增 `Permissions` 配置（写入 `config.yaml`）：高风险工具白名单/黑名单、批量确认开关、Bash cwd 锁定模式开关
- 流式期间输入框锁定保持不变（v0.1 行为）
- 新增斜杠命令：`/permissions`（查看当前策略）、`/tools`（查看可用工具列表）

**BREAKING**: `LLMClient.stream` 签名变化（增加 `tools` 参数，默认 `None`）。`Message.content` 类型从 `str` 放宽到 `str | list[ContentBlock]`。

## Capabilities

### New Capabilities
- `tool-calling`: 工具调用的数据模型、流式协议、后端适配、注册表

### Modified Capabilities
- `llm-streaming`: `LLMClient.stream` 接收 `tools` 参数；`ContentDelta` 支持 `tool_use`；`Message` 支持 ContentBlock 列表
- `interactive-tui`: 暂停式流式执行流程；工具调用/结果卡片；高风险工具确认 Modal
- `configuration`: 新增 `permissions` 配置块（auto_allow / confirm / batch_confirm / bash_locked_cwd / deny 列表）

## Impact

- 受影响代码：`baozicode/llm/base.py`、`baozicode/llm/anthropic.py`、`baozicode/llm/openai.py`、`baozicode/conversation/manager.py`、`baozicode/tui/chat_screen.py`、`baozicode/config/schema.py`、`baozicode/config/loader.py`、`baozicode/app.py`
- 新增代码：`baozicode/tools/`（~10 个新文件）、`baozicode/tui/tool_call_card.py`、`baozicode/tui/permission_modal.py`
- 新增依赖：`httpx`（WebFetch）、可选 `ripgrep` Python 包（Grep 后端，可降级到 subprocess）
- 配置文件：`config.example.yaml` 新增 `permissions:` 块示例
- 测试：新增 `tests/test_tools.py`、`tests/test_tool_calling.py`，扩展 `tests/test_backends.py` 覆盖 tool 适配
- OpenSpec：现有 3 个 capability spec 全部需要 MODIFIED，新增 1 个 capability spec