项目名：BaoZiCode
本地语言：中文

## v0.6 范围

v0.5 之上加一层：MCP (Model Context Protocol) 客户端,启动时自动发现外部 MCP server,
把 server 暴露的工具接进工具中心。两种传输：stdio（子进程管道）和 Streamable HTTP；
三步握手：initialize / notifications/initialized / tools/list；tools/call 在 Agent
调用时跑。失败降级（per-server try/except,banner 警告）；断开时 mark broken +
is_error 返回；`/mcp` slash 命令查看 server 状态 + reconnect。

`baozicode/mcp/manager.McpClientManager` 是编排器,持有 `states: dict[name, ServerState]`
（status / error / tools / session）。Agent 通过 `_default.get_all_tools()` 自动看到
MCP 工具——因为 manager 在 bootstrap 时把每个 MCP 工具都注册到 `ToolRegistry`,
五层防御权限（`_v5_executor`）对 `mcp__<server>__<tool>` 工具同样生效:
- 路径参数（`file_path` / `dir` / `path` 启发式扫到的）走 L2 沙箱
- 默认保守值 `risk=high` / `side_effect=true` / `path_args=[]`（无 schema 时）走 L1 黑名单

`tools/registry.py` v0.6 改为 `ToolRegistry` 类（内置 7 工具 + 运行时 MCP 注入）,
模块级 `_default = ToolRegistry()` 单例 + 顶层函数兼容层,12 个调用点零修改。

## v0.5 范围

v0.4 之上加一层：五层防御权限系统。L1 硬拦截危险命令、L2 路径沙箱、L3 三层 YAML
规则合并、L4 三档 mode、L5 人在回路。deny 不终止 Agent Loop,改为向 LLM 注入
`<system-reminder type="denial_rate_limit">` 提醒调整策略。

`baozicode/permissions/check(call, ctx) -> PermissionDecision` 是 5 层流水线
总入口,由 `Agent._v5_executor` 在每个 tool_call 之前调用;BaoZiCodeApp 在启动时
调 `permissions.bootstrap(project_root, config)` 加载三层 YAML 并构造
`MergedPermissions` + `RuleEngine`。

## 模块结构

