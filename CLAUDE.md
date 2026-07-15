项目名：BaoZiCode
本地语言：中文

## 各版本范围

### v0.5 范围
五层防御权限系统。L1 硬拦截危险命令、L2 路径沙箱、L3 三层 YAML
规则合并、L4 三档 mode、L5 人在回路。deny 不终止 Agent Loop,改为向 LLM 注入
`<system-reminder type="denial_rate_limit">` 提醒调整策略。

`baozicode/permissions/check(call, ctx) -> PermissionDecision` 是 5 层流水线
总入口,由 `Agent._v5_executor` 在每个 tool_call 之前调用;BaoZiCodeApp 在启动时
调 `permissions.bootstrap(project_root, config)` 加载三层 YAML 并构造
`MergedPermissions` + `RuleEngine`。

### v0.6 范围
v0.5 之上加一层：MCP (Model Context Protocol) 客户端,启动时自动发现外部 MCP server,
把 server 暴露的工具接进工具中心。两种传输：stdio（子进程管道）和 Streamable HTTP；
三步握手：initialize / notifications/initialized / tools/list；tools/call 在 Agent
调用时跑。失败降级（per-server try/except,banner 警告）；断开时 mark broken +
is_error 返回。

`baozicode/mcp/manager.McpClientManager` 是编排器,持有 `states: dict[name, ServerState]`
（status / error / tools / session）。Agent 通过 `_default.get_all_tools()` 自动看到
MCP 工具——因为 manager 在 bootstrap 时把每个 MCP 工具都注册到 `ToolRegistry`,
五层防御权限（`_v5_executor`）对 `mcp__<server>__<tool>` 工具同样生效:
- 路径参数（`file_path` / `dir` / `path` 启发式扫到的）走 L2 沙箱
- 默认保守值 `risk=high` / `side_effect=true` / `path_args=[]`（无 schema 时）走 L1 黑名单

`tools/registry.py` v0.6 改为 `ToolRegistry` 类（内置 7 工具 + 运行时 MCP 注入）,
模块级 `_default = ToolRegistry()` 单例 + 顶层函数兼容层,12 个调用点零修改。

### v0.7 范围
v0.6 之上加一层：上下文压缩。两层 token 预算管理（不破坏 v0.6 行为）：
- **Layer 1（offload）**：单 block > 8 KB 或单 message > 20 KB → 写盘 + 替换为 preview
  （头 25 + 尾 25 行）;`.baozicode/context/<session>/` 自动 `.gitignore`
- **Layer 2（摘要）**：逼近 `context_window - 13K`(自动)/ `- 3K`(手动) → 调 LLM
  生成 6 段摘要(Goal / Progress / Decisions / Files / Open Issues / Next);保留近期 ≥ 5 条
  或 ≥ 10K tokens 原文;3 次连续失败熔断,`StopReason.COMPACTION_FAILED` 终止 Agent
- summary prompt 显式禁止调工具(`tools=[]` + system 提示 "Never call tools");
  先写 `---ANALYSIS---` 草稿(丢弃)再写 `---SUMMARY---` 正文
- post-compaction 追加 `<system-reminder type="post_compaction">` 提醒 LLM 摘要不可信
- `/compact` 手动触发(Agent 空闲直接跑 / 运行中 `agent.request_compact()` 在下个迭代顶部生效);
  `/clear` + `on_unmount` 清空 `.baozicode/context/<session>/`

`baozicode/context/` 7 个子模块:`schema`(ContextConfig/CompactionTelemetry) /
`boundary`(块/消息边界检测) / `estimator`(token 估算) / `layer1`(offload) /
`layer2`(摘要) / `orchestrator`(`maybe_compact`) / `storage`(目录 + `.gitignore`)。

### v0.8 范围
v0.7 之上加三层:项目指令 / 长期记忆 / 会话存档。

**项目指令**(`baozicode/instructions/`)
- 三层 `<name>BaoZiCode.md`:`~/.config/baozicode/BaoZiCode.md`(user_global, 最低) <
  `<项目>/.baozicode/BaoZiCode.md`(project_local) < `<项目>/BaoZiCode.md`(project_root, 最高);
  按优先级拼接注入 `stable_system` 顶部(高优先级在前让模型优先遵循)
- `@include <relpath>` 引用:深度 ≤ 5 / 环路拦截(`visited` 集合)/ 项目目录白名单
- 三层全无 → 静默 + banner 一行建议
- `instructions.bootstrap(project_root, config)` → `LoadedInstructions{layers, concatenated}`

**长期记忆**(`baozicode/memory/`)
- 双层物理隔离:`user_dir`(跟人走)/ `project_dir`(跟项目走)
- 4 类笔记 + 自动路由:`user-pref / correction → user`;`project / reference → project`
- 索引 200 行 / 25KB 上限,灌进 system prompt 顶部
- Agent 自然停下后**异步**调 LLM 抽取笔记(快照式并发,去重交给 LLM 判断)
- 溢出状态机:`NORMAL → WARN → AUTO_COMPRESS(回 NORMAL) → HUMAN_NEEDED`
- 跨 session 删除别人写的笔记被拒(防互踩);`update` 允许跨 session 追加 / 合并
- 废弃 `AppConfig.memory_path`(v0.9 移除);兼容:文件存在 + 新双层目录都空 → banner WARN + 当 user 级索引读

**会话存档**(`baozicode/sessions/`)
- JSONL 追加写:`sessions/<YYYYMMDD-HHMMSS-xxxx>.jsonl`(中间 flush + fsync)
- `session_id` 格式从 v0.7 `uuid4` 32 字符 → v0.8 `YYYYMMDD-HHMMSS-xxxx` 20 字符
  (4 字符随机 hex 防同秒撞车);升级时自动迁移旧 uuid 目录,撞名挂 `_legacy` 后缀
- 续接四件套:坏行跳过 / orphan tool_call 截断 / token 超限自动压 / `time_gap` reminder(默认 8h)
- 30 天过期 session 启动时自动清理
- CLI flag:`--resume <SESSION_ID>` / `--new` / `--no-banner`;无 flag + 有历史 → 弹 `StartupSessionScreen`
- 启动顺序调整:`permissions → instructions → memory → sessions`

### v0.9 范围
v0.8 之上加一层：Slash 命令注册中心(`baozicode/commands/`)。
- 元数据集中:`CommandDef{name, aliases, description, usage, type, params_hint, hidden, handler}`
- `CommandRegistry.freeze()` 启动时一次 alias 冲突检测 → SystemExit(boot panic)
- 大小写不敏感 + Tab 实时补全(`TabCompleter.candidates()`)
- 3 类执行模式:`LOCAL` / `UI_STATE` / `PROMPT`;handler 返回 `LocalResult / UiStateResult /
  PromptResult(text)`,dispatcher 按 type 分支
