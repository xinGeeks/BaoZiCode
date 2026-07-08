# BaoZiCode 🥟

> 一个用 Python 开发的命令行 AI 编码助手，类似 Claude Code。

![version](https://img.shields.io/badge/version-0.8.0-blue)

## 是什么

BaoZiCode 是一个跑在终端里的多轮 AI 对话 TUI。它支持：

- 🧠 **多轮上下文** — AI 能记住之前说过的话
- ⚡ **流式响应** — 边收边渲染（代码块自动语法高亮）
- 🔌 **四后端** — Anthropic Claude / OpenAI GPT / MiniMax / DeepSeek，YAML 一键切换
- 🎨 **Textual TUI** — 现代终端界面，输入框、流式输出、ASCII 包子 banner
- 🛠️ **斜杠命令** — `/help` `/clear` `/exit` `/model` `/tools` `/permissions` +
  `/plan` `/do` `/auto` `/stop` `/status` `/mcp` `/compact` +
  `/resume` `/memory` `/new`
- 🔁 **Agent Loop（v0.3 核心）** — ReAct 自主循环,一次消息可跨多轮(默认 20 轮),
  自动判断何时停止(模型说完 / 迭代上限 / 取消 / 连续幻觉 / 拒绝累积 / 失败死循环)
- 🧰 **7 个工具** — Read / Write / Edit / Bash / Grep / Glob / WebFetch,side_effect 标记驱动并发调度
- 🔒 **权限控制** — 五层防御(v0.5):L1 黑名单 / L2 沙箱 / L3 规则引擎 / L4 模式(strict/default/permissive) / L5 人在回路
- 🔌 **MCP 集成（v0.6 新增）** — 启动时自动发现外部 MCP server(stdio / Streamable HTTP),
  把 server 暴露的工具接进工具中心;`/mcp` slash 命令查看 server 状态 / 重连
- 📋 **Plan Mode（v0.3）** — `/plan <task>` 先读后规划,`/do` 再切全工具执行
- 🧱 **模块化 system prompt（v0.4 新增）** — 11 段拼装,稳定指令走 LLM 缓存通道,
  动态指令通过 `<system-reminder>` 注入,7 条规则可独立开关
- 🧮 **上下文管理（v0.7 新增）** — 两层 token 预算压缩：Layer 1 单 block / 单 message offload 到磁盘,
  Layer 2 LLM 6 段结构化摘要 + 熔断;`/compact` 手动触发,自动按 13K 安全余量逼近
- 🗂️ **三机制长记忆（v0.8 新增）** —
  - **项目指令文件**:三层 `BaoZiCode.md` + `@include`,优先级 user_global < project_local < project_root
  - **会话存档**:JSONL 追加写 + `/resume` / `--resume` / `--new` / `--no-banner`,
    session_id 用 `YYYYMMDD-HHMMSS-xxxx` 20 字符,v0.7 uuid 自动迁移
  - **自动笔记**:双层 `user_dir` + `project_dir`,4 类(`user-pref` / `correction` / `project` / `reference`),
    Agent 自然停下后异步调 LLM 抽取,`MEMORY.md` 索引(200 行 / 25KB)灌进 system prompt,
    溢出三级分层(NORMAL → WARN → AUTO_COMPRESS → HUMAN_NEEDED)

## 当前版本：v0.8

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
- ✅ **v0.7 上下文管理** — 两层 token 预算压缩:
  - **Layer 1(offload)**:单 block > 8 KB 或单 message 合计 > 20 KB → 写盘 + 替换为 preview(头 25 + 尾 25 行),`.baozicode/context/<session>/` 下文件默认 `.gitignore`
  - **Layer 2(摘要)**:整体 token 逼近 `context_window - 13K`(自动)或 `- 3K`(手动) → 调 LLM 生成 6 段摘要(Goal / Progress / Decisions / Files / Open Issues / Next),保留近期 ≥ 5 条或 ≥ 10K tokens 原文;3 次连续失败熔断,`StopReason.COMPACTION_FAILED` 终止 Agent
  - **.baozicode/context/** 自动加 `.gitignore`(幂等)
  - **summary prompt 显式禁止调工具**:`tools=[]` + system 提示 "Never call tools";先写 `---ANALYSIS---` 草稿(丢弃),再写 `---SUMMARY---` 正文
  - **post-compaction reminder**:摘要后追加 `<system-reminder type="post_compaction">`,提醒 LLM 摘要不可信,需要细节重新调 Read/Grep/Bash
  - **`/compact` 手动触发**:Agent 空闲直接跑;运行中通过 `agent.request_compact()` 在下个迭代顶部生效
  - **`/clear` + `on_unmount`**:清空 `.baozicode/context/<session>/` 目录
  - **`/status` 增量**:`compactions / tokens_saved / last_compact`(compaction_count > 0 时才显示)
- ✅ **v0.8 三机制长记忆**:
  - **三层项目指令** — `~/.config/baozicode/BaoZiCode.md` < `<项目>/.baozicode/BaoZiCode.md` < `<项目>/BaoZiCode.md`,
    按优先级拼接注入 `stable_system` 顶部;`@include <relpath>` 引用(深度 ≤ 5 / 环路拦截 / 路径白名单);
    三层全无 → 静默 + banner 一行建议
  - **JSONL 会话存档** — `sessions/<YYYYMMDD-HHMMSS-xxxx>.jsonl` 追加写
    (每条 message + flush + fsync);`/resume` 弹选择器续接;`--resume <id>` 直接续;`--new` 强制开新;
    resume 异常四件套:坏行跳过 / orphan tool_call 截断 / token 超限自动压 / 间隔 > 8h 插 time_gap reminder;
    30 天过期自动清掉;v0.7 uuid → 新格式启动时迁移
  - **双层自动笔记** — `user_dir` / `project_dir` 各一份 `MEMORY.md` 索引(200 行 / 25KB),
    `user-pref` / `correction` → user,`project` / `reference` → project;
    Agent 自然停下后异步调 LLM 抽取,`MEMORY.md` 索引灌进 system prompt 顶部
    `## 长期记忆 (用户级)` / `## 长期记忆 (项目级)` 段;溢出三级分层
    (NORMAL → WARN → AUTO_COMPRESS → HUMAN_NEEDED)
  - **CLI flag** — `--resume <SESSION_ID>` / `--new` / `--no-banner`
  - **Slash** — `/resume` (弹选择) / `/memory` (双层状态) / `/new` (确认后开新)
  - **`/status` 增量** — `session_id` / `sessions(磁盘): N 个` / `memory.user: X 条` / `memory.project: Y 条`

## 上下文管理 (v0.7)

两层压缩策略,轻量预防 + 重量兜底,保证对话累积再多也不会因为上下文溢出而瘫掉。

**触发时机**

- 每次 API 请求前先跑 Layer 1(管单条消息大小),再看是否需要 Layer 2(管累积历史长度)
- 自动触发:`reserve_tokens = 13K`(留 13K 安全余量防估算误差)
- 手动触发:`reserve_tokens = 3K`(`/compact` 时余量收窄,用户主动要压)

**Layer 1 — 磁盘 offload**

- 单 ToolResultBlock.content 字节数 > `per_block_threshold`(默认 8K)→ 写盘 + preview
- 单 Message(role="tool") 字节合计 > `per_message_threshold`(默认 20K)→ 挑大的依次 offload
- preview 格式:`--- preview ({bytes} bytes) ---\n<first 25 lines>\n... [N lines / M bytes omitted] ...\n<last 25 lines>\n--- offloaded to: {relpath} ---`
- 幂等:`offloaded_to is not None` 的 block 第二轮 offload 直接跳过,不会重复写盘

**Layer 2 — LLM 6 段摘要**

- 从尾部按 token / count 双阈值往回数(默认 ≥ 10K tokens 或 ≥ 5 条),head = 其余
- 摘要 prompt:`---ANALYSIS---` 草稿 + `---SUMMARY---` 正文,显式禁止工具调用,6 段固定结构
- 失败语义:stream 异常 / parse 失败 / 摘要后 token 仍超阈值 → 失败计数 +1;连续 3 次 → `CompactionError`,Agent yield `done(reason=COMPACTION_FAILED)`
- 摘要后追加 `<system-reminder type="post_compaction">` 提醒需要文件细节时重新调用工具

**配置示例**

```yaml
agent:
  context_window_tokens: 128000      # 默认 128K
  compaction:
    per_block_threshold: 8192        # 单 block offload 阈值(字节)
    per_message_threshold: 20480     # 单 message 聚合 offload 阈值(字节)
    recent_window_min_messages: 5    # 摘要后保留的近期最少消息数
    recent_window_tokens: 10000      # 摘要后保留的近期最少 tokens
    reserve_tokens_auto: 13000       # 自动触发安全余量
    reserve_tokens_manual: 3000      # /compact 触发安全余量
    max_summary_tokens: 2000         # 摘要输出上限
    max_consecutive_failures: 3      # 摘要失败熔断阈值
```

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
baozicode                          # 使用默认配置 + 弹 session 选择器(有历史时)
baozicode -c my.yaml               # 使用指定配置文件
baozicode --new                    # 强制开新 session
baozicode --resume 20260101-120000-abcd  # 续接指定 session
baozicode --no-banner              # 抑制启动 banner
python -m baozicode                # 等价
```

启动时 stderr 打印三行 banner(可用 `--no-banner` 关闭):
```
[BaoZiCode] 指令: 2 layers loaded (BaoZiCode.md + .baozicode/BaoZiCode.md)
[BaoZiCode] 记忆: 5 notes (user: 3, project: 2), index: 12 lines / 1024 bytes (state: NORMAL)
[BaoZiCode] 会话: 7 sessions found, latest: 20260101-120000-abcd (旧对话)
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
| `/status` | 显示 mode / backend / model / token 累计 + session_id + memory 摘要 |
| `/mcp` | 查看 MCP server 状态（v0.6） |
| `/mcp reconnect <name>` | 重连指定 MCP server |
| `/compact` | 手动触发上下文压缩（v0.7：Layer 1 offload + Layer 2 摘要） |
| `/resume` | 列已有 sessions,选一个续接（v0.8） |
| `/memory` | 查看 user / project 两层 memory 状态（v0.8） |
| `/new` | 归档当前 session,开新（v0.8） |

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

## MCP 集成（v0.6）

BaoZiCode 在启动时自动发现外部 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server,
把 server 暴露的工具无缝接进工具中心 — Agent 调用时完全无感,跟内置 7 个工具一样用。

### 配置

在 `config.yaml` 顶层加 `mcp_servers` 块:

```yaml
mcp_servers:
  # stdio:本地子进程走 stdin/stdout 管道
  fs:
    type: stdio
    command: python
    args: ["-m", "mcp_server_filesystem", "/path/to/root"]
    env:
      MCP_LOG_LEVEL: DEBUG
    # init_timeout_s: 5        # 单步握手超时
    # tools_list_timeout_s: 8
    # startup_total_timeout_s: 15  # 整段握手超时
    # call_timeout_s: 60

  # Streamable HTTP:远程走 HTTP POST
  remote:
    type: http
    url: https://example.com/mcp
    headers:
      Authorization: "Bearer ${REMOTE_MCP_TOKEN}"
```

两层配置合并:用户级 `~/.config/baozicode/config.yaml` 和项目级 `./config.yaml` 都可声明
`mcp_servers`,**项目级按 server 名覆盖用户级**,合并前对 `command` / `args` / `env` /
`url` / `headers` 内的 `${VAR}` 占位符做展开(走现有 `_substitute_env`)。

### 工具命名

MCP 工具统一以 `mcp__<server>__<tool>` 形式命名(避免与内置 7 工具冲突),
例如上面 `fs` server 暴露 `read_file` 工具时,Agent 看到的是 `mcp__fs__read_file`。
Agent 内部的 `_v5_executor` 对这些工具同样走五层防御权限检查:
- 路径参数(`file_path` / `dir` / `path` 等启发式扫到的)走 L2 沙箱
- Bash MCP 工具(`risk=high` / `side_effect=true` 的默认推断)走 L1 黑名单

### 失败降级

单个 server 启动失败(超时 / 命令找不到 / HTTP 404)不影响其它 server:
CLI 启动 banner 会打印 `N connected, M failed`,TUI 内可用 `/mcp` 看到完整状态。
`/mcp reconnect <name>` 可单独重跑指定 server 的握手。

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
│   ├── base.py              # ToolDefinition (side_effect + path_args) / ToolCall / ToolResult
│   ├── read.py / write.py / edit.py / bash.py
│   ├── grep.py / glob.py / webfetch.py
│   └── registry.py          # ToolRegistry 类 + 模块级兼容层(v0.6 支持运行时 MCP 注入)
├── mcp/                     # v0.6 新增 — MCP 客户端
│   ├── types.py             # JsonRpcRequest/Response/Error/Notification + McpTool/McpCallResult
│   ├── jsonrpc.py           # JsonRpcDispatcher(请求/响应 id 配对)
│   ├── transport_stdio.py   # StdioTransport(子进程管道 + stderr drain task)
│   ├── transport_http.py    # HttpTransport(Streamable HTTP + SSE + Mcp-Session-Id)
│   ├── client.py            # McpSession(initialize → initialized → tools/list)
│   ├── adapter.py           # MCP ↔ ToolDefinition / ToolResult 转换
│   └── manager.py           # McpClientManager(多 server 生命周期 + 失败降级)
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