```
baozicode/
├── __main__.py             # python -m baozicode
├── cli.py                  # argparse 入口(v0.6:MCP bootstrap + banner)
├── app.py                  # Textual App(v0.5 + v0.6:mcp_manager 字段)
├── agent/                  # v0.3 — Agent Loop 与事件契约(v0.4 + v0.5 5 层权限)
│   ├── events.py           # AgentEvent / StopReason / UsageStats / Progress
│   ├── collector.py        # StreamCollector + TurnSnapshot(双路收集)
│   ├── guards.py           # v0.5:record_denial_warn / should_inject_denial_reminder 替代终止
│   ├── scheduler.py        # 工具并发调度(方案 B + C 扩展点)
│   └── loop.py             # Agent 主循环(v0.5:_v5_executor 调 permissions.check,deny 不终止)
├── permissions/            # v0.5 新增 — 五层防御
│   ├── types.py            # PermissionDecision / PermissionRule / MergedPermissions / PermissionMode
│   ├── blacklist.py        # L1:DangerousCommandBlacklist(TEXT_PATTERNS + token 扫描)
│   ├── sandbox.py          # L2:PathSandbox(real_root + symlink resolve + shell expansion 拦截)
│   ├── persistence.py      # append_rule_to_local_yaml / load_local_yaml(atomic 写)
│   ├── loader.py           # L3:load_permissions_layers(三层 YAML 合并)
│   ├── engine.py           # L3:RuleEngine(check + add_session_rule,deny-veto)
│   ├── mode.py             # L4:apply(decision, mode)(strict/default/permissive)
│   └── __init__.py         # check(call, ctx) 总入口 + bootstrap(project_root, config)
├── prompt/                 # v0.4 新增 — 模块化 system prompt 拼装
│   ├── types.py            # BuiltPrompt / BuildContext / CacheBreakpoint / SystemReminder
│   ├── rules.py            # Rule + RuleRegistry + 7 DEFAULT_RULES + augment_tool()
│   ├── reminder.py         # PlanModeReminder(节奏:1, 1+N, 1+2N, ...)
│   ├── builder.py          # PromptBuilder.build() 一次构建多次复用
│   ├── sections/           # 11 个 section renderer(7 固定 + env_info + 3 可选)
│   └── __init__.py         # 公开 API re-export
├── tui/
│   ├── chat_screen.py      # 主对话屏幕 + Agent 事件订阅 + slash 命令(v0.5:/permissions mode;v0.6:/mcp)
│   ├── tool_card.py        # ToolCallCard / ToolResultCard 组件
│   ├── permission_modal.py # v0.5:4 档 Modal(Y/A/P/N) + derive_glob_pattern
│   ├── banner.py           # ASCII 包子
│   └── styles.tcss         # Textual 样式
├── llm/
│   ├── base.py             # LLMClient ABC、Message、ContentBlock、ContentDelta
│   ├── anthropic.py        # AnthropicBackend + message_delta.usage 捕获
│   ├── openai.py           # OpenAICompatibleBackend 基类 + include_usage 兼容
│   ├── minimax.py          # MiniMaxBackend(OpenAI 兼容)
│   ├── deepseek.py         # DeepSeekBackend(OpenAI 兼容)
│   └── factory.py          # create_client(config) → LLMClient
├── tools/                  # 7 工具 + side_effect + path_args(v0.5) 标记
│   ├── base.py             # ToolDefinition(side_effect + path_args)/ ToolCall / ToolResult
│   ├── read.py             # side_effect=False, path_args=["file_path"]
│   ├── write.py            # side_effect=True, path_args=["file_path"]
│   ├── edit.py             # side_effect=True, path_args=["file_path"]
│   ├── bash.py             # side_effect=True, path_args=[] (L2 用 regex 提取)
│   ├── grep.py             # side_effect=False, path_args=["path"]
│   ├── glob.py             # side_effect=False, path_args=["path"]
│   ├── webfetch.py         # side_effect=False, path_args=[]
│   └── registry.py         # v0.6:ToolRegistry 类 + 模块级兼容层(支持 MCP 运行时注入)
├── mcp/                    # v0.6 新增 — MCP 客户端
│   ├── types.py            # JsonRpcRequest/Response/Error/Notification + McpTool/McpCallResult
│   ├── jsonrpc.py          # JsonRpcDispatcher(请求/响应 id 配对)
│   ├── transport_stdio.py  # StdioTransport(子进程管道 + stderr drain task)
│   ├── transport_http.py   # HttpTransport(Streamable HTTP + SSE + Mcp-Session-Id)
│   ├── client.py           # McpSession(initialize → initialized → tools/list)
│   ├── adapter.py          # MCP ↔ ToolDefinition / ToolResult 转换(路径 args 启发式)
│   └── manager.py          # McpClientManager(多 server 生命周期 + 失败降级)
├── conversation/
│   └── manager.py          # 多轮历史(add_turn snapshot 重建,add_tool_result)
└── config/
    ├── schema.py           # Pydantic AppConfig / BackendConfig / Permissions / AgentConfig / RulesConfig
    │                        #   v0.5 新增:PermissionRuleYaml / PermissionsV5 / AgentConfig.denial_warn_threshold
    │                        #   v0.6 新增:McpServerStdioConfig / McpServerHttpConfig / AppConfig.mcp_servers
    └── loader.py           # YAML + .env + ${VAR} 替换(v0.5:扫 permissions*.yaml sidecar 合并;
                             #   v0.6:两层 mcp_servers 合并 + ${VAR} 展开)
```

## 依赖方向（不要打破）

```
tui/  →  agent/  →  prompt/  →  llm/base.py → tools/base.py
   │       │          │            │             │
   │       └──────────┴────────────┘             │
   ↓                                             ↓
config/  ←──── llm/{anthropic,openai,minimax,deepseek}.py
   ↑
   └── conversation/ ──→ llm/base.py
   ↑
permissions/ ───→ config/ + tools/base.py          (v0.5 新增单向依赖)
   │
   └── 可选 → agent/(通过 PermissionCallback / MergedPermissions 类型)

   tui 不直接 import agent 业务逻辑;
   agent 可 import llm base + conversation manager + tools base / registry + prompt/ + permissions/
   prompt/ 可 import llm base + tools base + config schema(appconfig-only 类型注解)
   permissions/ 可 import config schema + tools/base.py + agent types(可选)
```