- 10 个内置命令:`/help /compact /clear /plan /do /session /memory /permission /status /review`
- Narrow `CommandContext` Protocol:7 个方法 + 2 个属性(`app` / `config`),handler 不 import textual
  不 import 业务模块,可用 Stub 单元测试,可被 CLI / HTTP 前端复用
- 状态栏 mode marker:`[DEFAULT] / [PLAN] / [STRICT] / [PERMISSIVE]`
- `/plan` / `/do` 严格动词:任何 args 静默忽略,只切 `plan_mode`
- v0.9 起删除 6 个老命令(`/exit /model /tools /mcp /stop /auto`),`/resume /new` 合并入 `/session`

### v1.0 范围
v0.9 之上加一层：Skill 系统(`baozicode/skills/`)。把可复用 AI 操作封装成
独立 Markdown 文件 + YAML frontmatter。**无 breaking change**——v0.9 配置 / 旧命令 /
v0.4 旧 `skills_dir/*.md` 单文件路径全部继续工作。
- 3 级存放:project > user > builtin(内嵌在包内,可被项目级覆盖)
- **两阶段加载**:boot 注入 `name + description` 到 `## 可用 Skill(两阶段加载)` 段;
  激活时把 body 替换占位符后钉到 `<system-reminder type="active_skills">` 顶部
- **2 种执行模式**:`shared`(共享当前对话,结果留主历史)/ `independent`(开子
  ConversationManager + 新 Agent,跑完生成摘要回流;`history-bubbles=N` 把主对话
  最近 N 条消息一起送进子对话,默认 0;`MAX_HISTORY_BUBBLES=50`)
- **双层工具白名单**:
  - L1 启动期校验 `allowed-tools` 全部存在(否则 SystemExit,banner 阶段就拦住)
  - L2 运行时收窄到 union(命中放行,未命中拒;`Agent.augmented_tools` + v0.5
    `_v5_executor` 两层防御,LLM 强调也被拦)
  - `load_skill` 标 `tool_type="internal"`,永远放行,不受白名单约束
- **占位符替换**:`{var}` 必带,`{var:default}` 可选
- **双入口触发**:用户 `/skill <name> [args]` slash + LLM `load_skill(name, args)` tool 调
- **显式热更新**:`registry.reload(name)` 立即重读;不引入 watchdog
- **自动注册短命令**:`/skill <name>` 加载后自动注册同名 slash 短命令
- **3 个内置 Skill**(`baozicode/skills/builtin/`):
  - `commit` — `mode: shared`, `allowed-tools: [Bash, Read]`
  - `review` — `mode: independent`, `allowed-tools: [Bash, Read, Grep]`, `history-bubbles: 3`
  - `test` — `mode: independent`, `allowed-tools: [Bash]`, `history-bubbles: 2`
- **第 12 个内置命令** `/skill`(在 v0.9 10 个之上):`/skill list` / `/skill <name> [args]` / `/skill clear`
- **`SkillsConfig`** 配置块可选(整块省略 → 走 bootstrap 默认);`enabled: false` → 整套系统空集,
  prompt section 也不出现「可用 Skill」段
- **目录形 Skill**(推荐):`<skills_dir>/<skill-name>/SKILL.md`,整目录可作为能力包
  分发(SKILL.md 入口 + 模板/示例/脚本);`name` 必须 `^[a-z][a-z0-9-]*$`

### v1.3 范围
v1.2 SubAgent 之上加一层:**git worktree 隔离**。带 `isolation: worktree` 的
sub-Agent 在独立工作目录里跑,主 Agent 与其它 sub-Agent 不被它的文件改动打扰。
**默认关闭**(`subagents.worktree.enabled: false`)→ 零行为变化,走 v1.2 老路径。

- **git 原生多工作树**:同仓库挂多目录、共享版本库、各自分支 `wt/<name>`;目录固定
  `<project>/.worktrees/<name>/`,自动加到 `.gitignore`;`project_root` 必须是 git repo
  (WorktreeManager 构造校验,否则 banner warning 静默不启用)
- **目录名安全校验**:限字符集 + 长度、拒 `.` / `..` 段、允许斜杠做嵌套
  (`phase1/api-designer` → `.worktrees/phase1/api-designer/`),防 LLM 输入路径遍历
- **完整生命周期**:创建(含 fast-path 恢复 —— 目录已存在只读文件系统不调 git)/
  进入 / 退出 / 删除
- **Initializer 4 步**:软链大依赖(`link_paths`)、复制本地配置(`copy_paths`)、
  设子目录 `core.hooksPath`、追加 `.worktrees/` 到 `.gitignore`
- **显式 cwd 而非 chdir**:Bash 工具加 `cwd` optional 参数,SubAgentManager 注入
  (LLM 不直接传);不传 → v1.2 老路径(`plan_cd` + commit session.cwd);传
  `cwd=<abs>` → 在该目录跑,fire-and-forget(执行完不更 session.cwd);非法 cwd
  (相对 / 不存在 / 非目录 / 有效 root 外)→ reject 不执行
- **role frontmatter**:`isolation: worktree` 声明隔离;不写(或 `null`)→ v1.2 老路径
  (共享主 project_root);`fork mode + isolation=worktree` 互斥(fork 强制走主 repo)
- **退出决策树**:`done`/`failed` → `exit(force=False)`(干净删 / 有未提交或未推送留
  detached,TUI 卡片显 `worktree: detached`);`canceled` → `force=True` 删
- **CleanupDaemon**:后台(默认 60s)三层过滤(task 活跃 → 时间 → 干净度)扫过期
  worktree 强清,任意一层不过就 skip
- **cache 取舍**:主 Agent BuiltPrompt byte-identical → Anthropic cache 命中零变化;
  worktree sub-Agent `env_info.cwd` 段不同 → 打破 byte-identical → 首次 LLM 请求
  cache miss(不引入第二份缓存,复用主 Agent template 只改 cwd 占位符)

### v1.4 范围

v1.4 分 **4 个独立 proposal + 独立 archive** 推进:

- **`v1-4-team-foundation`** (已完成 — 2026-07-10)
  - Team / Member / Message 数据层(`baozicode/teams/schema.py` frozen dataclass +
    `TeamNameValidator` 严格校验 + 8 个错误枚举)
  - Mailbox 文件层(`baozicode/teams/mailbox.py` 原子 JSONL append + state.json +
    wake.signal + 异步 `wait_for_wake`)
  - 跨平台 lockfile(`baozicode/teams/lockfile.py` POSIX `fcntl.flock` /
    Windows `msvcrt.locking` + 50ms 退避 + 30s stale 偷锁)
  - TeamStore + TeamsRegistry(目录操作 + 全局索引)
  - CLI 子命令(`baozicode/teams/cli.py` 5 子命令 `create / list / show /
    use / destroy` + 退出码 0-5 + `Error: <Enum>: <detail>` stderr 格式)
  - App 集成(`BaoZiCodeApp._build_teams_registry` + on_mount + 顶层 CLI 分发)
