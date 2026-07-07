项目名：BaoZiCode
本地语言：中文

## v0.4 范围

v0.3 之上加一层：模块化 system prompt。稳定指令走 LLM 缓存通道（不重新计费），
动态指令通过 `<system-reminder>` user-role 消息注入（不污染缓存前缀）。
7 个固定 sections（identity / constraints / task_mode / action_exec / tool_usage /
tone_style / text_output）+ env_info 段（cwd / OS / git / 当前时间）+ 3 个可选
sections（custom / skills / memory）。

`baozicode/prompt/PromptBuilder.build()` 启动时调用一次，把 `BuiltPrompt`
（stable_system + dynamic_messages + augmented_tools + cache_breakpoints）
传给 Agent，Agent 每轮把 reminders 拼到 `messages[-2]`。

## 模块结构

```
baozicode/
├── __main__.py             # python -m baozicode
├── cli.py                  # argparse 入口
├── app.py                  # Textual App（持有 config / conversation / llm_client / agent 状态）
├── agent/                  # v0.3 — Agent Loop 与事件契约(已集成 v0.4 PromptBuilder)
│   ├── events.py           # AgentEvent / StopReason / UsageStats / Progress
│   ├── collector.py        # StreamCollector + TurnSnapshot(双路收集)
│   ├── guards.py           # 三层 stop guards(unknown / deny / failed loop)
│   ├── scheduler.py        # 工具并发调度(方案 B + C 扩展点)
│   └── loop.py             # Agent 主循环(async generator 推 AgentEvent;__init__ 收 AppConfig)
├── prompt/                 # v0.4 新增 — 模块化 system prompt 拼装
│   ├── types.py            # BuiltPrompt / BuildContext / CacheBreakpoint / SystemReminder
│   ├── rules.py            # Rule / RuleRegistry + 7 DEFAULT_RULES + augment_tool()
│   ├── reminder.py         # PlanModeReminder(节奏:1, 1+N, 1+2N, ...)
│   ├── builder.py          # PromptBuilder.build() 一次构建多次复用
│   ├── sections/           # 11 个 section renderer(7 固定 + env_info + 3 可选)
│   └── __init__.py         # 公开 API re-export
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
    ├── schema.py           # Pydantic AppConfig / BackendConfig / Permissions / AgentConfig / RulesConfig(v0.4)
    └── loader.py           # YAML + .env + ${VAR} 替换
```

## 依赖方向（不要打破）

```
tui/  →  agent/  →  prompt/  →  llm/base.py → tools/base.py
                    ↓                           ↓
                  config/  ←────────── llm/{anthropic,openai,minimax,deepseek}.py
                    ↑                                    
                    └── conversation/ ──→ llm/base.py
             tui 不直接 import agent 业务逻辑;
             agent 可 import llm base + conversation manager + tools base / registry + prompt/
             prompt/ 可 import llm base + tools base + config schema(appconfig-only 类型注解)
```

- `tui/` 不直接 import `anthropic` / `openai`（必须经 `llm/factory.py`）
- `llm/` 不能 import `textual` 或 `prompt/`（避免反向依赖循环）
- `prompt/` 不依赖 `agent/` 或 `tui/`（被 agent 单向调用）
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
- **Agent Loop 契约**（v0.3 核心 + v0.4 PromptBuilder 集成）:
  - `Agent.run(user_message) -> AsyncIterator[AgentEvent]`,7 种事件类型:`text / tool_call / tool_result / usage / progress / done / error`
  - 7 种停止条件:`COMPLETED / MAX_ITERATIONS_REACHED / USER_CANCELLED / UNKNOWN_TOOL_HALLUCINATION / DENIALS_EXCEEDED / FAILED_TOOL_LOOP / STREAM_ERROR`
  - StreamCollector 双路:实时 yield text 给 TUI,内部累加 TurnSnapshot 作为 Agent 决策的唯一可信源
  - Plan Mode:`plan_mode=True` 时 `available_tools` 只含 `side_effect=False` 工具
  - v0.4:`Agent.__init__(llm, tools, conversation, permissions, *, config: AppConfig)` —— 不再收字符串 `system_prompt`;`__init__` 内调用一次 `PromptBuilder().build()` 得 `self._prompt`,每轮 `stream(...)` 用 `self._prompt.stable_system` + `augmented_tools` + `cache_breakpoints`,通过 `_inject_reminders(messages, iteration)` 把 env + plan_mode `<system-reminder>` 拼到 `messages[-2]`
- **Prompt 模块契约**（v0.4 新增）:
  - `BuiltPrompt = {stable_system, dynamic_messages, augmented_tools, cache_breakpoints}` —— 一次构建,每轮复用,`stable_system` byte-identical 让 LLM 命中缓存
  - 11 个 sections:7 固定(`identity / constraints / task_mode / action_exec / tool_usage / tone_style / text_output`)+ `env_info`(走 user-role 消息)+ 3 可选(`custom / skills / memory`,空内容跳过)
  - `RuleRegistry.augment_tool(tool)` 在工具 description 前注入被激活规则的 `【必读】/【建议】` 前缀,被禁用的规则整套消失(既不出现在 system 段,也不注入 description)
  - 7 个 DEFAULT_RULES:`edit_requires_read / prefer_specialized_tools / bash_timeout / parallel_limit / error_then_decide / absolute_paths / webfetch_to_file`,全部默认 True,在 `AppConfig.agent.rules` 里可独立开关
  - PlanModeReminder 节奏:iteration 1, `1+plan_reminder_interval`, `1+2*plan_reminder_interval`, ...(默认 interval=5);`agent.enable_system_reminders=False` 时整体跳过注入
- **LLM 缓存接口契约**（v0.4 落地,v0.5+ 后端具体生效）:
  - `LLMClient.stream(messages, system, tools, *, cache_breakpoints=None)` —— cache_breakpoints 是 keyword-only,4 个后端都接受,v0.4 不实际添加 cache_control 标记
  - BuiltPrompt 默认 2 个缓存断点:`CacheBreakpoint("system_start", priority=100)` + `CacheBreakpoint("after_tools", priority=80)`
  - 命中率通过 `UsageStats.cache_read_tokens / (cache_read_tokens + input_tokens)` 计算,`/status` 命令展示
- Bash cwd 三状态机：会话启动锁项目根 → `cd` 跟随 → 每次执行前 `Path.resolve().is_relative_to(project_root)` 防逃逸
- 入口命令：`baozicode`（在 `pyproject.toml` 的 `[project.scripts]` 声明）
- 配置：YAML 写非敏感配置，`.env` 写 API Key，YAML 用 `${ENV_VAR}` 占位符引用；`permissions:` 块可选,`agent.{max_iterations,enable_system_reminders,plan_reminder_interval,rules}` 块可选

## OpenSpec

`openspec/changes/` 下是 spec-driven 的变更提案。已完成 `v0-1 / v0-2 / v0-3-agent-loop`(已归档)。
活跃 / 进行中的变更:`v0-4-prompt`（模块化 system prompt）。