- `tui/` 不直接 import `anthropic` / `openai`（必须经 `llm/factory.py`）
- `llm/` 不能 import `textual` 或 `prompt/`（避免反向依赖循环）
- `prompt/` 不依赖 `agent/` 或 `tui/`（被 agent 单向调用）
- `agent/` 不依赖 `tui/`（v0.3 把 Agent 与 TUI 解耦,Agent 只推事件流）
- `tools/` 不依赖 `tui/` / `llm/` / `conversation/` / `agent/`（纯函数 + 路径）
- `permissions/` 不依赖 `agent/` 业务代码,只共享 `tools/base.py` 的 `ToolCall` 类型;
  `agent/` 可选依赖 `permissions/` 的 `MergedPermissions` / `PermissionMode` 类型
- 业务状态挂在 `App` 实例上，Screen 通过 `self.app` 访问

## 关键约定

- LLM 抽象：`LLMClient.stream(messages, system, tools) -> AsyncIterator[ContentDelta]`
- `ContentDelta.type`：`"text"` → str；`"tool_use"` → `ToolCall` 实例；`"usage"` → `UsageStats`
- Anthropic 的 `system` 走独立参数；OpenAI 走 `messages[0] role=system` —— 差异在每个 backend 内部消化
- `Message.content` 是 `Union[str, list[ContentBlock]]`：`str` 是 v0.1 快速路径；`list[ContentBlock]` 含 `TextBlock / ToolUseBlock / ToolResultBlock`
- 工具调用统一抽象：`ToolDefinition`（喂给 LLM）+ `ToolCall`（LLM 请求）+ `ToolResult`（喂回 LLM），后端 SDK 类型不出 `baozicode/llm/`
- 工具 `side_effect: bool`：并发调度的唯一信号(`True` 串行,`False` 可并行)
- 工具 `path_args: list[str]`：v0.5 新增 — L2 PathSandbox 提取路径用的 argument 名
  (Read/Write/Edit = `["file_path"]`,Grep/Glob = `["path"]`,Bash/WebFetch = `[]`)
- **Agent Loop 契约**（v0.3 核心 + v0.4 PromptBuilder 集成 + v0.5 五层防御）:
  - `Agent.run(user_message) -> AsyncIterator[AgentEvent]`,7 种事件类型:`text / tool_call / tool_result / usage / progress / done / error`
  - 7 种停止条件:`COMPLETED / MAX_ITERATIONS_REACHED / USER_CANCELLED / UNKNOWN_TOOL_HALLUCINATION / DENIALS_EXCEEDED / FAILED_TOOL_LOOP / STREAM_ERROR`
    - v0.5:`DENIALS_EXCEEDED` 不再是终止路径(deny 不终止 loop),但 enum 仍保留兼容
  - StreamCollector 双路:实时 yield text 给 TUI,内部累加 TurnSnapshot 作为 Agent 决策的唯一可信源
  - Plan Mode:`plan_mode=True` 时 `available_tools` 只含 `side_effect=False` 工具
  - v0.4:`Agent.__init__(llm, tools, conversation, permissions, *, config: AppConfig)` —— 不再收字符串 `system_prompt`;`__init__` 内调用一次 `PromptBuilder().build()` 得 `self._prompt`,每轮 `stream(...)` 用 `self._prompt.stable_system` + `augmented_tools` + `cache_breakpoints`,通过 `_inject_reminders(messages, iteration)` 把 env + plan_mode `<system-reminder>` 拼到 `messages[-2]`
  - v0.5:`Agent.__init__(..., *, session_mode, merged_permissions, permissions_engine)` 收 v0.5 5 层防御句柄;`_v5_executor` 调 `permissions.check(call, self._merged)`,deny 走 `_DENIAL_REMINDER_BODY` 注入,fallthrough 走 `_handle_user_decision` (调 `permission_callback` 即 L5 Modal)