- **`v1-4-team-tools`** (本段 — 已完成)
  - **共享任务清单**(`baozicode/teams/tasks.py`)`Task` frozen dataclass +
    6 状态字面量(`pending/ready/in_progress/done/failed/canceled`)+
    `Tasks.append` / `read_all` / `update_status` / `find_ready` /
    `detect_cycles` 全走 `.tasks.lock` 串行化
  - **6 个 Lead-only 协作工具**(`baozicode/teams/tools.py`)全部
    `role_visibility=['lead']` + `register_team_tools` 注册到全局
    `ToolRegistry`:`team_dispatch` 派活 + 标 task in_progress /
    `team_send_message` 发任意文本(可含 APPROVED:/REJECTED:) /
    `team_cancel` 软取消 / 强杀(`os.kill(state.backend_pid, SIGTERM)` 占位,
    pane-backend 注入后接管) / `team_merge` 顺序合 `wt/<member>` 到 target /
    `team_task_create` 创建带 `depends_on` 的 task 自动 8 字符 hex id +
    cycle 检测 / `team_task_query` 按 status / assignee 过滤
  - **Approval 协议**(`baozicode/teams/approval.py`)走 mailbox 文本格式:
    member 写 `---PLAN-<id>--- ... ---END---` → Lead 写
    `APPROVED: <id>` 或 `REJECTED: <id> <reason>`;
    `ApprovalProtocol.parse_plan / parse_approval / send_approval /
    is_task_complete / is_task_failed` 静态方法
  - **MailboxNotifier**(`baozicode/teams/mailbox_notifier.py`)Lead Agent
    每轮 `_inject_reminders` 之前调 `build_reminder()` 扫所有 member
    outbox → 拼 `<system-reminder type="team_mailbox">` 块;识别
    TASK-COMPLETE 自动 `Tasks.update_status(done)` +
    `Mailbox.write_state(idle)`,TASK-FAILED 同理,PLAN 等待审批;
    dedup 用 `body+timestamp` hash 防重复注入
  - **角色过滤**:`ToolDefinition.role_visibility: list[str] | None`
    + `ToolRegistry.get_all_tools(role=...)` + `Agent(role=...)` keyword-only
    参数(默认 `'subagent'`),`role ∈ {'lead','member','subagent','coordinator'}`
    严格校验;`AGENT_ROLES` frozenset 在 `tools/base.py` 顶层
  - **App + ChatScreen 接线**:`BaoZiCodeApp.use_team(name)` 同步设
    `active_team_name` + 构造 `MailboxNotifier`;`ChatScreen` 重建
    Agent 时按 `app.active_team_name` 是否非空切 `role='lead'` /
    `'subagent'`,Lead Agent 拿到 13 工具(7 内置 + 6 team_*),
    subagent / member 拿 7 内置
  - **team_merge helper**(`baozicode/teams/merge.py`)
    `run_team_merge(project_root, team, *, target, dry_run=False)`
    `git rev-parse` 校验 repo + `git checkout target` +
    字典序 `git merge --no-ff wt/<name>` 顺序合,冲突
    `git merge --abort` + 收集 aborted 列表,best-effort
- **`v1-4-team-pane-backend`** (本段 — 已完成) — 在 foundation + tools 之上:
  - **5 种 BackendType**(`baozicode/teams/pane.py`)`BackendHandle` Protocol
    + 5 实现(pane-tmux / pane-iterm2 / pane-windows-terminal /
    coroutine / worktree-coroutine)
  - **BackendManager**(`baozicode/teams/backend_manager.py`)居中调度
    `effective_backend` 决策树 + `spawn_if_offline` asyncio.Lock dedup +
    `restore_panes` Lead 重启 hydrate + `cleanup_team` team destroy 清理
  - **pane_info.json**(`baozicode/teams/pane_info.py`)`PaneInfo` frozen
    dataclass + `PaneMemberInfo` + 原子 write-then-rename,持久化 backend
    类型 + pane 句柄 + pid + last_active_ts,跨 Lead restart 恢复
  - **MemberAgent + MailboxLayer**(`baozicode/teams/member_agent.py`)
    role='member' + 7 工具子集 + MailboxLayer 内部走 Mailbox staticmethod
    读写,每轮 fresh Agent(无跨 turn conversation)
  - **MemberMainLoop**(`baozicode/teams/member_loop.py`)
    `wait_for_wake(200ms 轮询 wake.signal)` + `_run_turn(订阅 events +
    tool → outbox 自动转)` + 异常不挂 turn + `request_terminate()` 优雅退出
  - **`baozicode member run` CLI**(顶层 `member` + `run` 子命令)
    bootstrap registry → chdir → bootstrap config/LLM/perms →
    构造 MemberMainLoop → SIGINT/SIGTERM handler → `await loop.run()`;
    退出码 0/3/6/4/5/2
  - **team-tools spawn 钩子**(`baozicode/teams/tools.py`):
    `execute_team_dispatch` 末尾调 `spawn_if_offline`(返回 content 含
    `backend=<type>`);`execute_team_cancel(terminate=True)` 改走
    `backend_manager.kill(grace_seconds=5.0)` 替代 v1-4-team-tools 阶段的
    裸 `os.kill(state.backend_pid, SIGTERM)`
  - **App 接线**(`baozicode/app.py`):`__init__` 加 `backend_manager` 字段;
    `_build_teams_registry` 末尾 `_ensure_backend_manager()` 构造 singleton;
    `register_team_tools` 接 `backend_manager` kwarg 注入闭包;
    `use_team` 不重建 / `on_unmount` 不清理(panes 跨 Lead restart 持久)
- **`v1-4-team-coordinator`** (本段 — 已完成) — 在 foundation + tools +
  pane-backend 之上,落地 Coordinator 监督者模式(三锁门 + 写类工具
  剔除 + active_role 接线):
  - **三锁门**(`baozicode/teams/coordinator.py`)
    `coordinator_enabled(config, team)` + `check_coordinator_locks(config, team)`;
    三锁 = 配置 `teams.coordinator.enabled` + 环境变量
    `BAOZICODE_COORDINATOR`(truthy,大小写不敏感)+ `team.coordinator=True`;
    整 `TeamsConfig.coordinator` 块省略视为未启用
  - **Schema 扩展**(`baozicode/teams/schema.py`)
    `Team.coordinator: bool = False`(默认 False 向后兼容,旧 team.json
    缺字段 `from_dict` 默认 False)+ `TeamsConfig.coordinator: CoordinatorConfig | None`
  - **ToolRegistry coordinator 角色过滤**
    (`baozicode/tools/registry.py`)
    `get_all_tools(role='coordinator')` 显式剔除 `Write` / `Edit` /
    `Bash`(写类工具),`tool_type='internal'` 工具(`load_skill` /
    `task`)不受剔除;6 个 `team_*` 工具 `role_visibility` 从 `['lead']`
    扩展到 `['lead', 'coordinator']`
  - **App 接线**(`baozicode/app.py`)
    `App.active_role: str = "subagent"` + `App.active_coordinator: bool` 字段;
    `use_team(name, *, coordinator=False)` keyword-only kwarg,三锁
    命中 → `active_role='coordinator'`,不命中 → 降级 Lead + stderr
    报告哪一锁缺失
  - **ChatScreen active_role**(`baozicode/tui/chat_screen.py`)
    重建 Agent 时读 `app.active_role`(优先)决定 `role` kwarg,
    fallback 到 `active_team_name` 推断
  - **CLI `team use --coordinator`**(`baozicode/teams/cli.py`)
    `--coordinator` store_true flag;三锁检查 + 降级报告 + 退出码
    0/3/5
  - **Coordinator 读 member outbox** — 现有 `Read` tool 即可,
    `v0.5` L2 PathSandbox 已白名单 teams 路径

