# BaoZiCode 🥟

> 一个用 Python 开发的命令行 AI 编码助手，类似 Claude Code。

![version](https://img.shields.io/badge/version-1.3.0-blue)

## 是什么

BaoZiCode 是一个跑在终端里的多轮 AI 对话 TUI。它支持：

- 🧠 **多轮上下文** — AI 能记住之前说过的话
- ⚡ **流式响应** — 边收边渲染（代码块自动语法高亮）
- 🔌 **四后端** — Anthropic Claude / OpenAI GPT / MiniMax / DeepSeek，YAML 一键切换
- 🎨 **Textual TUI** — 现代终端界面，输入框、流式输出、ASCII 包子 banner
- 🛠️ **斜杠命令（v0.9 起 11 个）** — `/help /compact /clear /plan /do /session
  /memory /permission /status /review /skill`（v0.9 起 删除
  `/exit /model /tools /permissions /auto /stop /mcp`,`/resume /new` 合并入 `/session`）
- 🔁 **Agent Loop（v0.3 核心）** — ReAct 自主循环,一次消息可跨多轮(默认 20 轮),
  自动判断何时停止(模型说完 / 迭代上限 / 取消 / 连续幻觉 / 拒绝累积 / 失败死循环)
- 🧰 **7 个工具** — Read / Write / Edit / Bash / Grep / Glob / WebFetch,side_effect 标记驱动并发调度
- 🔒 **权限控制** — 五层防御(v0.5):L1 黑名单 / L2 沙箱 / L3 规则引擎 / L4 模式(strict/default/permissive) / L5 人在回路
- 🔌 **MCP 集成（v0.6 新增）** — 启动时自动发现外部 MCP server(stdio / Streamable HTTP),
  把 server 暴露的工具接进工具中心;`BaoZiCodeApp._mcp_manager.states` 字典保留
  per-server 状态,banner 启动期打印 `N connected, M failed`
- 📋 **Plan Mode（v0.3）** — `/plan <task>` 先读后规划,`/do` 再切全工具执行
- 🧱 **模块化 system prompt（v0.4 新增）** — 11 段拼装,稳定指令走 LLM 缓存通道,
  动态指令通过 `<system-reminder>` 注入,7 条规则可独立开关
- 🧮 **上下文管理（v0.7 新增）** — 两层 token 预算压缩：Layer 1 单 block / 单 message offload 到磁盘,
  Layer 2 LLM 6 段结构化摘要 + 熔断;`/compact` 手动触发,自动按 13K 安全余量逼近
- 🗂️ **三机制长记忆（v0.8 新增）** —
  - **项目指令文件**:三层 `BaoZiCode.md` + `@include`,优先级 user_global < project_local < project_root
  - **会话存档**:JSONL 追加写 + `--resume <sid>` / `--new` / `--no-banner` + `/session`
    slash 弹选择器续接,session_id 用 `YYYYMMDD-HHMMSS-xxxx` 20 字符,
    v0.7 uuid 自动迁移
  - **自动笔记**:双层 `user_dir` + `project_dir`,4 类(`user-pref` / `correction` / `project` / `reference`),
    Agent 自然停下后异步调 LLM 抽取,`MEMORY.md` 索引(200 行 / 25KB)灌进 system prompt,
    溢出三级分层(NORMAL → WARN → AUTO_COMPRESS → HUMAN_NEEDED)
- 🔔 **Hooks 生命周期（v1.1 新增）** — 在 Agent 关键节点上挂「事件 + 条件 + 动作」三要素规则,
  让格式化、拦截、上下文注入从手动盯变成机器自动做。`L1 → hook.pre → L2-L5 →
  execute → hook.post` 流水线,`tool.pre` 能拦在五层防御之前,`tool.post` 必触发,
  hook 失败 fail-open 不阻断 Agent
- 🧰 **Skill 系统（v1.0 新增）** — 把可复用 AI 操作封装成 Markdown + YAML frontmatter 文件,
  启动期**两阶段加载**(只列 `name + description` 进 system prompt,保持轻);激活时
  按需加载 body,替换占位符后钉进 `<system-reminder type="active_skills">`。
  3 级存放 project > user > builtin;两种执行模式 `shared`(主对话)/ `independent`
  (子对话);L1 启动期校验 `allowed-tools` 全部存在 + L2 运行期收窄到 union 命中放行;
  3 个内置 Skill(`commit` / `review` / `test`),`load_skill` 是 system 工具永远放行
- 🤖 **SubAgent 委派（v1.2 新增）** — 主 Agent 通过统一 `task` 工具派子任务给独立
  sub-Agent。两条派发路径:`definition`(干净上下文 + 角色身份,4 层 AND 工具过滤,
  `GLOBAL_DENY={task}` 硬禁嵌套)/ `fork`(共享主对话历史,prompt cache byte-identical
  命中,省钱)。默认 `async: true` 后台跑,sync 可设 `timeout_seconds` 超时自动切后台;
  3 种进入后台方式(显式 / 超时 / 手动 demote);主 Agent cancel 级联 cancel_all。
  2 个内置样板:`explorer`(Read/Grep/Glob/WebFetch)/ `summarizer`(Read/Grep/Glob + haiku)
  。**Breaking**:v1.0 旧 `SkillExecutor.independent_runner` 注入路径删除,独立模式
  重走 SubAgent 通道
- 🌲 **Worktree 隔离（v1.3 新增，可选）** — sub-Agent role frontmatter 加
  `isolation: worktree`,让该 sub-Agent 在独立 git worktree 里跑,与主 Agent /
  其它 sub-Agent 的文件改动互不打扰。用 git 原生多工作树(共享版本库 + 各自分支),
  目录固定 `.worktrees/<name>/`(自动 `.gitignore`);创建含 fast-path 恢复、
  Initializer 4 步初始化(链依赖 / 复制配置 / 配 hooks / 补 gitignore);Bash 工具
  显式注入 `cwd` 参数(不 chdir);退出变更保护(未提交 / 未推送默认拒删),
  CleanupDaemon 三层过滤后台清理。默认关闭,零行为变化

## 当前版本：v1.3

- ✅ **Worktree 隔离(v1.3 新增 — 主特性)** — `baozicode/worktree/`
  - sub-Agent role frontmatter 加 `isolation: worktree` → 该 sub-Agent 在独立
    git worktree(`.worktrees/<name>/`)里跑;不写 / `null` → 走 v1.2 老路径
    (共享主 project_root)。默认关闭,需 `subagents.worktree.enabled: true`
    + project_root 是 git repo
  - **目录名安全校验**:限字符集 + 长度、拒 `.` / `..` 段、允许斜杠做嵌套
    (`phase1/api-designer` → `.worktrees/phase1/api-designer/`),防 LLM 输入触发
    路径遍历
  - **完整生命周期**:创建(含 fast-path 恢复 —— 目录已存在只读文件系统不调 git)/
    进入 / 退出 / 删除;`WorktreeManager` 编排,`git worktree add -b wt/<name>`
  - **环境初始化**(Initializer 4 步):软链大依赖(`.venv` / `node_modules`)、
    复制本地配置(`.env` / `BaoZiCode.md`)、配子目录 git hooks、追加 `.worktrees/`
    到 `.gitignore`
  - **显式 cwd 而非 chdir**:Bash 工具加 `cwd` optional 参数,SubAgentManager 自动
    注入(LLM 不感知);所有路径相关缓存用绝对路径 key,天然按目录隔离,不需切换清缓存
  - **退出变更保护**:exit 决策树 —— 全干净 → 删 / 有未提交或未推送 commit → 留
    detached(TUI 卡片显 `worktree: detached`);`canceled` → force 删
  - **后台清理**:`CleanupDaemon`(默认 60s 一次)三层过滤(task 活跃 → 时间 →
    干净度)扫过期 worktree 强清,任意一层不过就 skip
  - **cache 取舍**:主 Agent prompt byte-identical → Anthropic cache 命中零变化;
    worktree sub-Agent 因 `cwd` 段不同 → 首次 LLM 请求 cache miss(不引入第二份缓存)
  - **Bash `cwd` 向后兼容**:不传 `cwd` → 走 v1.2 老路径;传 `cwd=<abs>` →
    fire-and-forget(执行完不更 session.cwd);非法 cwd(相对 / 不存在 / 非目录 /
    有效 root 外)→ reject 不执行
  - 详细迁移 + 配置块 + frontmatter 示例 + exit 决策表 + 故障排查见
    `docs/migrations/v1.2-to-v1.3.md`

- ✅ **Hooks 生命周期(v1.1 新增,v1.2 polish)** — `baozicode/hooks/`
  - 在 Agent 关键节点(session / turn / message / tool / system 共 11 个
    事件)挂「事件 + 条件 + 动作」三要素规则,让重复自动化
  - **条件语法**:精确 / 反向 / 正则 / glob,逻辑组合 `all` / `any`(二选一)
    复用权限规则匹配;`if` 省略即无条件
  - **6 种 action**:`shell`(`exit_code` 判 deny)、`prompt`(3 档 slot:
    sticky_reminder / stable_system / temp)、`http`(simpleeval
    `parse_expr` 决定 deny)、`sub-agent`(占位,v1.1.1 接通)、
    `clear_sticky_reminders` / `clear_stable_system_overrides`(v1.2 control action,
    各自只清一类 hook 注入状态)
  - **v1.2 polish**:`system.compaction` / `system.cancel` 在各自时机可靠 fire
    (手动 / 自动压缩路径都 fire,取消时 fire 改 dict payload 含 `iteration`),
    TUI `ToolResultCard` 按 `execution_status` 5 色渲染(L1 红 / hook_pre 黄 /
    L2-L5 橙 / success 绿 / failed 红)
  - **流水线插桩**:`L1 → hook.pre → L2-L5 → execute → hook.post`,
    L1 是 hard-wall(hook.pre 改不掉),`tool.post` 用 `try/finally` 包整个
    pipeline,任何 tool_call 尝试必触发(完整审计)
  - **执行控制**:`run_once`(全 session 只跑一次)/ `async`(只对 `tool.post`
    允许)/ `timeout_seconds`(默认 30)
  - **fail-open**:hook 抛任何异常只 `log.warning`,Agent 主流程不阻断
    (与权限系统「fail-closed」对称)
  - **ToolResult 新字段**:`execution_status` / `denied_by` / `denied_hook_id`,
    `is_error` 由 `__post_init__` 派生;旧调用方式完全兼容
  - **配置 + 校验**:`config.yaml` 顶层 `hooks:` 块(YAML 列表,声明顺序),
    启动期 `HookRegistry.freeze()` 收集所有错误一次性报
    (duplicate id / async+tool.pre / stable_system on tool.* 等)
  - **审计**:`HookAuditLog` 异步 JSONL append + 100 MB 启动期轮转,
    默认 `<project>/.baozicode/hooks/<session>.audit.jsonl`
  - 没有 `hooks:` 块的 v1.0 项目,Agent 走 legacy 路径(v1.0 byte-identical)
  - 详细迁移 + 字段表 + 11 个事件清单 + 6 种 action 详解 + 10 个 FAQ 见
    `docs/migrations/v1.0-to-v1.1.md`,v1.2 polish 见 `docs/migrations/v1.1-to-v1.2.md`

- ✅ **SubAgent 委派(v1.2 新增 — 主特性)** — `baozicode/agents/`
  - 主 Agent 通过统一的 `task` 工具,把子任务派给独立 sub-Agent。每个
    sub-Agent 跑独立 `ConversationManager` + 独立权限追踪 + 受限工具集
  - **definition 模式**:派独立角色(基于 `AGENT.md` frontmatter 定义的角色)
    跑任务,默认 `async: true` 后台跑,跑完结果摘要回流
  - **fork 模式**:复用主 Agent 历史(prompt cache byte-identical 命中,省钱),
    强制后台运行
  - **4 层 AND 工具过滤**:`GLOBAL_DENY={task}` 硬禁嵌套 → `role.tools`
    角色白名单 → `role.tools-deny` 角色黑名单 → 后台模式
    `background_whitelist`(默认 Read/Grep/Glob/WebFetch/notify_complete)
  - **加载优先级(同名覆盖)**:项目 `.baozicode/agents/` > 用户
    `~/.config/baozicode/agents/` > 内置 `<pkg>/baozicode/agents/builtin/`(2 个
    样板 `explorer` / `summarizer`)> MCP plugin(server 暴露 `agents://list`)
  - **Skill 独立模式打通**:v1.0 SkillExecutor 独立模式重写走 SubAgent 通道
    (旧 `IndependentRunner` 注入路径删除,见迁移指南)
  - **TUI 集成**:状态栏 `[agents: R/D/F]` 实时统计;`SubAgentCard` 折叠卡片
    点击展开看 sub-Agent streaming text;完成时 `App.notify` 弹 toast
  - **2 个内置样板 agent**:只读 `explorer`(Read/Grep/Glob/WebFetch) +
    `summarizer`(Read/Grep/Glob + haiku,适合长篇压缩)
  - 详细迁移 + 字段表 + 4 dispatch 组合 + 5 FAQ 见
    `docs/migrations/v1.1-to-v1.2.md`

- ✅ **Skill 系统(v1.0 新增)** — `baozicode/skills/`
  - 把可复用 AI 操作封装成独立 Markdown 文件 + YAML frontmatter,3 个内置
    (`commit` / `review` / `test`),三级存放 project > user > builtin
  - **两阶段加载**:启动时 system prompt 只列名字 + 一句话说明,要用时
    LLM 调 `load_skill` 工具按需加载完整 SOP(body 替换占位符后钉进
    `<system-reminder type="active_skills">` 顶部,每轮重建都在)
  - **两种执行模式**:`shared`(共享当前对话,结果留主历史)/ `independent`
    (开子对话跑完摘要回流,可带 `history-bubbles` 条历史)
  - **工具白名单双层防御**:`allowed-tools` 启动期 L1 校验存在性(不存在即 panic)
    + 运行时 L2 收窄到 union(命中放行,未命中拒);`load_skill` 是 system 工具,
    永远放行
  - **`/skill` slash 命令**:`/skill list` / `/skill <name> [args]` / `/skill clear`,
    加载后自动注册同名短命令;`/clear` 同步清空已激活;改 SKILL.md 后
    `registry.reload(name)` 立即生效
  - **SkillsConfig** 整块可省略,`enabled: false` 退回 v0.9 行为;v0.4 老
    `skills_dir` 单文件路径作为 fallback 保留(无破坏性)
  - 详细迁移 + 完整示例见 `docs/migrations/v0.9-to-v1.0.md`

- ✅ **Slash 命令注册中心**(`baozicode/commands/`)
  - 11 个内置命令:`/help /compact /clear /plan /do /session /memory /permission /status /review /skill`
  - 元数据集中:`CommandDef(name/aliases/description/usage/type/handler...)`
  - 启动时 `freeze()` 别名冲突 → SystemExit(boot panic,不延迟到运行)
  - 大小写不敏感 + 实时 Tab 补全
  - 3 类执行模式(LOCAL / UI_STATE / PROMPT) + `narrow CommandContext` 接口
  - 状态栏 mode marker:`[DEFAULT] / [PLAN] / [STRICT] / [PERMISSIVE]`

- ✅ 7 个工具 + `side_effect` 标记(`Plan B` 并发调度 + `Plan C` 扩展点)
- ✅ **Agent Loop** — 7 种 `AgentEvent`(text / tool_call / tool_result / usage / progress / done / error)
- ✅ **StreamCollector** — 双路收集:TUI 实时收 text,Agent 决策看 TurnSnapshot 完整源
- ✅ **三层 stop guards** — unknown_tool / deny_threshold / failed_loop
- ✅ **Plan Mode** — `/plan` 只开放 4 个只读工具,`/do` 切回全工具(严格动词)
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

## 斜杠命令(v0.9 重写 — `baozicode/commands/` 注册中心)

| 命令 | 类型 | 说明 |
|------|------|------|
| `/help` | LOCAL | 列出 11 个内置命令,描述 + usage |
| `/clear` | UI_STATE | 清空对话历史 + session 用量 + 已激活 Skill + hook 注入状态(v1.1.1 起同时清 sticky hook_prompt reminder / `## Hook Overrides` 段 / temp reminder,见 `clear_hook_runtime_state`) |
| `/compact` | UI_STATE | 手动触发上下文压缩(v0.7:Layer 1 offload + Layer 2 摘要) |
| `/plan` | UI_STATE | **严格动词**:切 plan_mode=True(args 静默忽略) |
| `/do` | UI_STATE | **严格动词**:切 plan_mode=False(args 静默忽略) |
| `/session` | UI_STATE | 弹 StartupSessionScreen 选:恢复某 sid / 开新 / 取消 |
| `/memory` | LOCAL | 查看 user / project 两层 memory 状态 |
| `/permission [mode]` | UI_STATE | 显示当前或切换 strict / default / permissive |
| `/status` | LOCAL | mode + backend + token 累计 + session_id + memory 摘要 |
| `/review [<since>]` | PROMPT | 让 Agent 审查自 `{since}` 起的改动,默认 `"本次会话开始"` |
| `/skill list\|<name> [args...]\|clear` | UI_STATE | 列出 / 加载并激活 / 清空已激活 Skill(v1.0) |

**别名**:`/permissions` = `/permission`(兼容 v0.5-v0.6 拼写)。

**v0.9 删除命令迁移**:`/exit` → `Ctrl+C` /
`/model` → 改 config 重启 / `/tools` → 合并入 `/status` /
`/mcp` → 启动 banner 看状态 / `/stop` → `Ctrl+C` /
`/auto` → `/permission mode permissive` /
`/resume` `/new` → 合入 `/session`。

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

**系统级（始终放行，不受白名单/Plan Mode 约束）**：
- `load_skill` — v1.0 Skill 加载器，LLM 调它把 Skill body 钉进当前会话的 active_skills reminder
- `task` — v1.2 sub-Agent 派发器（`type="definition"|"fork"`+`role`+`prompt`,
  `async` 默认 true；definition 派独立角色跑完摘要回流,fork 共享主对话
  历史让首次 LLM 请求命中 prompt cache）

## Skill 系统（v1.0）

把可复用的 AI 操作封装成独立 Markdown 文件，模型按需加载而不是把 SOP 全部塞进 system prompt。完整迁移 + 字段表见 `docs/migrations/v0.9-to-v1.0.md`，这里给一份"看完能用"的浓缩版。

**目录布局**

```
~/.config/baozicode/skills/<name>/SKILL.md    # 用户级（覆盖 builtin）
<project>/.baozicode/skills/<name>/SKILL.md   # 项目级（最高优先级）
<内置 commit / review / test 三件套>
```

三级同名 Skill 合并规则：**project > user > builtin**。整目录可作为"能力包"分发，除 `SKILL.md` 外可附带模板/示例/脚本。

**SKILL.md 格式**

```markdown
---
name: commit                              # 必填,小写字母+数字+连字符
description: 根据 git diff 生成 commit    # 必填,启动期塞 system prompt
mode: shared                               # shared(主对话) / independent(子对话)
allowed-tools: [Bash, Read]                # 启动期 L1 校验存在性
history-bubbles: 3                         # 独立模式带几条历史进子对话
---
根据 `git diff --staged` 生成 conventional commit message 并执行 commit。

## SOP
1. 调 `Bash` 跑 `git diff --staged --stat`
2. 调 `Bash` 跑 `git diff --staged`
3. 归纳改动 → 生成 commit message
4. 调 `Bash` 跑 `git commit -m "<subject>"`

## 占位符
- `{area}` — 改动范围(必带)
- `{since:HEAD~10}` — 默认值可不传
```

正文里 `{var}` / `{var:default}` 两种占位符在 `load_skill(name, args={...})` 时替换。

**两阶段加载**

```
启动:  system prompt 注入 `## 可用 Skill(两阶段加载)`
        列所有可见 Skill 的 name + description + 来源
        (Body 不进 system prompt,保持 LLM 看到的内容轻)

激活:  LLM 调 load_skill(name, args={...})
        body 替换占位符 → 钉入 SkillActivation
        同步注册同名 slash 短命令
        返回 body 给 LLM(让模型看到完整 SOP)

每轮:  Agent 重建 prompt,在 env reminder 后追加
       <system-reminder type="active_skills">
         ## /skill commit
         <body 渲染结果>
       </system-reminder>
       多个激活 Skill 按 load 顺序拼接
```

**两种执行模式**

| 模式 | 行为 | 适用 |
|------|------|------|
| `shared` | body 注入主对话,工具在主对话里调 | 短任务、与上下文耦合 |
| `independent` | SkillLoader 调独立 runner(子 Agent),完成后摘要回流 | 长任务、需隔离上下文(审查/测试) |

**工具白名单双层防御**

- **L1 启动期**:`allowed-tools` 里写了不存在的工具 → `SystemExit`(boot panic)
- **L2 运行期**:多个激活 Skill 的白名单取 **union**,命中放行,未命中拒
- **`load_skill` 是 system 工具**(`tool_type="internal"`),**永远**放行,
  即便 Skill 收窄到 `[Read]` 也能调

**Slash 命令**(`/skill ...`)

| 形态 | 行为 |
|------|------|
| `/skill list` | 列可见 Skill(name + description + 来源) |
| `/skill <name> [args...]` | 加载并激活;args 形如 `key=value`,空格引号包裹 `key="value with spaces"` |
| `/skill clear` | 清空所有已激活 |

**配置**

```yaml
skills:
  enabled: true                           # 整系统开关(默认 true)
  builtin_dir: <pkg>/skills/builtin        # 默认,通常不动
  user_dir: ~/.config/baozicode/skills     # 默认
  project_dir: ./.baozicode/skills         # 默认
```

整块可省略;`enabled: false` → 整套空集 + 不注册 `load_skill` 工具,退回 v0.9。

## SubAgent 委派（v1.2）

主 Agent 通过 `task` 工具把子任务派给隔离的 **sub-Agent** —— 每个 sub-Agent 跑独立
对话上下文、用受限工具集、有独立权限追踪。子 Agent 跑完结果异步回流主 Agent,
主对话历史不再被 sub-tool 调用污染。

### 两条派发路径

- **definition 模式** —— 派独立角色跑 AgentDef.frontmatter
  ```json
  {"type": "definition", "role": "explorer",
   "prompt": "扫 src/ 列出所有 .py 文件", "async": true}
  ```
- **fork 模式** —— 共享主 Agent 历史(prompt cache 命中,省钱)
  ```json
  {"type": "fork", "prompt": "基于上面结果筛匹配 pattern X 的文件"}
  ```

`async: true`(默认)→ 派完即返回 task_id 后台跑;`async: false` 阻塞等结果
(可设 `timeout_seconds` 超时自动切后台);**fork 强制后台**(sync 路径打 warning
后强制 async 走后台)。

### 三种进入后台的方式

| 方式 | 触发 | 备注 |
|------|------|------|
| **显式 `async: true`** | LLM 调 `task` 工具时直接传 | 默认行为,派完返回 task_id |
| **sync 超时** | `async: false` + `timeout_seconds`,子 Agent 跑超时 | 自动切后台,本轮不阻塞 |
| **手动切** | `request_subagent_async(task_id)` 手动 demote running sync task | 调试 / 想取消时用 |

### 角色定义

```markdown
<!-- ~/.config/baozicode/agents/explorer/AGENT.md -->
---
name: explorer
description: 只读探索仓库
tools: [Read, Grep, Glob, WebFetch]
model: sonnet
max-iterations: 8
permission-mode: permissive
---
你是只读探索 agent。禁止任何写操作,禁止 sub-Agent 嵌套(task 工具被硬禁)。
```

**加载优先级**(同名覆盖):项目 `.baozicode/agents/` > 用户
`~/.config/baozicode/agents/` > 内置 `<pkg>/baozicode/agents/builtin/`
(`explorer` / `summarizer` 两个样板)> MCP plugin(server 暴露 `agents://list`)。

### 工具过滤 4 层 AND

```
GLOBAL_DENY = {task}     # L1 ── 硬禁嵌套(子 Agent 不能调 task 工具)
role.tools: [...]        # L2 ── 角色白名单(None = 全允许)
role.tools_deny: [...]   # L3 ── 角色黑名单
background_whitelist     # L4 ── 后台模式额外白名单(默认 Read / Grep / Glob /
                         #       WebFetch / notify_complete)
```

任一层过滤后为空 → `ToolFilterEmptyError`,dispatch 时被捕获转 `ToolResult(is_error=True)`。

### 状态回流

| 主 Agent 状态 | 子 Agent 完成 | 回流方式 |
|--------------|--------------|---------|
| idle | 子 Agent done | 直接 `add_user("[role 子对话结果]\n...")` 进主对话,主 Agent 下条 user 消息触发 run |
| running | 子 Agent done | `enqueue_reminder("subagent_result")` 在下轮顶部消费 |

任一终态 → TUI 弹 `App.notify` toast + `SubAgentCard` 折叠卡片(点开展开
sub-Agent streaming text,终态颜色:done/canceled 绿边,failed 红边)。

### Cascade cancel

主 Agent `cancel()` 检测自己有 `_subagent_manager` → 调 `manager.cancel_all()`
→ 所有 running 的 sub-Agent 5s 内通过 `cancel_event` 信号停掉。子 Agent 取消后
走 `state=canceled` 终态回流(同 done 路径)。

### MCP Plugin Agent(可选)

如果 MCP server 实现 `agents://list` + `agents://<name>` 资源协议,启动时会自动
拉 AgentDef 注册到 SubAgentManager(同名按项目 > 用户 > 内置 > plugin 优先级)。

```python
# server 端契约示例
async def read_resource(uri: str) -> dict:
    if uri == "agents://list":
        return {"contents": [{"text": json.dumps([
            {"name": "browser", "description": "浏览器自动化", "version": "1"},
        ])}]}
    if uri == "agents://browser":
        return {"contents": [{"text": "---\nname: browser\n...\n---\n... body ..."}]}
    raise ValueError("unknown uri")
```

降级:server 端异常 → 跳过该 server;agent 详情拉取失败 → 跳过该 agent。

### Worktree 隔离(v1.3,可选)

sub-Agent role 的 frontmatter 加 `isolation: worktree`,即可让该 sub-Agent 在
**独立的 git worktree** 里跑 —— 与主 Agent、其它 sub-Agent 的文件改动互不打扰。
默认不启用(需 `config.yaml` 配 `subagents.worktree.enabled: true` + project_root 是 git repo)。

```yaml
---
name: phase1/api-designer      # 嵌套命名 → .worktrees/phase1/api-designer/
description: 设计阶段 1 的后端 API 契约
isolation: worktree
allowed-tools: [Read, Grep, Glob, Write, Edit]
---
你负责 phase1 的 API 契约设计...
```

隔离行为:

- 派发时 `WorktreeManager` 自动 `git worktree add -b wt/<name>`(目录已存在则走
  fast-path,只读文件系统不调 git)
- Initializer 跑 4 步:软链大依赖(`.venv` / `node_modules`)、复制本地配置
  (`.env` / `BaoZiCode.md`)、配子目录 git hooks、追加 `.worktrees/` 到 `.gitignore`
- Bash 工具由 SubAgentManager 自动注入 `cwd=<worktree_path>`(LLM 不感知)
- 完事按 exit 决策树:全干净 → 删 / 有未提交或未推送 → 留 detached(TUI 卡片显
  `worktree: detached`)
- `CleanupDaemon` 后台三层过滤(task 活跃 → 时间 → 干净度)扫过期 worktree 强清

cache 取舍:主 Agent 的 prompt byte-identical → Anthropic cache 命中零变化;
worktree sub-Agent 因 `cwd` 段不同 → 首次 LLM 请求 cache miss(不引入第二份缓存)。

---

详细:[docs/migrations/v1.2-to-v1.3.md](./docs/migrations/v1.2-to-v1.3.md)

---

详细:[docs/migrations/v1.1-to-v1.2.md](./docs/migrations/v1.1-to-v1.2.md)

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
│                            #   v1.0:skills section 双路径(v1.0 registry / v0.4 fallback)
├── skills/                 # v1.0 新增 — Skill 系统
│   ├── schema.py           # SkillDef / SkillFrontmatter / SkillSource
│   ├── registry.py         # SkillRegistry(三级 scan + priority merge + reload)
│   ├── activation.py       # SkillActivation(render_active_section + clear)
│   ├── loader.py           # SkillLoader(load_skill + 占位符替换 + L1 校验)
│   ├── execution.py        # SkillExecutor(shared / independent 走 SubAgent 通道 — v1.2)
│   ├── whitelist.py        # SkillWhitelistFilter(L2 union + system 工具豁免)
│   ├── bootstrap.py        # bootstrap_skills() + SkillSet 聚合
│   ├── load_skill_tool.py  # load_skill tool 定义 + 执行器注册
│   └── builtin/            # 3 个内置 Skill(目录形,可附带模板/脚本)
│       ├── commit/SKILL.md # shared, allowed-tools: [Bash, Read]
│       ├── review/SKILL.md # independent, history-bubbles: 3
│       └── test/SKILL.md   # independent, history-bubbles: 2
├── agents/                 # v1.2 新增 — SubAgent Delegation
│   ├── schema.py           # AgentFrontmatter(Pydantic, v1.3 +isolation)/ AgentDef / parse_agent
│   ├── registry.py         # AgentRegistry(3 级 scan + 优先级合并 + plugin 合并)
│   ├── loader.py           # substitute_placeholders({var} / {var:default})
│   ├── filter.py           # ToolFilter(4 层 AND + GLOBAL_DENY={task} + cache)
│   ├── runtime.py          # SubAgentRuntime(spawn — 状态隔离 + BuiltPrompt 分流,v1.3 +isolation 分支)
│   ├── manager.py          # SubAgentManager(dispatch 派发 + 状态机 + cascade cancel
│   │                       #   + v1.3 worktree 退出决策 + _run_subagent finally 块)
│   ├── plugin.py           # fetch_plugin_agents(MCP resources/read 协议)
│   └── builtin/            # 2 个样板 sub-Agent
│       ├── explorer/AGENT.md  # definition + tools=[Read,Grep,Glob,WebFetch]
│       └── summarizer/AGENT.md  # definition + model=haiku
├── worktree/               # v1.3 新增 — Git Worktree 隔离层
│   ├── schema.py           # WorktreeSpec / WorktreeState / 错误枚举 + WorktreePathValidator
│   ├── manager.py          # WorktreeManager(create / enter / exit / remove / list_active + fast-path)
│   ├── initializer.py      # WorktreeInitializer 4 步(link / copy / hooks / gitignore)
│   └── cleanup.py          # WorktreeCleanupDaemon(三层过滤后台清理)
├── tui/
│   ├── chat_screen.py      # 主对话屏幕(订阅 Agent 事件流 + 状态栏 [agents: ...] + 0.5s 轮询 sub-Agent)
│   ├── tool_card.py        # ToolCallCard / ToolResultCard
│   ├── subagent_card.py    # v1.2 — SubAgentCard 折叠卡片(点击展开 last_text)
│   ├── permission_modal.py # 高风险工具确认弹窗
│   ├── banner.py           # ASCII 包子
│   └── styles.tcss         # Textual 样式(含 StatusBar + SubAgentCard)
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
WorktreeManager(v1.3,optional)                 # isolation: worktree 角色走这里
  ├─ create(name) → git worktree add + Initializer 4 步
  ├─ exit(name, force?) → 决策树(干净删 / dirty 留 detached)
  └─ CleanupDaemon 后台三层过滤(task 活跃 → 时间 → 干净度)
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