- **五层防御权限契约**（v0.5 新增）:
  - `PermissionDecision = {decision: allow|deny|fallthrough, layer: L1_blacklist|L2_sandbox|L3_rule|L4_mode|L5_user|none, reason, matched_pattern, scope}`
  - `MergedPermissions = {rules, mode, sources_loaded, real_root, path_sandbox_enabled, session_rules}`
    - `session_rules` 由 SESSION Modal 放行累积,实际写入 `merged.session_rules`,
      `check()` 每次构造新 RuleEngine 都从 `merged.session_rules` 读
  - `RuleEngine.check(call)` 走两遍扫描:先 deny(短路),后 allow;同 (tool, pattern) 优先级 `session < local < project < user_global`
  - 三层 YAML 路径:`<project>/.baozicode/permissions.local.yaml` > `<project>/.baozicode/permissions.yaml` > `~/.config/baozicode/permissions.yaml`
  - `permission_callback` L5 user 决策返回 `PermissionChoice`(ONCE / SESSION / PERSISTENT / DENY);SESSION 走 `engine.add_session_rule`,PERSISTENT 走 `persistence.append_rule_to_local_yaml`,pattern 由 `derive_glob_pattern(call)` 模糊化
  - L1 硬黑名单(rm -rf / / sudo / chmod 777 / dd / mkfs / curl|sh / fork bomb / /etc/passwd / bash -c)无法被配置覆盖,任何 mode(包括 permissive)都拦
  - L2 沙箱:Read/Write/Edit 用 `ToolDefinition.path_args` 提路径;Bash 用保守 regex 提路径字面量;任何 shell expansion marker (`$VAR` / `${VAR}` / `$(...)` / `` ` `` / `~`) 整条拒
  - L4 `PermissionMode = strict | default | permissive`;`apply(decision, mode)` 把 fallthrough 转成 deny(严格)/ fallthrough(default)/ allow(放行);allow/deny 直通(纵深防御)
  - L5 人在回路:default mode 下 fallthrough 弹 4 档 Modal;`/auto` 跳 Modal 视为 ONCE
- **Prompt 模块契约**（v0.4 新增）:
  - `BuiltPrompt = {stable_system, dynamic_messages, augmented_tools, cache_breakpoints}` —— 一次构建,每轮复用,`stable_system` byte-identical 让 LLM 命中缓存
  - 11 个 sections:7 固定(`identity / constraints / task_mode / action_exec / tool_usage / tone_style / text_output`)+ `env_info`(走 user-role 消息)+ 3 可选(`custom / skills / memory`,空内容跳过)
  - `RuleRegistry.augment_tool(tool)` 在工具 description 前注入被激活规则的 `【必读】/【建议】` 前缀,被禁用的规则整套消失(既不出现在 system 段,也不注入 description)
  - 7 个 DEFAULT_RULES:`edit_requires_read / prefer_specialized_tools / bash_timeout / parallel_limit / error_then_decide / absolute_paths / webfetch_to_file`,全部默认 True,在 `AppConfig.agent.rules` 里可独立开关
  - PlanModeReminder 节奏:iteration 1, `1+plan_reminder_interval`, `1+2*plan_reminder_interval`, ...(默认 interval=5);`agent.enable_system_reminders=False` 时整体跳过注入
  - v0.5:`_inject_reminders` 还注入 `<system-reminder type="denial_rate_limit">` 当任一工具 `deny_counts[name] >= denial_warn_threshold`(默认 5)
- **LLM 缓存接口契约**（v0.4 落地,v0.5+ 后端具体生效）:
  - `LLMClient.stream(messages, system, tools, *, cache_breakpoints=None)` —— cache_breakpoints 是 keyword-only,4 个后端都接受,v0.4 不实际添加 cache_control 标记
  - BuiltPrompt 默认 2 个缓存断点:`CacheBreakpoint("system_start", priority=100)` + `CacheBreakpoint("after_tools", priority=80)`
  - 命中率通过 `UsageStats.cache_read_tokens / (cache_read_tokens + input_tokens)` 计算,`/status` 命令展示
- Bash cwd 三状态机：会话启动锁项目根 → `cd` 跟随 → 每次执行前 `Path.resolve().is_relative_to(project_root)` 防逃逸
- 入口命令：`baozicode`（在 `pyproject.toml` 的 `[project.scripts]` 声明）
- 配置：YAML 写非敏感配置，`.env` 写 API Key，YAML 用 `${ENV_VAR}` 占位符引用；
  v0.2 旧 `permissions:` 块可选,v0.5 新 `permissions_v5:` 块可选;`agent.{max_iterations,enable_system_reminders,plan_reminder_interval,denial_warn_threshold,rules}` 块可选;
  sidecar `permissions*.yaml` 在主 config 同目录自动合并

## OpenSpec

`openspec/changes/` 下是 spec-driven 的变更提案。已完成 `v0-1 / v0-2 / v0-3-agent-loop / v0-4-prompt / v0-5-permissions`(已归档)。
活跃 / 进行中的变更:`v0-6-mcp-client`（MCP 客户端,自动发现外部 server + 工具接入）。