**v1.4 explore 锁定的 12 个决策**(所有后续 proposal 引用):

1. **Pane backend**:tmux / iTerm2 / Windows Terminal 按环境优先级自动选,失败
   透明降级到 `coroutine`,不静默降级
2. **Resume load**:从 `state.json` 读全 conversation 上下文,新消息作为 user msg
   灌入,实现"从磁盘恢复上下文继续指派"
3. **Merge 冲突**:能自动合并就自动合并,搞不定 `git merge --abort` + 上报,
   不会留下半成品
4. **Wake**:Lead `Mailbox.touch_wake(member_dir)` 触发,pane 后端
   `wait_for_wake` 200ms 轮询 mtime
5. **Tasks 位置**:`<teams_dir>/<team>/tasks.jsonl` — 共享任务清单放
   user-global(`teams.dir`),不跟项目走
6. **Idle 触发**:仅在 task 完成时触发(不是聊天结束),符合 explore 锁定
7. **Coordinator git**:走 `team_merge` first-class 工具,**不**走 Bash
   `git merge`(防止 Lead 写出错的 git 命令)
8. **Approval protocol**:YAML frontmatter(`requires_approval: true`)+ body
   含 plan,Lead 用 `APPROVED: <id>` / `REJECTED: <id> <reason>` 特定格式回复
9. **跨 worktree 可见**:Lead 通过 Read 读其他 worktree 路径,
   v1.3 已有 L2 sandbox whitelist 可复用
10. **Lead 终止**:Main Loop self-drives 到 end,不靠外部 signal
11. **Member 命名**:Lead 在 `team_dispatch` 必须显式 `member=<name>`,不自动生成
12. **Team 生命周期**:Team 是 long-lived + reusable,不被 `destroy` 视为
    "项目结束就清";只有用户显式 `destroy` 才删

**Foundation 阶段**只覆盖 schema / mailbox / lockfile / lifecycle CLI 这一层
(决策 8 / 9 / 10 / 12),其余 9 个由后续 3 个 proposal 在此之上实现。

**team-tools 阶段**在 Foundation 之上落地决策 1 / 2 / 4 / 5 / 6 / 7 / 8 / 11
(8 个);决策 3 由 v1.3 L2 sandbox 已有 + 决策 9 / 10 foundation 已实现;
决策 12 由 v1-4-team-pane-backend 填充 backend spawn / resume 路径。

## 模块结构

