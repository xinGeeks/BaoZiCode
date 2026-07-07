# v0.4 — Modular System Prompt with Cache Strategy

## Why

BaoZiCode 当前 system prompt 是单字符串占位符 (`"You are BaoZiCode, a helpful AI coding assistant."`)，行为完全靠工具 schema 自身承载，规则没有显式声明，工具描述也没有强化。结果是 LLM 行为不稳定（Edit 前忘 Read、用 Bash 模拟 Grep、Bash 不传 timeout），同时浪费钱：稳定指令和环境信息混在一起，每次都全量重发，Anthropic prompt cache 命中率 = 0。

v0.4 重构成「模块化拼装 + 缓存友好 + 行为强化」三层：稳定指令（身份 / 7 条关键规则 / 工具描述）走可缓存通道，环境信息和运行时补充指令走 user-role 消息通道（不破坏 cache prefix），关键规则在 system prompt 和工具 description 双重强化提高遵守率。

设计背景：见 `docs/superpowers/specs/2026-07-07-v0-4-prompt-design.md`。
实施计划：见 `docs/superpowers/plans/2026-07-07-v0-4-prompt.md`。

## What Changes

**新能力：**
- `baozicode/prompt/` 新包：`BuiltPrompt` 类型 + `PromptBuilder` + 11 个 `sections/*.py` 渲染器
- `RuleRegistry`：7 条关键规则（edit_requires_read / prefer_specialized_tools / bash_timeout / parallel_limit / error_then_decide / absolute_paths / webfetch_to_file），同时出现在 system prompt 和工具 description
- `PlanModeReminder`：plan mode 补充消息节奏控制（iteration 1 发一次，之后每 5 轮重发一次）
- `CacheBreakpoint` 类型 + `LLMClient.stream` 新增 keyword-only `cache_breakpoints` 参数（v0.4 接口级，4 后端 v0.4 忽略，v0.5 落地实现）

**修改能力：**
- `Agent.__init__` 构造签名变更：去掉 `system_prompt: str` 参数，改为 `config: AppConfig`（**BREAKING**：调用方需迁移）
- `Agent.run` 主循环用 `self._prompt.stable_system` / `self._prompt.augmented_tools`，新增 `_inject_reminders` 把 `<system-reminder>` 消息拼到 `messages[-1]` 之前
- `AppConfig` 新增 4 字段（`custom_instructions` / `skills_dir` / `memory_path` / `plan_reminder_interval`），`AgentConfig` 新增 2 字段（`enable_system_reminders` / `rules`），新增 `RulesConfig` 类
- `/status` 命令显示 `cache_read` / `cache_write` / `hit_rate` 三个新指标

**不动：**
- 4 个后端（Anthropic / OpenAI / MiniMax / DeepSeek）的 `cache_control` 具体实现（v0.5）
- Skill 系统本体（v0.4 只扫空目录）
- 长期记忆的写入机制（v0.4 只读 `memory.md`）
- `tools/*.py` 业务代码（只通过 `RuleRegistry.augment_tool` 在 description 注入前缀）

## Capabilities

### New Capabilities
- `prompt-modular`: 7 个固定 sections (identity / constraints / task_mode / action_exec / tool_usage / tone_style / text_output) + env_info + 3 个可选 sections (custom / skills / memory) 的模块化拼装，依赖 `BuiltPrompt` 类型和 `PromptBuilder`
- `system-reminders`: `<system-reminder type=...>` user-role 消息注入机制，含 `PlanModeReminder` 节奏控制和 4 种 reminder 类型（env / plan_mode / task_complete / cancel）
- `prompt-cache`: `CacheBreakpoint` 类型 + `LLMClient.stream(..., cache_breakpoints=...)` 接口扩展
- `agent-loop`: Agent 构造从 `system_prompt: str` 改为 `config: AppConfig`；`Agent.__init__` 调 `PromptBuilder.build()` 一次，结果存 `self._prompt: BuiltPrompt`；`Agent.run` 用 `self._prompt.stable_system` 并在每轮 LLM 调用前调 `_inject_reminders` 注入补充消息

> 注：v0-3-agent-loop 已归档但 spec sync 阶段被打断（`openspec/specs/agent-loop/` 目录为空），v0.4 一次性补建 agent-loop 完整 spec（覆盖 v0.3 Agent 主体行为 + v0.4 PromptBuilder 集成），v0-3 的内容不另行 sync。

### Modified Capabilities
- `configuration`: `AppConfig` 新增 `custom_instructions` / `skills_dir` / `memory_path` 字段；`AgentConfig` 新增 `enable_system_reminders` / `plan_reminder_interval` / `rules` 字段；新增 `RulesConfig` 类
- `interactive-tui`: `/status` 命令在原有 input/output 行后增加 `cache_read` / `cache_write` / `hit_rate` 三行

## Impact

**代码：**
- 新增：`baozicode/prompt/` 包（~13 个文件）
- 修改：`baozicode/agent/loop.py`（Agent 构造和主循环）
- 修改：`baozicode/llm/base.py` + 4 个后端（`cache_breakpoints` keyword-only 参数）
- 修改：`baozicode/config/schema.py` + `config.example.yaml`（新字段）
- 修改：`baozicode/tui/chat_screen.py`（`/status` 扩展）
- 修改：`tests/` 多个现有测试（Agent 构造签名迁移）

**调用方：**
- 所有 `Agent(system_prompt="...")` 调用需改为 `Agent(config=AppConfig(...))` —— 这是唯一 **BREAKING** 变更

**依赖：**
- 无新第三方依赖
- 仍只依赖 `pydantic` / `textual` / `anthropic` / `openai` / `python-dotenv` / `pyyaml` / `httpx`

**测试：**
- 新增 ≥41 个测试（目标 ≥54，实际分阶段达到）
- 总测试数 ≥108（67 旧 + 41 新）
- v0.4 阶段用 mock LLM 验证行为；真实 API 行为对比在 v0.5 做
