# BaoZiCode 🥟

> 一个用 Python 开发的命令行 AI 编码助手，类似 Claude Code。

![version](https://img.shields.io/badge/version-1.5.0-blue)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

## 是什么

BaoZiCode 是跑在终端里的多轮 AI 对话 TUI。它把 LLM、工具调用、权限、记忆、技能、子 Agent 委派、团队协作全部串在一条 ReAct 循环里 —— 一条 user 消息能自主跑多轮工具调用直到模型认为完成为止。

**核心能力**

- 🧠 **多轮上下文** + ⚡ **流式响应**（代码块自动语法高亮）
- 🔌 **四后端** — Anthropic Claude / OpenAI GPT / MiniMax / DeepSeek，YAML 一键切换
- 🎨 **Textual TUI** — 现代终端界面，状态栏实时进度，ASCII 包子 banner
- 🔁 **Agent Loop** — 自主多轮 ReAct，8 种停止条件，工具并发调度
- 🧰 **7 工具 + Skill 系统** — 内置 Read/Write/Edit/Bash/Grep/Glob/WebFetch + 可挂载的 Skill 能力包
- 🤖 **SubAgent 委派** — 主 Agent 通过 `task` 工具派子任务，独立上下文 + 受限工具集 + 后台异步
- 👥 **Team 协作** — Lead / Member / Coordinator 三角色，Mailbox 通信 + 多 Lead 协同
- 🛡️ **五层防御权限** — 黑名单 / 沙箱 / 规则 / 模式 / 人在回路，deny 不终止 loop
- 📦 **长期记忆** — 项目指令 + 会话存档 + 自动笔记，三机制串联
- 🪝 **Hooks 生命周期** — 11 个事件点上挂规则，做格式化 / 拦截 / 注入

**当前版本：v1.5 (SubAgent 修复)** —— 修复 v1.2 SubAgent 的两个 bug：`tools: []` 显式空工具集合法放行；同步派发路径删除，`async_=True` 成为唯一路径。详见 [docs/features/v1.5-subagent-fixes.md](./docs/features/v1.5-subagent-fixes.md)。

## 安装

需要 Python 3.11+。

```bash
cd BaoZiCode
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

`Grep` 工具在系统装了 `rg`（ripgrep）时优先使用；没装则 fallback 到 Python `re`。

## 配置

**配置文件查找顺序**（高优先级覆盖低优先级）：

1. `--config <path>` 命令行参数
2. 当前目录 `./config.yaml`
3. `~/.config/baozicode/config.yaml`

**最小配置**：

```bash
cp config.example.yaml config.yaml
cp .env.example .env
# 编辑 .env 填 API Key
```

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
# 或
OPENAI_API_KEY=sk-...
```

```yaml
# config.yaml
backend: anthropic              # anthropic | openai | minimax | deepseek
```

### 配置块速查

| 块 | 作用 | 默认 | 详细 |
|---|---|---|---|
| `backend` | LLM 后端选择 | `anthropic` | — |
| `agent.context_window_tokens` | 模型上下文窗口大小（压触发用） | 128000 | [v0.7](./docs/features/v0.7-context.md) |
| `agent.compaction.*` | 上下文压缩阈值 | 见下 | [v0.7](./docs/features/v0.7-context.md) |
| `agent.rules.*` | 7 条默认规则独立开关 | 全 True | [v1.0](./docs/features/v1.0-skills.md)（prompt 段） |
| `agent.max_iterations` | Agent Loop 单消息最多跑几轮 | 20 | — |
| `agent.denial_warn_threshold` | 同一工具连续拒几次后注入提醒 | 5 | [v0.5](./docs/features/v0.5-permissions.md) |
| `permissions.mode` | L4 模式档位 | `default` | [v0.5](./docs/features/v0.5-permissions.md) |
| `permissions.rules` | L3 规则列表（allow / deny） | `[]` | [v0.5](./docs/features/v0.5-permissions.md) |
| `mcp_servers` | MCP server 列表（stdio / http） | `{}` | [v0.6](./docs/features/v0.6-mcp.md) |
| `skills.enabled` | Skill 系统总开关 | `true` | [v1.0](./docs/features/v1.0-skills.md) |
| `skills.user_dir` / `project_dir` / `builtin_dir` | Skill 存放目录 | 见文件 | [v1.0](./docs/features/v1.0-skills.md) |
| `instructions.*` | 三层 BaoZiCode.md 加载行为 | 开启 | [v0.8](./docs/features/v0.8-memory-sessions.md) |
| `memory.*` | 双层长期记忆 | 开启 | [v0.8](./docs/features/v0.8-memory-sessions.md) |
| `sessions.*` | JSONL 会话存档 + resume | 开启 / 30 天保留 | [v0.8](./docs/features/v0.8-memory-sessions.md) |
| `hooks` | Hook 规则列表 | `[]` | [v1.1](./docs/features/v1.1-hooks.md) |
| `subagents.worktree.enabled` | v1.3 worktree 隔离开关 | `false` | [v1.3](./docs/features/v1.3-worktree.md) |
| `teams.enabled` / `teams.dir` | Team 系统开关与存放目录 | 开启 | [v1.4](./docs/features/v1.4-team.md) |
| `teams.coordinator.enabled` | Coordinator 模式三锁门之配置锁 | `false` | [v1.4](./docs/features/v1.4-team.md) |

**完整 AppConfig schema** 见 [`baozicode/config/schema.py`](./baozicode/config/schema.py)。

**YAML 引用环境变量**：`${ANTHROPIC_API_KEY}` 在启动时替换为 `.env` / 系统环境变量值。

## 启动

```bash
baozicode                          # 默认配置 + 弹 session 选择器（有历史时）
baozicode -c my.yaml               # 指定配置文件
baozicode --new                    # 强制开新 session
baozicode --resume 20260101-120000-abcd  # 续接指定 session
baozicode --no-banner              # 抑制启动 banner
python -m baozicode                # 等价入口
```

启动时 stderr 打印三行 banner（可用 `--no-banner` 关闭）：

```
[BaoZiCode] 指令: 2 layers loaded (BaoZiCode.md + .baozicode/BaoZiCode.md)
[BaoZiCode] 记忆: 5 notes (user: 3, project: 2), index: 12 lines / 1024 bytes (state: NORMAL)
[BaoZiCode] 会话: 7 sessions found, latest: 20260101-120000-abcd (旧对话)
```

## 斜杠命令（v0.9 注册中心，11 个）

| 命令 | 类型 | 说明 |
|------|------|------|
| `/help` | LOCAL | 列出 11 个内置命令 |
| `/clear` | UI_STATE | 清空对话历史 + session 用量 + 已激活 Skill + hook 注入状态 |
| `/compact` | UI_STATE | 手动触发上下文压缩（v0.7 Layer 1 + Layer 2） |
| `/plan` | UI_STATE | 切 plan_mode=True（严格动词，args 忽略） |
| `/do` | UI_STATE | 切 plan_mode=False（严格动词） |
| `/session` | UI_STATE | 弹选择器：恢复某 sid / 开新 / 取消 |
| `/memory` | LOCAL | 查看 user / project 两层 memory 状态 |
| `/permission [mode]` | UI_STATE | 显示 / 切换 strict / default / permissive |
| `/status` | LOCAL | mode + backend + token + session_id + memory 摘要 |
| `/review [<since>]` | PROMPT | 让 Agent 审查自 `{since}` 起的改动 |
| `/skill list\|<name> [args]\|clear` | UI_STATE | 列出 / 加载 / 清空已激活 Skill（v1.0） |

**别名**：`/permissions` = `/permission`（兼容 v0.5-v0.6 拼写）。

**v0.9 删除命令迁移**：`/exit` → `Ctrl+C` / `/model` → 改 config 重启 / `/tools` → 合并入 `/status` / `/mcp` → 启动 banner / `/stop` → `Ctrl+C` / `/auto` → `/permission mode permissive` / `/resume` `/new` → 合入 `/session`。

详细见 [docs/features/v0.9-commands.md](./docs/features/v0.9-commands.md)。

### Plan Mode 典型工作流

```
/plan refactor auth.py     # 模型用 Read/Grep/Glob/WebFetch 看完文件，给个纯文本计划
…（继续输入约束，直到满意）…
/do                        # 切全工具，开始执行
```

## 工具清单（7 内置 + 2 系统）

**只读**（`side_effect=False`，Plan Mode 也暴露）：

| 工具 | 作用 |
|------|------|
| `Read` | 读文本文件（50KB / 2000 行 cap） |
| `Grep` | ripgrep / Python re 搜索 |
| `Glob` | 文件匹配 |
| `WebFetch` | HTTP 抓取 + HTML 去 tags |

**有副作用**（`side_effect=True`，Plan Mode 隐藏）：

| 工具 | 作用 |
|------|------|
| `Write` | 整文件覆写（自动创建父目录） |
| `Edit` | `old_string` 精确替换（必须唯一） |
| `Bash` | shell 命令（cwd 锁项目根） |

**系统级**（`tool_type="internal"`，不受白名单 / Plan Mode 约束）：

| 工具 | 作用 | 引入版本 |
|------|------|---------|
| `load_skill` | Skill 加载器，按需钉入 body 到 active_skills reminder | v1.0 |
| `task` | SubAgent 派发器（`type="definition"\|"fork"` + `role` + `prompt` + `async=true`） | v1.2 |

**Team 协作**（仅 `role='lead'` / `'coordinator'` 可见）：`team_dispatch` / `team_send_message` / `team_cancel` / `team_merge` / `team_task_create` / `team_task_query`。详见 [docs/features/v1.4-team.md](./docs/features/v1.4-team.md)。

## 功能索引

每个版本的能力详解都拆到了 `docs/features/`，主 README 不再重复：

| 版本 | 功能 | 文档 |
|------|------|------|
| v0.1-0.4 | 多轮对话 / 流式 / 四后端 / Agent Loop / 模块化 system prompt | （基础能力） |
| v0.5 | 五层防御权限系统 | [v0.5-permissions.md](./docs/features/v0.5-permissions.md) |
| v0.6 | MCP 客户端集成 | [v0.6-mcp.md](./docs/features/v0.6-mcp.md) |
| v0.7 | 两层上下文压缩 | [v0.7-context.md](./docs/features/v0.7-context.md) |
| v0.8 | 项目指令 / 长期记忆 / 会话存档 | [v0.8-memory-sessions.md](./docs/features/v0.8-memory-sessions.md) |
| v0.9 | Slash 命令注册中心 | [v0.9-commands.md](./docs/features/v0.9-commands.md) |
| v1.0 | Skill 系统（两阶段加载 + 双层白名单） | [v1.0-skills.md](./docs/features/v1.0-skills.md) |
| v1.1 | Hooks 生命周期 | [v1.1-hooks.md](./docs/features/v1.1-hooks.md) |
| v1.2 | SubAgent 委派 | [v1.2-subagent.md](./docs/features/v1.2-subagent.md) |
| v1.3 | Worktree 隔离 | [v1.3-worktree.md](./docs/features/v1.3-worktree.md) |
| v1.4 | Team 协作（Lead / Member / Coordinator） | [v1.4-team.md](./docs/features/v1.4-team.md) |
| v1.5 | SubAgent 修复（`tools: []` + async-only） | [v1.5-subagent-fixes.md](./docs/features/v1.5-subagent-fixes.md) |

升级迁移步骤见 [`docs/migrations/`](./docs/migrations/)。

## 架构

```
TUI (Textual)
  └── ChatScreen
        ├── async for event in agent.run(text)   # 订阅 AgentEvent 流
        ├── text / tool_call / tool_result / progress / usage / done
        └── 11 个 /command + Esc / Ctrl+C

Agent                                          # 业务逻辑下沉，完全脱离 Textual
  ├── StreamCollector（双路：实时 yield + snapshot 决策源）
  ├── 三层 guards（unknown_tool / denial / failed_loop）
  ├── scheduler（side_effect 切 batch：parallel / sequential）
  └── run() → AsyncIterator[AgentEvent]         # 8 种 StopReason

WorktreeManager (v1.3, optional)                # isolation: worktree 角色走这里
  ├─ create → git worktree add + Initializer 4 步
  ├─ exit → 决策树（干净删 / dirty 留 detached）
  └─ CleanupDaemon 后台三层过滤

ConversationManager  →  LLMClient（抽象）
                          ├─ AnthropicBackend（message_delta.usage）
                          └─ OpenAICompatibleBackend（stream_options.include_usage）
                              ├─ OpenAI
                              ├─ MiniMax
                              └─ DeepSeek

Tools registry
  ├── 7 个 ToolDefinition（side_effect + path_args + tool_type）
  ├── load_skill (v1.0, internal)
  ├── task (v1.2, internal) → SubAgentManager
  └── 6 × team_* (v1.4, role_visibility=lead/coordinator)

Permissions (v0.5 五层防御)                     # _v5_executor 每 tool_call 前调
  L1 黑名单 → L2 沙箱 → L3 规则 → L4 mode → L5 人在回路

Config (YAML + .env + ${VAR})                  # loader 启动期一次加载
```

**依赖单向**：UI 不直接 import anthropic / openai；Agent 不依赖 Textual；模型 SDK 类型不出 `baozicode/llm/`；Textual 类型不出 `baozicode/tui/`。Agent Loop 是异步生成器，TUI 只是 consumer —— Agent 完全可以被 headless 脚本驱动。

## 项目结构

```
baozicode/
├── __main__.py             # python -m baozicode
├── cli.py                  # argparse 入口（--config / --resume / --new / --no-banner）
├── app.py                  # Textual App（所有版本字段累加：conversation / llm / permissions / mcp / memory / skills / teams / hooks ...）
│
├── agent/                  # v0.3 — Agent Loop 与事件契约
│   ├── events.py           # AgentEvent / StopReason(8种) / UsageStats(cache_read/cache_write) / Progress
│   ├── collector.py        # StreamCollector + TurnSnapshot（双路收集）
│   ├── guards.py           # 三层 stop guards
│   ├── scheduler.py        # 工具并发调度（side_effect 驱动 batch 切分）
│   └── loop.py             # Agent.run(user_message) → AsyncIterator[AgentEvent]
│
├── permissions/            # v0.5 — 五层防御
│   ├── types.py            # PermissionDecision / PermissionRule / MergedPermissions / PermissionMode
│   ├── blacklist.py        # L1: DangerousCommandBlacklist
│   ├── sandbox.py          # L2: PathSandbox(real_root + symlink resolve + shell expansion 拦截)
│   ├── loader.py           # L3: load_permissions_layers（三层 YAML 合并）
│   ├── engine.py           # L3: RuleEngine（deny-veto）
│   ├── mode.py             # L4: apply(decision, mode)
│   └── __init__.py         # check(call, ctx) 总入口 + bootstrap
│
├── prompt/                 # v0.4 — 模块化 system prompt
│   ├── types.py            # BuiltPrompt / BuildContext / CacheBreakpoint / SystemReminder
│   ├── rules.py            # Rule + RuleRegistry + 7 DEFAULT_RULES + augment_tool
│   ├── reminder.py         # PlanModeReminder / denial_rate_limit / active_skills / post_compaction
│   ├── builder.py          # PromptBuilder.build() + set_dynamic_section
│   └── sections/           # 11 个 section renderer（7 固定 + env_info + 3 可选）
│
├── skills/                 # v1.0 — Skill 系统
│   ├── schema.py           # SkillFrontmatter (Pydantic) / SkillDef
│   ├── registry.py         # SkillRegistry（三级 scan + 合并 + reload）
│   ├── activation.py       # SkillActivation（render_active_section + clear）
│   ├── loader.py           # SkillLoader（load_skill + 占位符替换）
│   ├── execution.py        # SkillExecutor（shared / independent 走 SubAgent 通道）
│   ├── whitelist.py        # SkillWhitelistFilter（L2 union + internal 豁免）
│   ├── bootstrap.py        # bootstrap_skills + SkillSet
│   └── builtin/            # 3 个内置 Skill
│       ├── commit/  review/  test/
│
├── agents/                 # v1.2 — SubAgent Delegation（v1.5 修复）
│   ├── schema.py           # AgentFrontmatter (Pydantic, v1.3 +isolation)
│   ├── registry.py         # AgentRegistry（三级 scan + plugin 合并）
│   ├── filter.py           # ToolFilter（4 层 AND + _l2_explicit_empty 状态位）
│   ├── runtime.py          # SubAgentRuntime（spawn + 状态隔离 + BuiltPrompt 分流）
│   ├── manager.py          # SubAgentManager（dispatch async_=True 唯一 + cascade cancel）
│   └── builtin/            # explorer / summarizer（summarizer 用 tools: []）
│
├── worktree/               # v1.3 — Git Worktree 隔离层
│   ├── schema.py           # WorktreeSpec / WorktreeState / PathValidator
│   ├── manager.py          # WorktreeManager（create / enter / exit / remove + fast-path）
│   ├── initializer.py      # WorktreeInitializer 4 步
│   └── cleanup.py          # CleanupDaemon（三层过滤）
│
├── hooks/                  # v1.1 — Hooks 生命周期
│   ├── registry.py         # HookRegistry + freeze()
│   ├── loader.py           # HookLoader（条件语法 + 6 种 action）
│   ├── executor.py         # HookExecutor（fail-open + 11 事件）
│   ├── audit.py            # HookAuditLog（异步 JSONL + 100MB 轮转）
│   └── schema.py           # HookDef / HookEvent / HookAction
│
├── teams/                  # v1.4 — Team Foundation + Tools + Pane Backend + Coordinator
│   ├── schema.py           # Team / Member / Message frozen dataclass + 8 错误枚举
│   ├── mailbox.py          # 原子 JSONL append + state.json + wake.signal
│   ├── lockfile.py         # 跨平台 mailbox_lock（POSIX fcntl / Windows msvcrt）
│   ├── store.py / registry.py    # TeamStore / TeamsRegistry
│   ├── cli.py              # baozicode team create/list/show/use/destroy + member run
│   ├── coordinator.py      # 三锁门 + 角色过滤
│   ├── tasks.py            # 共享任务清单 + DAG cycle 检测
│   ├── tools.py            # 6 个 Lead-only team_* 协作工具
│   ├── approval.py         # PLAN/END / APPROVED/REJECTED 协议
│   ├── mailbox_notifier.py # 每轮扫 outbox → system-reminder
│   ├── merge.py            # run_team_merge（顺序合 worktree 分支）
│   ├── pane.py             # 5 BackendType + BackendHandle Protocol
│   ├── backend_manager.py  # BackendManager 居中调度
│   ├── pane_info.py        # 持久化跨 Lead restart
│   ├── member_agent.py     # MemberAgent + MailboxLayer
│   └── member_loop.py      # MemberMainLoop 长生命周期 polling
│
├── tui/
│   ├── chat_screen.py      # 主对话屏幕（订阅 Agent 事件 + Skill 注入 + 斜杠分发）
│   ├── startup_session_screen.py  # v0.8 — 启动 session 选择器
│   ├── tool_card.py        # ToolCallCard / ToolResultCard
│   ├── permission_modal.py # v0.5 — 4 档 Modal (Y/A/P/N)
│   ├── banner.py           # ASCII 包子
│   └── styles.tcss         # Textual 样式
│
├── llm/
│   ├── base.py             # LLMClient ABC / Message / ContentBlock / ContentDelta
│   ├── anthropic.py        # AnthropicBackend（tool_use + message_delta.usage）
│   ├── openai.py           # OpenAICompatibleBackend 基类
│   ├── minimax.py          # MiniMaxBackend
│   ├── deepseek.py         # DeepSeekBackend
│   └── factory.py          # create_client(config)
│
├── tools/                  # 7 工具 + side_effect + path_args + tool_type
│   ├── base.py             # ToolDefinition / ToolCall / ToolResult
│   ├── read.py / write.py / edit.py / bash.py / grep.py / glob.py / webfetch.py
│   └── registry.py         # ToolRegistry + 模块级兼容层（运行时 MCP + Skill 注入）
│
├── mcp/                    # v0.6 — MCP 客户端
│   ├── types.py / jsonrpc.py / transport_stdio.py / transport_http.py
│   ├── client.py           # McpSession（initialize → initialized → tools/list）
│   ├── adapter.py          # MCP ↔ ToolDefinition / ToolResult
│   └── manager.py          # McpClientManager（多 server + 失败降级）
│
├── context/                # v0.7 — 两层 token 预算压缩
│   ├── schema.py / boundary.py / estimator.py
│   ├── layer1.py           # offload（写盘 + preview）
│   ├── layer2.py           # 摘要（LLM 6 段结构化 + 熔断）
│   ├── orchestrator.py     # maybe_compact
│   └── storage.py          # .baozicode/context/<session>/
│
├── instructions/           # v0.8 — 三层 BaoZiCode.md 加载
│   ├── schema.py / loader.py / include.py
│
├── memory/                 # v0.8 — 双层长期记忆
│   ├── schema.py / store.py / updater.py / overflow.py / prompt.py
│
├── sessions/               # v0.8 — JSONL 会话存档 + resume
│   ├── schema.py / _id.py / archive.py / cleanup.py / resume.py
│
├── commands/               # v0.9 — Slash 命令注册中心
│   ├── registry.py / context.py / dispatcher.py / completor.py / builtin.py
│
├── conversation/
│   └── manager.py          # 多轮历史（add_turn snapshot 重建 + add_tool_result）
│
└── config/
    ├── schema.py           # Pydantic AppConfig（v0.5-v1.5 累计字段）
    └── loader.py           # YAML + .env + ${VAR} 替换 + sidecar 合并
```

## License

MIT