```
baozicode/
├── __main__.py             # python -m baozicode
├── __init__.py
├── cli.py                  # argparse(--config/--resume/--new/--no-banner) + MCP/sessions/instructions/memory banner
├── app.py                  # Textual App(全套 v0.5+v0.6+v0.7+v0.8+v0.9+v1.0 字段)
├── agent/                  # Agent Loop 与事件契约
│   ├── events.py           # AgentEvent / StopReason(v0.7 加 COMPACTION_FAILED) / UsageStats / Progress
│   ├── collector.py        # StreamCollector + TurnSnapshot(双路收集)
│   ├── guards.py           # v0.5:record_denial_warn / should_inject_denial_reminder 替代终止
│   ├── scheduler.py        # 工具并发调度(方案 B + C 扩展点)
│   └── loop.py             # Agent 主循环(v0.5:_v5_executor + v1.0:skill_whitelist_check)
├── permissions/            # v0.5 — 五层防御
│   ├── types.py            # PermissionDecision / PermissionRule / MergedPermissions / PermissionMode
│   ├── blacklist.py        # L1:DangerousCommandBlacklist(TEXT_PATTERNS + token 扫描)
│   ├── sandbox.py          # L2:PathSandbox(real_root + symlink resolve + shell expansion 拦截)
│   ├── persistence.py      # append_rule_to_local_yaml / load_local_yaml(atomic 写)
│   ├── loader.py           # L3:load_permissions_layers(三层 YAML 合并)
│   ├── engine.py           # L3:RuleEngine(check + add_session_rule,deny-veto)
│   ├── mode.py             # L4:apply(decision, mode)(strict/default/permissive)
│   └── __init__.py         # check(call, ctx) 总入口 + bootstrap(project_root, config)
├── prompt/                 # v0.4 — 模块化 system prompt 拼装(v0.7-v1.0 持续增量)
│   ├── types.py            # BuiltPrompt / BuildContext / CacheBreakpoint / SystemReminder
│   ├── rules.py            # Rule + RuleRegistry + 7 DEFAULT_RULES + augment_tool()
│   ├── reminder.py         # PlanModeReminder / denial_rate_limit(v0.5) / active_skills(v1.0)
│   ├── builder.py          # PromptBuilder.build() + set_dynamic_section(v1.0)
│   ├── sections/           # 11 个 section renderer(7 固定 + env_info + 3 可选)
│   │   ├── identity.py / constraints.py / task_mode.py / action_exec.py
│   │   ├── tool_usage.py / tone_style.py / text_output.py
│   │   ├── env_info.py    # 走 user-role 消息(env / plan_mode / time_gap / post_compaction)
│   │   ├── memory.py      # v0.8:长期记忆索引段
│   │   ├── skills.py      # v1.0 改写:「可用 Skill(两阶段加载)」+ v0.4 旧 skills_dir fallback
│   │   └── custom.py
│   └── __init__.py         # 公开 API re-export
├── tui/
│   ├── chat_screen.py      # 主对话屏幕 + Agent 事件订阅 + Skill 注入(boot) + 斜杠分发(v0.9)
│   ├── startup_session_screen.py  # v0.8:启动 session 选择器(无 flag + 有历史时弹出)
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
├── tools/                  # 7 工具 + side_effect + path_args(v0.5) + tool_type(v1.0)
│   ├── base.py             # ToolDefinition(side_effect + path_args + tool_type)
│   │                       #   / ToolCall / ToolResult
│   ├── read.py             # side_effect=False, path_args=["file_path"]
│   ├── write.py            # side_effect=True, path_args=["file_path"]
│   ├── edit.py             # side_effect=True, path_args=["file_path"]
│   ├── bash.py             # side_effect=True, path_args=[] (L2 用 regex 提取)
│   ├── grep.py             # side_effect=False, path_args=["path"]
│   ├── glob.py             # side_effect=False, path_args=["path"]
│   ├── webfetch.py         # side_effect=False, path_args=[]
│   └── registry.py         # v0.6:ToolRegistry 类 + 模块级兼容层(支持 MCP + Skill 运行时注入)
├── mcp/                    # v0.6 — MCP 客户端
│   ├── types.py            # JsonRpcRequest/Response/Error/Notification + McpTool/McpCallResult
│   ├── jsonrpc.py          # JsonRpcDispatcher(请求/响应 id 配对)
│   ├── transport_stdio.py  # StdioTransport(子进程管道 + stderr drain task)
│   ├── transport_http.py   # HttpTransport(Streamable HTTP + SSE + Mcp-Session-Id)
│   ├── client.py           # McpSession(initialize → initialized → tools/list)
│   ├── adapter.py          # MCP ↔ ToolDefinition / ToolResult 转换(路径 args 启发式)
│   └── manager.py          # McpClientManager(多 server 生命周期 + 失败降级)
├── context/                # v0.7 — 两层 token 预算压缩
│   ├── schema.py           # ContextConfig / CompactionConfig / CompactionTelemetry / CompactionResult
│   ├── boundary.py         # 块/消息边界检测
│   ├── estimator.py        # token 估算
│   ├── layer1.py           # offload:单 block/message 超阈值 → 写盘 + preview
│   ├── layer2.py           # 摘要:LLM 6 段结构化(Goal/Progress/Decisions/Files/Open Issues/Next)
│   ├── orchestrator.py     # maybe_compact(messages, trigger, ctx) 编排器
│   └── storage.py          # .baozicode/context/<session>/ 目录 + 自动 .gitignore
├── instructions/           # v0.8 — 三层 BaoZiCode.md 加载
│   ├── schema.py           # InstructionLayer / LoadedInstructions
│   ├── loader.py           # 三层扫 + bootstrap(project_root, config)
│   └── include.py          # @include 解析(深度限 + 环路拦截 + 项目目录白名单)
├── memory/                 # v0.8 — 双层长期记忆 + 自动 LLM 抽取
│   ├── schema.py           # MemoryEntry / MemoryIndex / 4 类枚举
│   ├── store.py            # MemoryStore(单层目录读/写/索引更新)
│   ├── updater.py          # 异步 LLM 抽取(快照式并发 + 去重)
│   ├── overflow.py         # NORMAL/WARN/AUTO_COMPRESS/HUMAN_NEEDED 状态机
│   └── prompt.py           # 索引渲染进 system prompt(200 行 / 25KB 上限)
├── sessions/               # v0.8 — JSONL 会话存档 + resume
│   ├── schema.py           # SessionMeta / SessionMessage
│   ├── _id.py              # YYYYMMDD-HHMMSS-xxxx 格式化 + uuid 迁移
│   ├── archive.py          # SessionArchiver(JSONL 追加 + flush + fsync)
│   ├── cleanup.py          # retention_days 过期清理
│   └── resume.py           # load_session:坏行/orphan/time_gap/超 token 四件套
├── commands/               # v0.9 — Slash 命令注册中心
│   ├── registry.py         # CommandDef / CommandRegistry.freeze() / CommandType
│   ├── context.py          # CommandContext Protocol(7 方法 + 2 属性)
│   ├── dispatcher.py       # parse_command + dispatch
│   ├── completor.py        # TabCompleter.candidates()
│   ├── builtin.py          # 10 个内置命令(/help /compact /clear /plan /do /session /memory /permission /status /review)
│   └── __init__.py         # 公开 API re-export(dispatch/build_builtin_defs 延迟 import)
├── skills/                 # v1.0 — Skill 系统(两阶段加载 + 双层白名单)
│   ├── schema.py           # SkillFrontmatter(Pydantic 强校验) / SkillDef(frozen dataclass) / parse_frontmatter
│   ├── registry.py         # SkillRegistry.scan / reload / list_visible / lookup
│   ├── loader.py           # SkillLoader.load_skill + substitute_placeholders + LOAD_SKILL_TOOL
│   ├── activation.py       # ActiveSkill / SkillActivation + render_active_section + 斜杠挂载
│   ├── whitelist.py        # SkillWhitelistFilter(L2 动态) + validate_declared_tools(L1 启动期)
│   ├── execution.py        # SkillExecutor(shared/independent) + IndependentRunner + SkillExecutionResult
│   ├── bootstrap.py        # SkillSet + bootstrap_skills(project_root, tool_registry, skills_config)
│   └── builtin/            # 3 个样板 Skill
│       ├── commit/         # shared + Bash/Read
│       ├── review/         # independent + Bash/Read/Grep + history-bubbles=3
│       └── test/           # independent + Bash + history-bubbles=2
├── teams/                  # v1.4 Team Foundation + Tools + Pane Backend + Coordinator
│   ├── schema.py           # Team / Member / Message / MemberState frozen dataclass
│   │                        #   + TeamNameValidator(严格校验)+ BackendType Literal + 8 错误枚举
│   │                        #   + Team.coordinator: bool(v1-4-team-coordinator)
│   ├── mailbox.py          # Mailbox.append_message 原子 5 步协议 + read/write state + touch/await wake
│   ├── lockfile.py         # mailbox_lock(path, *, timeout, stale_seconds) 跨平台
│   │                        #   (POSIX fcntl.flock / Windows msvcrt.locking)+ 50ms 退避 + 30s 偷锁
│   ├── store.py            # TeamStore.create/load/from_name/show/add_member/destroy
│   ├── registry.py         # TeamsRegistry.bootstrap(config) + list_teams/get/create_team/delete_team
│   ├── cli.py              # add_subcommand(subparsers) + main(argv) — 5 子命令(create/list/show/use/destroy)
│   │                        #   + use --coordinator flag(v1-4-team-coordinator)
│   ├── coordinator.py      # v1-4-team-coordinator:coordinator_enabled / check_coordinator_locks
│   ├── tasks.py            # v1-4-team-tools:Tasks append/read_all/update_status/find_ready/detect_cycles
│   ├── tools.py            # v1-4-team-tools:6 个 Lead-only team_* 协作工具(role_visibility=['lead','coordinator'])
│   ├── approval.py         # v1-4-team-tools:ApprovalProtocol.parse_plan / send_approval / is_task_complete
│   ├── mailbox_notifier.py # v1-4-team-tools:MailboxNotifier.build_reminder() 每轮扫 member outbox
│   ├── merge.py            # v1-4-team-tools:run_team_merge(project_root, team, target) 顺序合 worktree
│   ├── pane.py             # v1-4-team-pane-backend:5 BackendType + BackendHandle Protocol
│   ├── backend_manager.py  # v1-4-team-pane-backend:BackendManager 居中调度 spawn/kill/restore
│   ├── pane_info.py        # v1-4-team-pane-backend:PaneInfo / PaneMemberInfo 持久化
│   ├── member_agent.py     # v1-4-team-pane-backend:build_member_agent + MailboxLayer
│   ├── member_loop.py      # v1-4-team-pane-backend:MemberMainLoop 长生命周期 polling
│   └── __init__.py         # 公开 API re-export(Team / Member / Mailbox / mailbox_lock / TeamsRegistry /
│                            #   TeamStore / BackendManager / 错误枚举 / coordinator_enabled /
│                            #   check_coordinator_locks / MailboxNotifier / ApprovalProtocol /
│                            #   run_team_merge / Tasks / Task / register_team_tools 等)
├── conversation/
│   └── manager.py          # 多轮历史(add_turn snapshot 重建,add_tool_result,set_archiver v0.8)
└── config/
    ├── schema.py           # Pydantic AppConfig / BackendConfig / Permissions / AgentConfig / RulesConfig
    │                        #   v0.5:PermissionRuleYaml / PermissionsV5 / AgentConfig.denial_warn_threshold
    │                        #   v0.6:McpServerStdioConfig / McpServerHttpConfig / AppConfig.mcp_servers
    │                        #   v0.7:CompactionConfig / 上下文阈值字段
    │                        #   v0.8:InstructionsConfig / MemoryConfig / SessionsConfig
    │                        #   v0.9:CommandsConfig(review_prompt)
    │                        #   v1.0:SkillsConfig(enabled/builtin_dir/user_dir/project_dir/summary_model/history_bubbles_default)
    │                        #   v1.4:TeamsConfig(enabled/dir) — AppConfig.teams 可选
    └── loader.py           # YAML + .env + ${VAR} 替换
                             #   v0.5:扫 permissions*.yaml sidecar 合并
                             #   v0.6:两层 mcp_servers 合并 + ${VAR} 展开
                             #   v0.7-v1.0:各新配置块 schema + 旧字段 fallback(memory_path / skills_dir)
                             #   v1.4:teams 块可选(整块省略 → AppConfig.teams = None,CLI 走默认)
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
   ├── permissions/ ───→ config/ + tools/base.py          (v0.5 单向依赖)
   ├── context/ ───────→ llm/base.py + config/schema.py    (v0.7 新增)
   ├── instructions/ ──→ config/schema.py                  (v0.8 新增)
   ├── memory/ ────────→ llm/base.py + config/schema.py    (v0.8 新增)
   ├── sessions/ ──────→ config/schema.py + llm/base.py    (v0.8 新增,resume 用)
   ├── commands/ ──────→ permissions/types.py + llm/base.py + config/schema.py + textual  (v0.9 新增)
   ├── skills/ ────────→ tools/base.py + conversation/manager.py + config/schema.py       (v1.0 新增)
   └── mcp/ ───────────→ tools/base.py + config/schema.py  (v0.6 新增)
```

