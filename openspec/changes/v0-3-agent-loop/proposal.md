## Why

v0.2 已经把"LLM 能调工具"的循环跑通了——一次用户消息可以触发多轮 tool_use → tool_result → 续流。但这个循环**耦合在 `ChatScreen._send_user_message` 里**(TUI 和 Agent 逻辑搅在一起,100 多行),且只支持"一次 LLM 响应里串行执行所有 tool_use"。当模型开始尝试复杂多步任务时,会出现三类问题:

1. **没有兜底**——LLM 进入死循环(反复调同一个失败工具、幻觉出未知工具名)会无限转下去,只有"用户看烦了手动 Ctrl+C"这一种出口
2. **没有并发**——4 个 Read 全是只读,但当前实现严格串行,慢 4 倍
3. **没有模式分离**——所有工具一把梭,用户没法"先让模型看看代码再决定改不改"

v0.3 把 Agent 抽成独立模块,加停止条件兜底、加只读工具的并发、加 Plan Mode 两段式。同时为未来的 DAG 拓扑调度(方案 C)预留扩展点。

## What Changes

- **新增 `baozicode/agent/` 模块**:`Agent` 类 + `StreamCollector` + 三层 stop guards + 调度器
- **`Agent.run()` 是 async generator**,产出 `AgentEvent` 流(`text` / `tool_call` / `tool_result` / `usage` / `progress` / `done` / `error`),TUI 订阅这个流,彻底解耦
- **5 种停止条件**:`COMPLETED` / `MAX_ITERATIONS_REACHED` / `USER_CANCELLED` / `UNKNOWN_TOOL_HALLUCINATION` / `DENIALS_EXCEEDED` / `FAILED_TOOL_LOOP` / `STREAM_ERROR`
- **迭代上限默认 20 轮**,可在 `config.yaml` 配 `agent.max_iterations`
- **`StreamCollector` 双路**:实时 `emit("text", ...)` 推 TUI,内部累加完整 `TurnSnapshot`(text + tool_calls + blocks)作为 Agent 决策唯一可信源
- **工具并发调度(方案 B)**:`side_effect=False` 的工具在同一 LLM 响应里 `asyncio.gather`,`side_effect=True` 的按 LLM 返回顺序串行执行;调度器封装在单一函数,方案 C(DAG)可替换
- **`ToolDefinition.side_effect: bool` 新字段**,Read/Grep/Glob/WebFetch = False,Write/Edit/Bash = True
- **Plan Mode**:`/plan <task>` 切到只读工具跑一轮出计划,`/do` 切回全工具执行;`/plan` 后用户可继续追加约束
- **用户取消**:Esc / Ctrl+C 在 Agent 运行中触发 `USER_CANCELLED`,不退出 app;未运行时按 Ctrl+C 仍退出 app
- **`usage` 事件**:每轮 stream 结束 yield `{this_turn, session_total}`(本轮用量 + 会话累计)
- **`progress` 事件**:最小集 `{iteration, max, phase}`,phase ∈ `{streaming, tool_exec, checking}`
- **TUI `ChatScreen._send_user_message` 重构**:从 100 行内联循环简化为"订阅 `Agent.run()` 事件 → 渲染"
- **`/status` 斜杠命令**(可选):显示当前 session 累计 token 用量

## Capabilities

### New Capabilities

- `agent-loop`: Agent 类、AgentEvent 契约、5 种停止条件、迭代上限、`/stop` 命令、用户取消信号
- `tool-scheduling`: `ToolDefinition.side_effect` 字段、按 side_effect 分批(并行批 vs 串行批)、调度器扩展点
- `plan-mode`: `/plan` + `/do` + `/auto` 斜杠命令、基于 `side_effect=False` 的工具过滤、`plan_ready` 空闲态

### Modified Capabilities

- `tool-calling`: 三层 stop guards(未知工具/拒绝超限/失败循环)成为 Tool Calling 的正式需求,不再仅是实现细节
- `llm-streaming`: `LLMClient.stream` 必须 yield 用量信息(`usage` 字段或单独事件),这是 v0.3 usage 事件的前提
- `interactive-tui`: 进度条渲染、`/plan` `/do` `/auto` `/stop` 命令、Esc/Ctrl+C 双语义(运行中=取消 run,未运行=退出 app)

## Impact

**新增模块**:`baozicode/agent/`(`__init__.py`, `loop.py`, `events.py`, `collector.py`, `scheduler.py`, `guards.py`)

**修改文件**:
- `baozicode/tui/chat_screen.py` — 100 行内联循环 → 事件订阅
- `baozicode/tools/base.py` — `ToolDefinition` 加 `side_effect: bool = False`
- `baozicode/tools/{read,write,edit,bash,grep,glob,webfetch}.py` — 每个 `TOOL` 加 `side_effect=...`
- `baozicode/llm/{anthropic,openai}.py` — `stream()` yield usage 数据(扩展 `ContentDelta` 或新增 `usage_event`)
- `baozicode/config/schema.py` — `AppConfig` 加 `agent: AgentConfig`(`max_iterations`, `cancel_grace_iterations`)
- `baozicode/conversation/manager.py` — 可能需要 `add_turn(snapshot)` 从完整 TurnSnapshot 重建消息
- `config.example.yaml` — `agent:` 块示例
- `CLAUDE.md` — 模块结构加 `baozicode/agent/`

**新增测试**:
- `tests/test_agent_loop.py` — 5 种停止条件各一 case
- `tests/test_stream_collector.py` — TurnSnapshot 完整重建一致性
- `tests/test_scheduler.py` — 并发批/串行批分组正确性
- `tests/test_guards.py` — 三层独立判断 + 共用出口
- `tests/test_plan_mode.py` — `/plan` 工具过滤 + `/do` 切换

**依赖**:无新增。`asyncio.gather` / `asyncio.Event` 是 stdlib。

**破坏性变更**:无。`ToolDefinition.side_effect` 默认 False,旧调用方兼容。`Agent` 是新增模块,v0.2 的 TUI 调用路径在 v0.3 改为订阅 Agent.run(),内部接口变化,外部 CLI 行为不变。
