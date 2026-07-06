项目名：BaoZiCode
本地语言：中文

## v0.3 范围

多轮对话 TUI + 4 后端 + 7 工具 + Agent Loop（自主 ReAct 循环）+ Plan Mode 三段式
(`/plan` 只读规划 → 补充输入 → `/do` 全工具执行）+ 三层 stop guards。

## 模块结构

```
baozicode/
├── __main__.py             # python -m baozicode
├── cli.py                  # argparse 入口
├── app.py                  # Textual App（持有 config / conversation / llm_client / agent 状态）
├── agent/                  # v0.3 新增 — Agent Loop 与事件契约
│   ├── events.py           # AgentEvent / StopReason / UsageStats / Progress
│   ├── collector.py        # StreamCollector + TurnSnapshot(双路收集)
│   ├── guards.py           # 三层 stop guards(unknown / deny / failed loop)
│   ├── scheduler.py        # 工具并发调度(方案 B + C 扩展点)
│   └── loop.py             # Agent 主循环(async generator 推 AgentEvent)
├── tui/
│   ├── chat_screen.py      # 主对话屏幕 + Agent 事件订阅 + slash 命令 + 流式渲染 + 状态栏
│   ├── tool_card.py        # ToolCallCard / ToolResultCard 组件
│   ├── permission_modal.py # 高风险工具确认 Modal(含 batch 模式)
│   ├── banner.py           # ASCII 包子
│   └── styles.tcss         # Textual 样式
├── llm/
│   ├── base.py             # LLMClient ABC、Message、ContentBlock、ContentDelta
│   ├── anthropic.py        # AnthropicBackend + message_delta.usage 捕获
│   ├── openai.py           # OpenAICompatibleBackend 基类 + include_usage 兼容
│   ├── minimax.py          # MiniMaxBackend(OpenAI 兼容)
│   ├── deepseek.py         # DeepSeekBackend(OpenAI 兼容)
│   └── factory.py          # create_client(config) → LLMClient
├── tools/                  # 7 工具 + side_effect 标记
│   ├── base.py             # ToolDefinition(side_effect 字段) / ToolCall / ToolResult
│   ├── read.py             # side_effect=False
│   ├── write.py            # side_effect=True
│   ├── edit.py             # side_effect=True
│   ├── bash.py             # side_effect=True
│   ├── grep.py             # side_effect=False
│   ├── glob.py             # side_effect=False
│   ├── webfetch.py         # side_effect=False
│   └── registry.py         # get_all_tools / execute_tool
├── conversation/
│   └── manager.py          # 多轮历史(add_turn snapshot 重建,add_tool_result)
└── config/
    ├── schema.py           # Pydantic AppConfig / BackendConfig / Permissions / AgentConfig
    └── loader.py           # YAML + .env + ${VAR} 替换
```

## 依赖方向（不要打破）

```
tui/  →  agent/  →  conversation/  →  llm/base.py  →  tools/base.py
                    ↓                  ↓                ↓
                    │          llm/{anthropic,openai,..}.py   tools/{read,write,...}.py
                    │                  ↓                ↓
                    ↓                config/     (via registry.py)
             tui 不直接 import agent 业务逻辑;
             agent 可 import llm base + conversation manager + tools base / registry
```

- `tui/` 不直接 import `anthropic` / `openai`（必须经 `llm/factory.py`）
- `llm/` 不能 import `textual`
- `agent/` 不依赖 `tui/`（v0.3 把 Agent 与 TUI 解耦,Agent 只推事件流）
- `tools/` 不依赖 `tui/` / `llm/` / `conversation/` / `agent/`（纯函数 + 路径）
- 业务状态挂在 `App` 实例上，Screen 通过 `self.app` 访问

## 关键约定

- LLM 抽象：`LLMClient.stream(messages, system, tools) -> AsyncIterator[ContentDelta]`
- `ContentDelta.type`：`"text"` → str；`"tool_use"` → `ToolCall` 实例；`"usage"` → `UsageStats`
- Anthropic 的 `system` 走独立参数；OpenAI 走 `messages[0] role=system` —— 差异在每个 backend 内部消化
- `Message.content` 是 `Union[str, list[ContentBlock]]`：`str` 是 v0.1 快速路径；`list[ContentBlock]` 含 `TextBlock / ToolUseBlock / ToolResultBlock`
- 工具调用统一抽象：`ToolDefinition`（喂给 LLM）+ `ToolCall`（LLM 请求）+ `ToolResult`（喂回 LLM），后端 SDK 类型不出 `baozicode/llm/`
- 工具 `side_effect: bool`：并发调度的唯一信号(`True` 串行,`False` 可并行)
- **Agent Loop 契约**（v0.3 核心）:
  - `Agent.run(user_message) -> AsyncIterator[AgentEvent]`,7 种事件类型:`text / tool_call / tool_result / usage / progress / done / error`
  - 5 种停止条件:`COMPLETED / MAX_ITERATIONS_REACHED / USER_CANCELLED / UNKNOWN_TOOL_HALLUCINATION / DENIALS_EXCEEDED / FAILED_TOOL_LOOP / STREAM_ERROR`
  - StreamCollector 双路:实时 yield text 给 TUI,内部累加 TurnSnapshot 作为 Agent 决策的唯一可信源
  - Plan Mode:`plan_mode=True` 时 `available_tools` 只含 `side_effect=False` 工具
- Bash cwd 三状态机：会话启动锁项目根 → `cd` 跟随 → 每次执行前 `Path.resolve().is_relative_to(project_root)` 防逃逸
- 入口命令：`baozicode`（在 `pyproject.toml` 的 `[project.scripts]` 声明）
- 配置：YAML 写非敏感配置，`.env` 写 API Key，YAML 用 `${ENV_VAR}` 占位符引用；`permissions:` 块可选,`agent.max_iterations` 块可选(默认 20)

## OpenSpec

`openspec/changes/` 下是 spec-driven 的变更提案。当前活跃 change 是 `v0-3-agent-loop`（v0-1 / v0-2 已归档到 `archive/`）。