- `tui/` 不直接 import `anthropic` / `openai`（必须经 `llm/factory.py`）
- `llm/` 不能 import `textual` 或 `prompt/`（避免反向依赖循环）
- `prompt/` 不依赖 `agent/` 或 `tui/`（被 agent 单向调用）
- `agent/` 不依赖 `tui/`（v0.3 把 Agent 与 TUI 解耦,Agent 只推事件流）
- `tools/` 不依赖 `tui/` / `llm/` / `conversation/` / `agent/`（纯函数 + 路径）
- `permissions/` 不依赖 `agent/` 业务代码,只共享 `tools/base.py` 的 `ToolCall` 类型;
  `agent/` 可选依赖 `permissions/` 的 `MergedPermissions` / `PermissionMode` 类型
- `context/` 可依赖 `llm/base.py` 的类型 + `config/schema.py` 的 `ContextConfig`;不依赖 `agent/`
- `instructions/` / `sessions/` 只依赖 `config/` + `tools/`;`sessions/resume` 例外(可依赖 `llm/base.py`)
- `memory/` 可依赖 `llm/base.py` 的类型(异步抽取时构造 LLMClient 提示);不依赖 `agent/`
- `commands/` 不依赖 `tui/` 业务代码(handler 只接 `CommandContext` Protocol),只 import
  `textual/screen.py` 拿 `push_modal` 类型;handler 与 textual 完全解耦
- `skills/` 不 import textual(slash 挂载留作调用方)、不 import llm 后端(模型选择由
  `config.skills.summary_model` 决定,实际调 LLM 留作 execution);`SkillExecutor.execute`
  是 stub,生产代码由 `BaoZiCodeApp._setup_skill_runner` 在运行时注入真实 runner
- 业务状态挂在 `App` 实例上，Screen 通过 `self.app` 访问

## 关键约定

- LLM 抽象：`LLMClient.stream(messages, system, tools) -> AsyncIterator[ContentDelta]`
- `ContentDelta.type`：`"text"` → str；`"tool_use"` → `ToolCall` 实例；`"usage"` → `UsageStats`
- Anthropic 的 `system` 走独立参数；OpenAI 走 `messages[0] role=system` —— 差异在每个 backend 内部消化
- `Message.content` 是 `Union[str, list[ContentBlock]]`：`str` 是 v0.1 快速路径；`list[ContentBlock]` 含 `TextBlock / ToolUseBlock / ToolResultBlock`
- 工具调用统一抽象：`ToolDefinition`（喂给 LLM）+ `ToolCall`（LLM 请求）+ `ToolResult`（喂回 LLM），后端 SDK 类型不出 `baozicode/llm/`
- 工具 `side_effect: bool`：并发调度的唯一信号(`True` 串行,`False` 可并行)
- 工具 `path_args: list[str]`：v0.5 — L2 PathSandbox 提取路径用的 argument 名
  (Read/Write/Edit = `["file_path"]`,Grep/Glob = `["path"]`,Bash/WebFetch = `[]`)
- 工具 `tool_type: "internal" | "external"`：v1.0 — `internal` 标系统工具(如 `load_skill`),
  `SkillWhitelistFilter` 看到 `internal` 直接放行(不受 Skill 白名单约束)

**Agent Loop 契约**（v0.3 核心 + v0.4 PromptBuilder + v0.5 五层防御 + v1.0 Skill 白名单）:
- `Agent.run(user_message) -> AsyncIterator[AgentEvent]`,7 种事件类型:`text / tool_call / tool_result / usage / progress / done / error`
- 8 种停止条件:`COMPLETED / MAX_ITERATIONS_REACHED / USER_CANCELLED / UNKNOWN_TOOL_HALLUCINATION /
  DENIALS_EXCEEDED / FAILED_TOOL_LOOP / STREAM_ERROR / COMPACTION_FAILED`(v0.7 新增)
