# BaoZiCode 🥟

> 一个用 Python 开发的命令行 AI 编码助手，类似 Claude Code。

![version](https://img.shields.io/badge/version-0.5.0-blue)

## 是什么

BaoZiCode 是一个跑在终端里的多轮 AI 对话 TUI。它支持：

- 🧠 **多轮上下文** — AI 能记住之前说过的话
- ⚡ **流式响应** — 边收边渲染（代码块自动语法高亮）
- 🔌 **四后端** — Anthropic Claude / OpenAI GPT / MiniMax / DeepSeek，YAML 一键切换
- 🎨 **Textual TUI** — 现代终端界面，输入框、流式输出、ASCII 包子 banner
- 🛠️ **斜杠命令** — `/help` `/clear` `/exit` `/model` `/tools` `/permissions` +
  `/plan` `/do` `/auto` `/stop` `/status`
- 🔁 **Agent Loop（v0.3 核心）** — ReAct 自主循环,一次消息可跨多轮(默认 20 轮),
  自动判断何时停止(模型说完 / 迭代上限 / 取消 / 连续幻觉 / 拒绝累积 / 失败死循环)
- 🧰 **7 个工具** — Read / Write / Edit / Bash / Grep / Glob / WebFetch,side_effect 标记驱动并发调度
- 🔒 **权限控制** — 五层防御(v0.5):L1 黑名单 / L2 沙箱 / L3 规则引擎 / L4 模式(strict/default/permissive) / L5 人在回路
- 📋 **Plan Mode（v0.3）** — `/plan <task>` 先读后规划,`/do` 再切全工具执行
- 🧱 **模块化 system prompt（v0.4 新增）** — 11 段拼装,稳定指令走 LLM 缓存通道,
  动态指令通过 `<system-reminder>` 注入,7 条规则可独立开关

## 当前版本：v0.4

- ✅ 7 个工具 + `side_effect` 标记(`Plan B` 并发调度 + `Plan C` 扩展点)
- ✅ **Agent Loop** — 7 种 `AgentEvent`(text / tool_call / tool_result / usage / progress / done / error)
- ✅ **StreamCollector** — 双路收集:TUI 实时收 text,Agent 决策看 TurnSnapshot 完整源
- ✅ **三层 stop guards** — unknown_tool / deny_threshold / failed_loop
- ✅ **Plan Mode** — `/plan` 只开放 4 个只读工具,`/do` 切回全工具
- ✅ **7 停止条件** — COMPLETED / MAX_ITERATIONS_REACHED / USER_CANCELLED /
  UNKNOWN_TOOL_HALLUCINATION / DENIALS_EXCEEDED / FAILED_TOOL_LOOP / STREAM_ERROR
- ✅ **Token 用量追踪** — per-turn + session total,Anthropic 走 `message_delta.usage`,
  OpenAI 走 `stream_options.include_usage`
- ✅ **模块化 system prompt（v0.4 新增）** — `baozicode/prompt/PromptBuilder.build()`
  一次构建得 `BuiltPrompt{stable_system, dynamic_messages, augmented_tools, cache_breakpoints}`;
  - 7 固定 sections(`identity / constraints / task_mode / action_exec / tool_usage / tone_style / text_output`)
    拼成 `stable_system`(逐轮 byte-identical,LLM 可命中缓存)
  - `env_info` 走 `<system-reminder type="env">` user-role 消息;Plan Mode 时再加
    `<system-reminder type="plan_mode">`,节奏 1, 1+N, 1+2N,...(默认 N=5)
  - `RulesConfig` 控制 7 条默认规则:`edit_requires_read / prefer_specialized_tools /
    bash_timeout / parallel_limit / error_then_decide / absolute_paths / webfetch_to_file`,
    禁用规则整套消失(既不出现在 system 段,也不注入 description 前缀)
  - `LLMClient.stream(..., *, cache_breakpoints=None)` 接口,v0.4 4 个后端接受并忽略,v0.5+ 落地 cache_control
- ✅ **/status 增量** — `input / output / cache_read / cache_write / hit_rate`
  分行显示,命中率 = `round(cache_read / (cache_read + input) * 100, 1)`
- ✅ 进度状态栏(`{iteration}/{max} · {phase}`)+ 底部 mode 切换
- ✅ 11 个斜杠命令 + Esc/Ctrl+C 取消(运行中)或退出(idle)
- ❌ 对话持久化 —— v0.5+

## 安装

需要 Python 3.11+。

```bash
# 1. 克隆 / 进入项目目录
cd BaoZiCode

# 2. 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. 安装依赖（开发模式）
pip install -e .
```

`Grep` 工具在系统装了 `rg`（ripgrep）时优先使用；没装则 fallback 到 Python `re`。

## 配置

```bash
# 1. 复制配置文件模板
cp config.example.yaml config.yaml
cp .env.example .env

# 2. 编辑 .env，填入你的 API Key
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...

# 3. （可选）编辑 config.yaml 切换后端
# backend: anthropic   # 或 openai / minimax / deepseek
# 4. （可选）编辑 config.yaml 配置工具权限
# permissions:
#   auto_allow: [Grep, Glob]
#   deny: []
#   batch_confirm: false
#   bash_locked_cwd: false
```

配置文件查找顺序：
1. `--config <path>` 命令行参数
2. 当前目录 `./config.yaml`
3. `~/.config/baozicode/config.yaml`

## 启动

```bash
baozicode            # 使用默认配置
baozicode -c my.yaml # 使用指定配置文件
python -m baozicode  # 等价
```

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示可用命令 |
| `/clear` | 清空对话历史 + session 用量 |
| `/exit` | 退出（Ctrl+C 同样有效） |
| `/model` | 切换到另一后端 |
| `/tools` | 列出 7 个工具（含 side_effect 标记） |
| `/permissions` | 显示当前生效的权限配置（五层防御版本） |
| `/permissions mode` | 切换权限模式（strict / default / permissive） |
| `/plan [task]` | 进入 plan mode（只读工具）+ 可选运行任务 |
| `/do [task]` | 退出 plan mode（全工具）+ 可选运行任务 |
| `/auto` | 切换 auto 模式（跳过本会话所有 Modal） |
| `/stop` | 取消正在运行的 Agent（Esc / Ctrl+C 同效） |
| `/status` | 显示 mode / backend / model / token 累计 |

**Plan Mode 典型工作流**:
```
/plan refactor auth.py     # 模型用 Read/Grep/Glob/WebFetch 看完文件,给个纯文本计划
…（继续输入约束,直到满意）…
/do                       # 切全工具,开始执行
```

## 工具清单

**只读**（`side_effect=False`,Plan Mode 也暴露）：
- `Read` — 读文本文件（50KB / 2000 行 cap）
- `Grep` — ripgrep / Python re 搜索
- `Glob` — 文件匹配
- `WebFetch` — HTTP 抓取 + HTML 去 tags

**有副作用**（`side_effect=True`,Plan Mode 隐藏）：
- `Write` — 整文件覆写（自动创建父目录）
- `Edit` — `old_string` 精确替换（必须唯一）
- `Bash` — shell 命令（cwd 锁项目根，`cd` 在根内可跟随）

## 权限系统（v0.5 五层防御）

工具调用通过 5 层防御逐级放行,**deny-veto**(任何层 deny 即拒,即使其它层 allow):

```
L1 黑名单  →  L2 沙箱  →  L3 规则  →  L4 mode  →  L5 人在回路
   ↑           ↑           ↑           ↑            ↑
 不可配       不可配     可配        可配         用户决策
 硬拦截     symlink     三层 YAML   strict/      4 档 Modal:
            resolve      合并        default/    Y 仅本次
            前缀判断     (allow/     permissive  A 本会话
                         deny 规则)               P 永久
                                              N 拒绝
```

**L1 黑名单(硬拦截)**:正则匹配 `rm -rf /`、`sudo rm/chmod/chown`、`chmod 777`、
`dd if=...`、`mkfs`、`curl|sh`、fork bomb、`mv / /dev/null`、写 `/etc/passwd`、
写 `~/.ssh/authorized_keys`、`bash -c` / `sh -c` 任何形式。**这层无法被配置放开**,
任何 mode(包括 permissive)都会拦。

**L2 沙箱**:Read/Write/Edit 的 `file_path` 必须 `Path.resolve()` 后落在
`project_root` 内(`is_relative_to` 判断,先 resolve symlink 防逃逸);
Bash 命令中的路径字面量用保守正则提取,任何 shell expansion marker
(`$VAR` / `${VAR}` / `$(...)` / `` ` `` / `~`)整条拒。

**L3 规则引擎**:三层 YAML 按优先级合并,deny 命中立即短路:
- `<project>/.baozicode/permissions.local.yaml` (不入 git,本机优先)
- `<project>/.baozicode/permissions.yaml` (项目级,提交进 git)
- `~/.config/baozicode/permissions.yaml` (用户全局,跨项目)

规则语法示例:
```yaml
mode: default       # strict | default | permissive
rules:
  - tool: Bash
    pattern: "git *"      # fnmatch glob,匹配任一 argument 字符串
    decision: allow
  - tool: Bash
    pattern: "rm *"
    decision: deny
```

**L4 模式档位**:
- `strict` — fallthrough 全部拒(任何规则没覆盖的都直接 is_error)
- `default` — fallthrough 弹 L5 Modal 让用户决定
- `permissive` — fallthrough 全部过(对调试 / 受信任项目友好)

**L5 人在回路**:default mode 下,`PermissionModal` 弹出 4 档选择:
- `Y 仅本次` — 放行这一次
- `A 本会话` — 加 session rule,放行到本次进程结束
- `P 永久` — 追加到 `permissions.local.yaml`,放行到本机所有未来会话
- `N 拒绝` — 拒(is_error 喂回 LLM)

按 P 永久存储的不是精确命令字符串,而是 glob 模糊模式
(如 `npm test --coverage` → `npm test *`)。

**deny 不终止 Agent Loop**:5 层任一 deny 只是把 is_error 喂回 LLM,
`Agent.run()` 不中断。同一工具被连续拒 ≥ `denial_warn_threshold` 次(默认 5),
`<system-reminder type="denial_rate_limit">` 注入 LLM 上下文,提示它调整策略
(改工具 / 改参数 / 询问用户 / 放弃)。

**配置建议**:
- `.baozicode/permissions.local.yaml` 加进 `.gitignore`(本机偏好不进版本控制)
- 项目级规则放 `.baozicode/permissions.yaml` 提交进 git,团队共享

## 项目结构

```
baozicode/
├── __main__.py             # python -m baozicode
├── cli.py                  # argparse 入口
├── app.py                  # Textual App（持有 conversation / llm_client / 当前 agent）
├── agent/                  # v0.3 起 — Agent Loop 与事件契约(v0.4 接 PromptBuilder)
│   ├── events.py           # AgentEvent / StopReason / UsageStats(cache_read/cache_write)/ Progress
│   ├── collector.py        # StreamCollector + TurnSnapshot（双路收集）
│   ├── guards.py           # 三层 stop guards（unknown / deny / failed loop）
│   ├── scheduler.py        # 工具并发调度（side_effect 驱动 batch 切分）
│   └── loop.py             # Agent.run(user_message) → AsyncIterator[AgentEvent]
│                            #   v0.4:__init__ 收 config: AppConfig;_inject_reminders 把
│                            #   <system-reminder> 拼到 messages[-2]
├── prompt/                 # v0.4 新增 — 模块化 system prompt
│   ├── types.py            # BuiltPrompt / BuildContext / CacheBreakpoint / SystemReminder
│   ├── rules.py            # Rule + RuleRegistry + 7 DEFAULT_RULES + augment_tool()
│   ├── reminder.py         # PlanModeReminder(节奏控制)
│   ├── builder.py          # PromptBuilder.build() 一次构建多次复用
│   └── sections/           # 11 个 section renderers(7 固定 + env_info + 3 可选)
├── tui/
│   ├── chat_screen.py      # 主对话屏幕（订阅 Agent 事件流 + 11 slash 命令 + 状态栏）
│   ├── tool_card.py        # ToolCallCard / ToolResultCard
│   ├── permission_modal.py # 高风险工具确认弹窗
│   ├── banner.py           # ASCII 包子
│   └── styles.tcss         # Textual 样式（含 StatusBar）
├── llm/
│   ├── base.py              # LLMClient 抽象 / Message / ContentBlock / ContentDelta
│   ├── anthropic.py         # Anthropic 后端（tool_use + message_delta.usage）
│   ├── openai.py            # OpenAICompatibleBackend 基类 + stream_options.include_usage
│   ├── minimax.py           # MiniMax 后端（OpenAI 兼容）
│   ├── deepseek.py          # DeepSeek 后端（OpenAI 兼容）
│   └── factory.py           # 后端选择
├── tools/                  # 7 个工具 + side_effect 标记
│   ├── base.py              # ToolDefinition (side_effect) / ToolCall / ToolResult
│   ├── read.py / write.py / edit.py / bash.py
│   ├── grep.py / glob.py / webfetch.py
│   └── registry.py          # get_all_tools / execute_tool
├── conversation/
│   └── manager.py          # 多轮历史（add_turn snapshot 重建 / add_tool_result）
└── config/
    ├── schema.py           # Pydantic AppConfig / Permissions / AgentConfig
    └── loader.py           # YAML + .env + ${VAR} 替换
```

## 架构

```
TUI (Textual)
  ├── ChatScreen
  │     ├── async for event in agent.run(text)        # 订阅 Agent 事件流
  │     ├── event type:
  │     │     ├─ text      → Markdown.get_stream.write()
  │     │     ├─ tool_call → ToolCallCard.mount()
  │     │     ├─ tool_result→ ToolResultCard.mount()
  │     │     ├─ progress  → StatusBar.update()
  │     │     ├─ usage     → info card (per-turn + session)
  │     │     └─ done      → 收尾 + 渲染终止原因
  │     ├── /plan / /do / /auto / /stop / /status
  │     └── Esc / Ctrl+C → cancel_agent() (运行中) 或 exit (idle)
  ↓
Agent                                          # 业务逻辑下沉,完全脱离 Textual
  ├── StreamCollector(双路):                  ─┐
  │   ├─ absorb(): async yield text chunks     │
  │   └─ snapshot() → TurnSnapshot             │
  ├── 三层 guards:unknown / deny / failed      │ 纯函数,从 ToolCall + GuardState
  ├── scheduler:side_effect 切 batch            │ 判定,不依赖 LLM/TUI/工具
  │   ├─ parallel batch → asyncio.gather        │
  │   └─ sequential batch → 逐个 await          │
  └── run() → AsyncIterator[AgentEvent]         # 5 种 StopReason
  ↓
ConversationManager  →  LLMClient (抽象)
                          ├─ AnthropicBackend   (message_delta.usage)
                          └─ OpenAICompatibleBackend (stream_options.include_usage)
                              ├─ MiniMaxBackend
                              └─ DeepSeekBackend
                       ↓
Tools registry  →  7 个 ToolDefinition(side_effect) + execute()
                       ↓
                    Config (YAML + .env + ${VAR})
```

依赖单向：UI 不直接 import anthropic / openai；Agent 不依赖 Textual。
模型 SDK 类型不出 `baozicode/llm/`;Textual 类型不出 `baozicode/tui/`。
Agent Loop 是异步生成器,TUI 只是 consumer — Agent 完全可以被 headless 脚本驱动。

## License

MIT