- v0.5:`DENIALS_EXCEEDED` 不再是终止路径(deny 不终止 loop),但 enum 仍保留兼容
- StreamCollector 双路:实时 yield text 给 TUI,内部累加 TurnSnapshot 作为 Agent 决策的唯一可信源
- Plan Mode:`plan_mode=True` 时 `available_tools` 只含 `side_effect=False` 工具
- v0.4:`Agent.__init__(llm, tools, conversation, permissions, *, config: AppConfig)` —— 不再收字符串 `system_prompt`;`__init__` 内调用一次 `PromptBuilder().build()` 得 `self._prompt`,每轮 `stream(...)` 用 `self._prompt.stable_system` + `augmented_tools` + `cache_breakpoints`,通过 `_inject_reminders(messages, iteration)` 把 env + plan_mode `<system-reminder>` 拼到 `messages[-2]`
- v0.5:`Agent.__init__(..., *, session_mode, merged_permissions, permissions_engine)` 收 v0.5 5 层防御句柄;`_v5_executor` 调 `permissions.check(call, self._merged)`,deny 走 `_DENIAL_REMINDER_BODY` 注入,fallthrough 走 `_handle_user_decision` (调 `permission_callback` 即 L5 Modal)
- v1.0:`Agent.__init__(..., *, skill_registry, skill_activation)` 收 Skill 句柄;`_v5_executor`
  在 permission check 之前先做 `skill_whitelist_check(call)` —— 不在白名单 → `is_error`(deny 而非 fallthrough);
  每轮 `_inject_reminders` 也注入 `SkillActivation.render_active_section()` 钉到顶部

**五层防御权限契约**（v0.5）:
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

**Prompt 模块契约**（v0.4 增量,v0.7-v1.0 持续加 section）:
- `BuiltPrompt = {stable_system, dynamic_messages, augmented_tools, cache_breakpoints}` —— 一次构建,每轮复用,`stable_system` byte-identical 让 LLM 命中缓存
- 11 个 sections:7 固定(`identity / constraints / task_mode / action_exec / tool_usage / tone_style / text_output`)+ `env_info`(走 user-role 消息)+ 3 可选(`memory v0.8 / skills v1.0 / custom`,空内容跳过)
- `RuleRegistry.augment_tool(tool)` 在工具 description 前注入被激活规则的 `【必读】/【建议】` 前缀,被禁用的规则整套消失(既不出现在 system 段,也不注入 description)
- 7 个 DEFAULT_RULES:`edit_requires_read / prefer_specialized_tools / bash_timeout / parallel_limit / error_then_decide / absolute_paths / webfetch_to_file`,全部默认 True,在 `AppConfig.agent.rules` 里可独立开关
- PlanModeReminder 节奏:iteration 1, `1+plan_reminder_interval`, `1+2*plan_reminder_interval`, ...(默认 interval=5);`agent.enable_system_reminders=False` 时整体跳过注入
- v0.5:`_inject_reminders` 还注入 `<system-reminder type="denial_rate_limit">` 当任一工具 `deny_counts[name] >= denial_warn_threshold`(默认 5)
- v0.7:`<system-reminder type="post_compaction">` 在摘要后追加,提醒 LLM 摘要不可信,需要细节重新调 Read/Grep/Bash
- v1.0:`PromptBuilder.set_dynamic_section(name, renderer)` / `render_dynamic_sections(ctx)`
  hook 让 ChatScreen 在每轮 Agent.run 前注入「当前激活 Skill」动态段(`<system-reminder type="active_skills">`);
  `SkillActivation.render_active_section()` 是 renderer,激活 Skill body 替换占位符后按 load 顺序拼接

**LLM 缓存接口契约**（v0.4 落地,v0.5+ 后端具体生效）:
- `LLMClient.stream(messages, system, tools, *, cache_breakpoints=None)` —— cache_breakpoints 是 keyword-only,4 个后端都接受,v0.4 不实际添加 cache_control 标记
- BuiltPrompt 默认 2 个缓存断点:`CacheBreakpoint("system_start", priority=100)` + `CacheBreakpoint("after_tools", priority=80)`
- 命中率通过 `UsageStats.cache_read_tokens / (cache_read_tokens + input_tokens)` 计算,`/status` 命令展示

**上下文压缩契约**（v0.7）:
- `ContextConfig = build(context_window_tokens, trigger, compaction)` —— trigger `auto` 走 `-13K`,
  `manual` 走 `-3K`;由 `App._build_context_config(trigger=...)` 派生并复用
- `maybe_compact(messages, trigger, ctx)` → `(new_messages, CompactionResult)`;自动触发时挂在每轮
  Agent 决策前,手动触发 `/compact` 在 Agent 空闲直接调,运行中通过 `agent.request_compact()`
  在下个迭代顶部生效
- Layer 1 offload 文件存 `<project_root>/.baozicode/context/<session_id>/<block_id>.md`,
  Message 内的 `TextBlock` 替换为 `preview(头 25 行 + ... + 尾 25 行)` + `<system-reminder type="offloaded">` 提醒
- Layer 2 摘要 prompt:system 提示 "Never call tools";输出分两段(草稿丢弃 + 正文);
  失败熔断:连续 3 次失败 → `StopReason.COMPACTION_FAILED`
- `CompactionTelemetry` 累积 `compaction_count / tokens_saved / last_compact_ts`,`/status` 显示
- `/clear` + `on_unmount` 调 `ContextStorage.cleanup()` 清空本 session 的 offload 目录

**指令加载契约**（v0.8）:
- `LoadedInstructions = {layers: list[InstructionLayer], concatenated: str}` —— 三层按优先级
  拼接后注入 `stable_system` 顶部
- `@include <relpath>`:深度 ≤ 5,环路用 `visited: set[Path]` 拦截,跨项目路径(realpath 白名单)被拒
- 三层全无 → `layers=[]`,banner 打印一行建议,不阻断启动
- 启动顺序:`permissions → instructions → memory → sessions`(避免 memory 提示依赖 instructions)

**Memory 契约**（v0.8）:
- `MemoryStore(user_dir | project_dir)` 单层,持 `entries: list[MemoryEntry]` + `index_total_lines/bytes`
- 4 类枚举:`user-pref / correction / project / reference`;`updater` 按类型自动路由
- 索引上限 200 行 / 25KB;`MemoryOverflowHandler._classify(total_lines, total_bytes)`
  → `NORMAL / WARN / AUTO_COMPRESS / HUMAN_NEEDED`
- 自动合并:超限 → 调 LLM 合并相似笔记(回 `NORMAL`);连续失败 → `HUMAN_NEEDED`
- 跨 session 删除别人写的笔记被拒(防互踩);`update` 允许跨 session 追加 / 合并
- 启动时若 `memory_path` 旧文件存在 + 新双层目录都空 → banner WARN + 当 user 级索引读;v0.9 移除 `memory_path`

**Sessions 契约**（v0.8）:
- `SessionArchiver(sessions_root, session_id)` —— 追加写 `<sessions_root>/<session_id>.jsonl`;
  每条 `ConversationManager.add_*` 同步落盘,中间 flush + fsync;崩只会丢最后一行
- `session_id` 格式:`YYYYMMDD-HHMMSS-xxxx`(20 字符,4 字符随机 hex 防同秒撞车);
  启动时 `migrate_uuid_context_dirs` 自动迁移 v0.7 旧 uuid 目录,撞名挂 `_legacy`
- `load_session(sid, sessions_root, ..., time_gap_threshold_hours)` —— 四件套:
  坏行(JSON 解析失败)跳过 / orphan tool_call 截断到 call 之前 / token 超限调一次
  `maybe_compact` / 间隔 > `time_gap_threshold_hours`(默认 8h)插 `<system-reminder type="time_gap">`
- `cleanup_expired(sessions_root, retention_days=30)` 启动时自动跑,过期 JSONL + 对应
  context 目录一并删除
- `BaoZiCodeApp.resume_session(sid)` —— 替换 `conversation.messages` + 切换 `archiver`
  到旧 sid(后续 `add_*` 写到旧 JSONL,实现"续接");失败 → ValueError(TUI 弹错误)
- `BaoZiCodeApp.start_new_session()` —— 分配新 sid + 清空 conversation + 关闭旧 archiver
- 启动选择决策:CLI `--new` 跳过选择 / `--resume <sid>` 验证后直接 resume / 无 flag + 有 sessions → 弹 `StartupSessionScreen`

**Commands 契约**（v0.9）:
- `CommandDef = {name, aliases, description, usage, type, params_hint, hidden, handler}`
  启动时一次性注册进 `CommandRegistry.freeze()`,撞名(`name` 或 `alias` 冲突)→ SystemExit
- `CommandType ∈ {LOCAL, UI_STATE, PROMPT}` —— `handler(ctx, args) -> CommandResult`;
  dispatcher 按 type 分支:`LOCAL` 直接跑(无回显)/ `UI_STATE` 切界面状态 /
  `PROMPT` 把 `PromptResult.text` 灌进 `Agent.run()`
- `CommandContext` Protocol:7 方法(`show_info / show_error / send_to_agent / switch_mode /
  get_token_usage / refresh_status / push_modal`)+ 2 属性(`app / config`);handler 不 import
  textual 不 import 业务模块,可用 `StubCommandContext` 单元测试
- `TabCompleter.candidates(partial_input)` —— 单匹配直接补,多匹配返回列表,`hidden=True` 不参与,
  已进 args 区(输入含空格)不接管
- 11 个内置命令(`baozicode/commands/builtin.py`):`/help /compact /clear /plan /do /session /
  /memory /permission /status /review`(v0.9 10 个)+ `/skill`(v1.0 第 11 个);
  v0.9 起删除 6 个老命令(`/exit /model /tools /mcp /stop /auto`),`/resume /new` 合并入 `/session`

**Skills 契约**（v1.0）:
- `SkillFrontmatter` Pydantic 强校验:`name`(必填,`^[a-z][a-z0-9-]*$`)/ `description`(必填)/
  `mode`(shared|independent, 默认 shared)/ `allowed-tools`(list, 默认 None 全允许)/
  `history-bubbles`(int ≥ 0,默认 0,上限 `MAX_HISTORY_BUBBLES=50`)/ `model`(独立模式子 Agent 模型)/
  `hidden`(bool,默认 false)
- `SkillDef` frozen dataclass:`{frontmatter, body, source, path}` —— `source ∈ {"builtin", "user", "project"}`
- `SkillRegistry.scan(project_root)` —— 3 级扫 + frontmatter 解析 + L1 校验 `allowed-tools` 全部
  存在(否则 SystemExit);失败文件跳过不阻断;`reload(name)` 立即重读单文件
- `SkillLoader.load_skill(name, args)` —— `substitute_placeholders(body, args)` 先替换 `{var}` /
  `{var:default}`,再 `SkillActivation.add(...)` 钉入活跃集合 + 注册同名 slash 短命令 + 返回渲染 body
- `SkillActivation` 持 `active: list[ActiveSkill]` + `render_active_section()`(输出
  `<system-reminder type="active_skills">` 块,多 Skill 按 load 顺序拼接);`/clear` 同步清空
- `SkillWhitelistFilter` 双层:
  - L1 `validate_declared_tools(frontmatter.allowed_tools, tool_registry)` 启动期校验存在性
  - L2 运行时 `is_allowed(call)` —— `tool_type=internal` 直接 True;否则看 `active_declared_tools()`
    (所有激活 Skill 的 `allowed-tools` 取 union)是否包含 `call.name`
- `SkillExecutor.execute(skill, args)` —— `mode=shared` 直接追加 prompt 进主对话;
  `mode=independent` 调注入的 `IndependentRunner`,默认是 stub,生产代码由
  `BaoZiCodeApp._setup_skill_runner` 注入真实 runner(开新 ConversationManager + 新 Agent,
  `history-bubbles=N` 把主对话最近 N 条消息一起送进子对话,完成后把最后一条 text 摘要回流)
- `LOAD_SKILL_TOOL`(`baozicode/skills/loader.py`)是 `ToolDefinition`,`tool_type="internal"`,
  在 `App.on_mount` 异步注册到模块级 `ToolRegistry` 单例,handler = `loader.execute`;
  注册幂等:撞名 ValueError 视为已注册
- `bootstrap_skills(project_root, tool_registry, skills_config)` → `SkillSet{registry, activation,
  loader, executor}` —— `SkillsConfig=None` 走 bootstrap 全部默认;`enabled=False` 返回空集
- v0.4 兼容:`skill_registry=None` 时 `prompt/sections/skills.py` 退回 v0.4 旧路径(扫
  `skills_dir/*.md`,标题保留「已激活 Skill」)

- Bash cwd 三状态机：会话启动锁项目根 → `cd` 跟随 → 每次执行前 `Path.resolve().is_relative_to(project_root)` 防逃逸
- 入口命令：`baozicode`（在 `pyproject.toml` 的 `[project.scripts]` 声明）
- 配置：YAML 写非敏感配置，`.env` 写 API Key，YAML 用 `${ENV_VAR}` 占位符引用；
  v0.2 旧 `permissions:` 块可选,v0.5 新 `permissions_v5:` 块可选;`agent.{max_iterations,enable_system_reminders,plan_reminder_interval,denial_warn_threshold,rules}` 块可选;
  sidecar `permissions*.yaml` 在主 config 同目录自动合并;
  旧字段 `memory_path`(v0.8)/ `skills_dir`(v0.4 兼容 v1.0)检测到文件存在 → banner WARN + 走 fallback

## OpenSpec

`openspec/changes/` 下是 spec-driven 的变更提案。已归档 `v0-1` / `v0-2` / `v0-3-agent-loop` /
`v0-4-prompt` / `v0-5-permissions` / `v0-6-mcp-client` / `v0-7-context-management` /
`v0-8-memory-and-sessions` / `v0-9-command-registry` / `v1-0-skills`(共 10 个)。
`openspec/specs/` 当前 25 个 capability(覆盖 v0.1 → v1.0)。
当前无活跃变更;下一个提案按 spec 节奏起新目录(命名 `vN-M-...`)。