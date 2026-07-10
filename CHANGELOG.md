# Changelog

BaoZiCode 所有重要变更记录在此。版本号遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)。

## [v1.4] — 2026-07 (Foundation)

### 新增能力

v1.4 是分 4 个独立 proposal 推进的大版本。**本段仅覆盖 v1-4-team-foundation**:
Team 数据层 + Mailbox 文件层 + 跨平台 lockfile + 5 子命令 CLI。
后续 3 个 proposal 在此之上扩展(team-tools / pane-backend / coordinator)。

- **Team 数据模型**(`baozicode/teams/schema.py`)
  - `Team` / `Member` / `Message` / `MemberState` frozen dataclass,持久化
    到 `<teams_dir>/<team>/team.json` 和 `<member>/{inbox,outbox}.jsonl`
  - `TeamNameValidator.validate(name)` 严格校验:`[a-z0-9-]` 2-30 字符,
    必须以字母开头、字母数字结尾、拒连续 `--`;拼错立即
    `TeamNameInvalid` 子类(`TeamNameTooShort` / `TooLong` / `BadChar` /
    `BadStart` / `BadEnd` / `DoubleHyphen`)
  - `BackendType` Pydantic Literal 强校验(5 种后端:`pane-tmux` /
    `pane-iterm2` / `pane-windows-terminal` / `coroutine` /
    `worktree-coroutine`);LLM 拼错 fail-fast
  - 8 个错误枚举:`TeamAlreadyExists` / `TeamNotFound` /
    `MemberAlreadyExists` / `MemberNotFound` 全部从标准 IO 异常派生
    (`FileExistsError` / `FileNotFoundError`),LLM 一看就懂

- **Mailbox 文件层**(`baozicode/teams/mailbox.py`)
  - `Mailbox.append_message` 5 步原子协议:tmp 文件 + flush + fsync +
    `shutil.copyfileobj` 追加 + 目标 fsync + 删 tmp + release 锁
  - `Mailbox.read_messages` skip 坏行(JSONL 部分损坏仍可读),
    `skip_bad_lines=False` 时 raise `ValueError` 严格要求
  - `Mailbox.read_state` 缺字段填默认(`status="offline"` /
    `last_active_ts=None` / `current_task=None` / `backend_pid=None`),
    0 字节 / 不存在都走默认
  - `Mailbox.write_state` write-then-rename 原子写,无 tmp 残留
  - `Mailbox.touch_wake` / `Mailbox.wake_initialized` / `Mailbox.wait_for_wake`
    异步 poll `wake.signal` mtime,200ms 间隔;`timeout=30.0` 默认;
    已有 wake.signal 不立即返回,要 mtime 变化才触发

- **跨平台 lockfile**(`baozicode/teams/lockfile.py`)
  - `mailbox_lock(path, *, timeout=5.0, stale_seconds=30.0)` context manager
    按 `sys.platform` 分发:POSIX `fcntl.flock`(advisory)+
    Windows `msvcrt.locking`(mandatory)
  - 50ms 退避重试,busy 错误覆盖 `errno=11/13/33/35/158`
    (POSIX `EAGAIN` / `EACCES` / `EDEADLK` / Windows
    `ERROR_LOCK_VIOLATION`)
  - 锁内容 `{pid}\n{hostname}\n{ts}\n` 写完 fsync 落盘,便于 debug
    谁持有锁 / 锁僵了多久
  - Stale 偷锁:mtime 超过 `stale_seconds` 的锁视为过期,下个 caller 偷

- **TeamStore + Registry**(`baozicode/teams/store.py` + `registry.py`)
  - `TeamStore.create` 原子建 team 目录 + 写 `team.json`,失败回滚删目录
  - `TeamStore.add_member` 同步建 `<member>/` 子目录 + 4 个文件 +
    默认 `state.json`(offline)
  - `TeamStore.destroy(*, confirm=False)` 默认拒,需 CLI 显式 `--yes`
  - `TeamsRegistry.bootstrap(config)` 从 `TeamsConfig.dir` 建索引,
    `mkdir(parents=True, exist_ok=True)` 首次启动零手动干预
  - `list_teams` 字典序(跟 `ls` 一致)+ 跳过无 `team.json` 的目录
  - `get(name) -> TeamStore | None`(不抛);`delete_team(*, confirm)`

- **CLI 子命令**(`baozicode/teams/cli.py`)
  - `add_subcommand(subparsers)` 注册到顶层 argparse,支持
    `baozicode team <action>` 二级结构
  - 5 子命令:`create` / `list` / `show` / `use` / `destroy`
  - `--teams-dir <PATH>` 全局覆盖(应急 / 测试)
  - 退出码:0 / 1 / 2(名不合法)/ 3(not found)/ 4(IO)/ 5(config)
  - 错误格式 `Error: <EnumClass>: <detail>` 走 stderr
  - `destroy` 默认 stdin 确认(`[y/N]`),`--yes` 跳过,`--force` 容错
    目录不在
  - `main(argv=None)` 独立跑入口(`python -m baozicode.teams.cli`)

- **配置 schema**(`baozicode/config/schema.py`)
  - `TeamsConfig` Pydantic:`enabled: bool = True` +
    `dir: Path = "~/.config/baozicode/teams/"`
  - `AppConfig.teams: TeamsConfig | None = None`(整块省略走默认)
  - `config.example.yaml` 加 `teams:` 完整示例(注释点明 coordinator /
    pane_backend 留给后续 proposal)

- **App 集成**(`baozicode/app.py` + `baozicode/cli.py`)
  - `BaoZiCodeApp._build_teams_registry()` 同步构造 idempotent registry
  - `BaoZiCodeApp.on_mount` 末尾调 `_build_teams_registry()`,
    `self.teams` 句柄挂在 App 上
  - `BaoZiCodeApp.on_unmount` 释放 `self.teams`(无 IO,仅丢引用)
  - 顶层 `baozicode team ...` 子命令分发到 `teams.cli.main`
  - `teams.enabled=False` 时 `self.teams = None`,CLI 仍可用

### 测试覆盖

- `tests/test_teams_v14_schema.py` — 69 tests(名字校验 / Team JSON /
  Member / Message / BackendType)
- `tests/test_teams_v14_lockfile.py` — 14 tests(8 平台无关 + 6 POSIX-only)
- `tests/test_teams_v14_mailbox.py` — 23 tests(append / read / state /
  wake / wait_for_wake)
- `tests/test_teams_v14_store.py` — 29 tests(create / load / add_member /
  destroy / registry)
- `tests/test_teams_v14_cli.py` — 23 tests(5 子命令 happy / 错误退出码 /
  destroy 确认)
- `tests/test_teams_v14_config.py` — 12 tests(TeamsConfig 默认 /
  AppConfig.teams 字段 / YAML 加载)
- `tests/test_teams_v14_app.py` — 11 tests(App bootstrap / on_mount /
  on_unmount / 顶层 CLI 分发)
- 共 **181 tests**,全平台通过(Windows 锁 3 个 POSIX-only skipped)

### 无 breaking change

v1.3 项目升级 v1.4 后,主对话照常跑,SubAgent / Worktree / Hooks / Skills /
Memory / Sessions 全不动。新增 `baozicode team <action>` CLI 子命令 +
`teams:` 配置块(可选)。

### 后续 proposal(本段**不**实现)

- **v1-4-team-tools** — `team_dispatch` / `team_send_message` /
  `team_cancel` / `team_merge` 协作工具
- **v1-4-team-pane-backend** — tmux / iTerm2 / Windows Terminal pane
  后端实际派生 + 唤醒
- **v1-4-team-coordinator** — 双锁开关(配置 + 环境变量)+ 剥夺写工具

详细迁移 + 配置块 + CLI 用法 + Python API + FAQ 见
`docs/migrations/v1.3-to-v1.4.md`。

## [v1.3] — 2026-07

### 新增能力

- **Worktree 隔离**(`baozicode/worktree/`)
  - sub-Agent role frontmatter 加 `isolation: worktree` → 该 sub-Agent 在独立
    git worktree(`.worktrees/<name>/`)里跑,与主 Agent / 其它 sub-Agent 的
    文件改动互不打扰;用 git 原生多工作树(共享版本库 + 各自分支 `wt/<name>`)
  - **目录名安全校验**:限字符集 + 长度、拒 `.` / `..` 段、允许斜杠做嵌套
    (`phase1/api-designer` → `.worktrees/phase1/api-designer/`),防路径遍历
  - **完整生命周期**:创建(含 fast-path 恢复 —— 目录已存在只读文件系统不调
    git)/ 进入 / 退出 / 删除,`WorktreeManager` 编排
  - **环境初始化**(Initializer 4 步):软链大依赖(`.venv` / `node_modules`)、
    复制本地配置(`.env` / `BaoZiCode.md`)、配子目录 git hooks
    (`core.hooksPath`)、追加 `.worktrees/` 到 `.gitignore`
  - **显式 cwd 而非 chdir**:Bash 工具加 `cwd` optional 参数,SubAgentManager
    自动注入(LLM 不感知);所有路径相关缓存(文件内容 / 系统提示词 / 项目指令 /
    记忆)用绝对路径 key,天然按目录隔离,不需切换清缓存
  - **退出变更保护**:exit 决策树 —— 全干净 → 删 / 有未提交或未推送 commit →
    留 detached(TUI `SubAgentCard` 显 `worktree: detached`);`canceled` → force 删
  - **后台清理**:`CleanupDaemon`(默认 60s 一次)三层过滤(task 活跃 → 时间 →
    干净度)扫过期 worktree 强清,任意一层不过就 skip
  - **配置**:`config.yaml` 的 `subagents.worktree` 块(`enabled` / `link_paths` /
    `copy_paths` / `max_concurrent` / `retention_minutes` /
    `daemon_interval_seconds`);默认 `enabled: false`,零行为变化
  - **App 装配**:`_build_worktree_manager`(worktree enabled + git repo 才构造)+
    on_mount worker 起 daemon + on_unmount 强清所有 worktree

### 兼容性

- 默认不启用(`worktree.enabled: false`)→ 走 v1.2 老路径,byte-identical
- role frontmatter 不写 `isolation`(或 `null`)→ sub-Agent 共享主 project_root
- Bash 不传 `cwd` → v1.2 老路径(`plan_cd` + commit session.cwd,完全等价);
  传 `cwd=<abs>` → fire-and-forget(执行完不更 session.cwd)
- project_root 非 git repo → WorktreeManager 构造失败 → 系统静默不启用(banner 警告)
- **cache 取舍**:主 Agent prompt byte-identical → Anthropic cache 命中零变化;
  worktree sub-Agent 因 `cwd` 段不同 → 首次 LLM 请求 cache miss(不引入第二份缓存)

### 迁移

- 详见 `docs/migrations/v1.2-to-v1.3.md`

## [v1.1] — 2026-07

### 新增能力

- **Hooks 生命周期**(`baozicode/hooks/`)
  - 在 Agent 生命周期的关键节点(session.start/end / turn.start/end /
    message.received/sent / tool.pre/post / system.error / system.compaction /
    system.cancel 共 11 个事件)挂「事件 + 条件 + 动作」三要素规则
  - 条件复用权限规则的匹配语法:`exact / glob / regex / not_exact /
    not_glob / not_regex`,逻辑组合用 `all` 或 `any` 二选一;省略即无条件
  - 4 种 action:执行 shell 命令(`exit_code` 判 deny)、注入提示词(3 档 slot:
    `sticky_reminder` / `stable_system` / `temp`)、发 HTTP 请求、用 simpleeval
    沙箱解析 `parse_expr` 决定 deny
  - 流水线插到五层防御中间:**L1 → hook.pre → L2-L5 → execute →
    hook.post**,L1 永远是 hard-wall(hook.pre 改不掉),`tool.post` 用
    `try/finally` 包住整个流水线,任何 tool_call 尝试必触发
  - `ToolResult` 增字段:`execution_status` (block_l1 / block_hook_pre /
    block_permission / executed_success / executed_failed)、`denied_by`
    (l1_blacklist / hook_pre / l2_l5_permission)、`denied_hook_id`;
    `is_error` 由 `__post_init__` 派生,旧调用方式完全兼容
  - 执行控制:`run_once`(全 session 只跑一次)/ `async`(只对 `tool.post`
    允许)/ `timeout_seconds`(默认 30,拒绝 hook 后台挂死)
  - 失败策略:**fail-open** — hook 抛任何异常只 `log.warning`,
    `Agent.run` 主流程不阻断(与权限系统「fail-closed」对称)
  - 集中校验:启动期 `HookRegistry.freeze()` 收集所有错误一次性报
    (duplicate id / invalid event / async+tool.pre / slot=stable_system
    on tool.* 等),`SystemExit(1)` 拒绝启动
  - 审计:`HookAuditLog` 异步 JSONL append + 100 MB 启动期轮转,
    `<project>/.baozicode/hooks/<session>.audit.jsonl`
  - 配置在 `config.yaml` 顶层 `hooks:` 块(YAML 列表,保留声明顺序),
    启动顺序接在 permissions 之后、instructions 之前

### 兼容性

- v1.0 配置 + 旧命令 + 旧 Skill `skills_dir/*.md` 单文件路径全部继续工作
- 没有 `hooks:` 块的 v1.0 项目,Agent 走 legacy `_v5_executor`
  (L1 → L2 → L3 → L4 → L5 → execute),与 v1.0 byte-identical 行为

### 已归档变更

- `openspec/changes/archive/2026-07-09-v1-1-hooks-lifecycle/`(proposal +
  design + tasks + 4 个 delta spec + 1 个新 capability spec)
- 迁移指南:`docs/migrations/v1.0-to-v1.1.md`(8 节 + 10 个 FAQ:
  Hook YAML schema / 11 个事件 / 4 种 action 字段表 / ToolResult 派生关系 /
  流水线形态 / 配置块 / 最少动作升级 / 常见 FAQ)

## [v1.0] — 2026-07

### 新增能力

- **Skill 系统**(`baozicode/skills/`)
  - 把可复用 AI 操作封装成独立 Markdown 文件 + YAML frontmatter
  - 三级存放(项目 > 用户 > 内置),同名按优先级覆盖,单个文件解析失败不阻断
  - 两阶段加载:启动时名字 + 一句话说明,要用时调 `load_skill` 工具按需加载完整 SOP
  - 两种执行模式:`shared`(共享当前对话,结果留主历史)/ `independent`(开子对话
    跑完摘要回流,可带 `history-bubbles` 条历史)
  - 工具白名单双层防御:`allowed-tools` 启动期 L1 校验(不存在即 panic)+ 运行时
    L2 收窄(命中 union 放行,未命中拒);`load_skill` 自身是 system 工具,不受白名单约束
  - 激活 Skill 的 body 钉在每轮 env 上下文最顶部的 `<system-reminder type="active_skills">`,
    多 Skill 并行激活,正文按 load 顺序拼接
  - 启动时自动注册为 `/skill` 短命令(`/skill list` / `/skill <name> [args]` / `/skill clear`),
    改动 SKILL.md 后 `registry.reload(name)` 立即生效
  - `/clear` 同步清空已激活 Skill
  - 占位符替换:`{var}` 和 `{var:default}` 在激活时用 args 替换
  - 目录型 Skill(整个 `<name>/` 目录作为能力包分发):入口 SKILL.md + 模板/示例/脚本
    都可以一起打包,frontmatter 只描述入口

- **3 个内置 Skill**
  - `commit` — `mode: shared`, `allowed-tools: [Bash, Read]` —
    根据 `git diff --staged` 生成 conventional commit message 并执行 commit
  - `review` — `mode: independent`, `allowed-tools: [Bash, Read, Grep]`,
    `history-bubbles: 3` — 子对话审查自 `{since}` 起的改动,三段报告(摘要/风险/建议)
  - `test` — `mode: independent`, `allowed-tools: [Bash]`,
    `history-bubbles: 2` — 子对话跑 pytest,失败根因分析回流

- **`SkillsConfig` 配置块**(`config.yaml`)
  - `enabled` / `builtin_dir` / `user_dir` / `project_dir` / `summary_model` /
    `history_bubbles_default`,整个块可省略
  - `enabled: false` → 整套系统空集,prompt section 也不出现「可用 Skill」段
  - `AppConfig.skills: SkillsConfig | None`,默认 None 走 bootstrap 默认

- **v0.4 兼容**
  - 旧 `skills_dir` 字段仍被 `prompt/sections/skills.py` 识别 — 若未传
    `skill_registry`(例如测试或外部调用),section 退回 v0.4 旧路径(扫
    `skills_dir/*.md`),标题保留「已激活 Skill」
  - 现有 `commit` / `review` / `test` 3 个老 `.md` 文件被自动识别,无需迁移

- **测试覆盖**:1018 个测试全部通过(+12 个 v1.0 e2e + 17 个 v1.0 SkillsConfig
  + 5 个 Skills 注入 + 7 个 App 集成 + P1-P7 各模块单元测试)

### 兼容性

- **完全向后兼容** v0.9 — 无 breaking change
- 旧 `skills_dir` 字段(单层目录放 `.md` 文件)仍能渲染,不传 `skill_registry`
  时自动走 fallback
- 11 个老 slash 命令不变;`/skill` 是新增第 12 个
- 启动 banner 在有 builtin Skill 时多一行:`[Skill] X 个可用 (commit, review, test)`

## [v0.9] — 2026-07

### 新增能力

- **Slash 命令注册中心** (`baozicode/commands/`)
  - 顶层包 `commands/{registry,context,dispatcher,completor,builtin}.py`,
    元数据集中 (name / aliases / description / usage / type / params_hint / hidden / handler)
  - 启动时一次性 alias 冲突检测 (`registry.freeze()`),撞名直接 SystemExit,不延迟到运行
  - 大小写不敏感:用户在输入框打 `/PLAN` / `/Plan` 都命中 `/plan` 主名
  - 10 个内置命令:`/help /compact /clear /plan /do /session /memory /permission /status /review`
    - `/permission` 主名 + `/permissions` 别名(沿用 v0.5-v0.6 老命令拼写,迁移无阻力)
  - CommandResult 联合类型返回(`LocalResult` / `UiStateResult` / `PromptResult(text)`),
    dispatcher 按 type 分支决定下一步动作(本地 / 切状态 / 灌进 Agent)

- **三类命令执行模式**
  - `LOCAL`: 纯本地,无 chat 回显(handler 自己 `ctx.show_info`)
  - `UI_STATE`: 影响界面状态(切 plan_mode / session_mode / refresh status)
  - `PROMPT`: 把预设 prompt 灌进 Agent 完整流程(handler 返回 `PromptResult(text)`,
    dispatcher 自动 dispatch 到 `Agent.run(text)`)
  - `/review` 是唯一 PROMPT 类型,演示预设 prompt → Agent 完整流程

- **Narrow CommandContext 接口**
  - 7 个方法:`show_info / show_error / send_to_agent / switch_mode / get_token_usage / refresh_status / push_modal`
  - 2 个属性:`app` / `config`(escape hatch,handler 可拿全 app 句柄)
  - handler 与 textual 解耦:不 import textual 不 import 业务模块,
    可单元测试(Stub CommandContext),未来可被 CLI / HTTP 前端复用

- **Tab 实时补全**
  - 每次按键调 `TabCompleter.candidates()` — 单匹配直接补,多匹配返回列表,
    hidden 命令不参与
  - 空输入 + Tab 列全部 10 个命令(给用户 discover)
  - 已进 args 区(输入包含空格)Tab 不接管,留作字面 Tab 字符

- **`/plan` / `/do` 严格动词**
  - 任何 args 静默忽略,只切 `plan_mode` (True / False)
  - 这是与 v0.6 的关键差别:之前 `/plan foo bar` 会同时跑任务,
    现在只切模式,任务由用户单独发(`/do foo bar` 也已不再支持 — 任务是普通消息)

- **状态栏 mode marker**
  - `[DEFAULT]` / `[PLAN]` / `[STRICT]` / `[PERMISSIVE]` — 一眼看到当前 mode
  - 替换 v0.5-v0.6 状态栏的 `mode=plan / mode=full · perm_mode=default` 多段

- **`CommandsConfig.review_prompt`** — 配置项
  - `config.commands.review_prompt: str | None`,为 `None` 用 builtin 默认文本
  - 模板字符串含 `{since}` 占位符,运行时被 `/review <since>` 替换
  - 给高级用户覆盖默认 review prompt 的口子

### 修改能力

- `baozicode/tui/chat_screen.py` 删 16 命令 if/elif 树(`SLASH_COMMANDS` + `_handle_slash`)
- `baozicode/commands/__init__.py` 导出 `dispatch / build_builtin_defs / CommandRegistry / CommandContext`
- `BaoZiCodeApp._command_registry` 字段在 `__init__` 构造(空),
  ChatScreen.on_mount 时注入 10 个 handler 并 `freeze()`
- `BaoZiCodeApp._command_ctx` 字段由 ChatScreen 注入(`TextualCommandContext` 实现)
- `baozicode/config/schema.py` 新增 `CommandsConfig` 字段(`review_prompt`)
- 测试增量:+103(v0.8 705 → v0.9 808)

### 废弃/移除字段

- **`/exit /model /tools /mcp /stop /auto` 6 个旧命令**:
  - `/exit` → `Ctrl+C` 退出(App 上 `BINDINGS` 已有)
  - `/model` → 改 `config.yaml` 重启;banner 写明当前后端 + 模型
  - `/tools` → 合并进 `/status` (列出可用工具摘要)
  - `/mcp` → 启动时 stderr banner 输出 MCP server 状态;不再单独命令
  - `/stop` → `Ctrl+C` 在运行中 + v0.7 agent cancel hook
  - `/auto` → 等价 `/permission mode permissive`;L5 modal 内部行为不变
- **`/resume /new` 合并入 `/session`**:`/session` 现在是单一入口,弹 StartupSessionScreen
  让用户选"恢复某 sid" / "开新" / "取消"

### 升级路径

- **最小动作**:不动配置也能跑,`CommandsConfig` 默认 `review_prompt=None` 用 builtin 默认
- **推荐动作**:`/help` 查看 10 个内置命令;旧的 6 杂事命令全部有迁移路径
- **可选动作**:`config.commands.review_prompt` 覆盖默认 /review prompt,自定义审查模板

详见 [docs/migrations/v0.8-to-v0.9.md](docs/migrations/v0.8-to-v0.9.md)。

## [v0.8] — 2026-07

### 新增能力

- **三层项目指令文件** (`BaoZiCode.md`)
  - 三层加载:`~/.config/baozicode/BaoZiCode.md` (user_global, 最低) < `<项目>/.baozicode/BaoZiCode.md` (project_local) < `<项目>/BaoZiCode.md` (project_root, 最高)
  - 启动时按优先级拼接,高优先级排在前面让模型优先遵循
  - `@include <相对路径>` 引用其他 Markdown 片段(深度 ≤ 5 / 环路拦截 / 项目目录白名单)
  - 三层全无 → 静默 + banner 一行建议

- **JSONL 会话存档**
  - 每条 message 追加写到 `sessions/<YYYYMMDD-HHMMSS-xxxx>.jsonl`(中间加 flush + fsync)
  - 崩只会丢最后一行;恢复时坏行可跳过
  - session_id 格式从 v0.7 `uuid4` 32 字符改为 v0.8 `YYYYMMDD-HHMMSS-xxxx` 20 字符
    (4 字符随机 hex 防同秒撞车),启动时自动迁移旧 uuid 目录
  - 不单独维护 meta 文件,ID / 标题 / 消息数直接扫 JSONL 算
  - **resume 异常四件套**:坏行跳过 / orphan tool_call 截断 / token 超限自动压 / 间隔 > 8h 插 time_gap reminder
  - 30 天过期 session 启动时自动清理

- **双层自动记忆**
  - `user_dir` (跟人走) / `project_dir` (跟项目走) 两层物理隔离的笔记目录
  - 笔记 4 类 + 自动路由:`user-pref` / `correction` → user,`project` / `reference` → project
  - `MEMORY.md` 索引(200 行 / 25KB 上限)灌进 system prompt 顶部
    `## 长期记忆 (用户级)` / `## 长期记忆 (项目级)` 段
  - Agent 自然停下后**异步**调 LLM 抽取笔记(快照式并发,去重交给 LLM 判断)
  - 溢出三级分层状态机:`NORMAL` → `WARN` → `AUTO_COMPRESS`(回 NORMAL)→ `HUMAN_NEEDED`
  - 跨 session 删除别人写的笔记被拒绝(防止互踩);`update` 允许跨 session 追加 / 合并

- **CLI flag**:`--resume <SESSION_ID>` / `--new` / `--no-banner`
- **Slash 命令**:`/resume` (弹选择) / `/memory` (双层状态) / `/new` (确认后开新)
- **TUI 启动 session 选择器**:无 flag + 有历史 sessions → 启动时弹 `StartupSessionScreen`
  询问续哪个 / 开新 / 保持当前

### 修改能力

- `session_id` 格式:uuid4 32 字符 → YYYYMMDD-HHMMSS-xxxx 20 字符
- 启动顺序调整:`permissions → instructions → memory → sessions`
- 启动 banner 增三行摘要(指令 / 记忆 / 会话),`--no-banner` 可抑制
- `/status` 增量:`session_id` / `sessions(磁盘): N 个` / `memory.user: X 条` / `memory.project: Y 条`
- `ConversationManager` 透明接 `SessionArchiver`,每条 `add_*` 同步落盘
- `Agent.__init__` 接受 `project_root`,自动从两层 memory 目录读 index 灌进 prompt

### 废弃字段

- `AppConfig.memory_path` —— 单文件长效记忆字段,改为 `MemoryConfig.user_dir` +
  `MemoryConfig.project_dir` 双层目录。v0.9 移除。
  - 兼容行为:文件存在 + 新双层目录都空 → fallback 当 user 级索引读,banner 打印一行 WARN
  - 启动时显式检测:用户改了 `memory_path` 默认值 + 文件存在 → stderr `WARN: memory_path is deprecated, ...`

### 升级路径

- 最小动作:不动配置也能跑,所有新机制有 `enabled: true` 默认值
- 推荐动作:创建 `<项目>/BaoZiCode.md`,把项目规范 / 注意事项写进去
- 可选动作:迁移旧 `memory.md` 到新双层目录(见 `docs/migrations/v0.7-to-v0.8.md` §四)

详见 [docs/migrations/v0.7-to-v0.8.md](docs/migrations/v0.7-to-v0.8.md)。

## [v0.7] — 2026-06

### 新增能力

- **上下文压缩**:两层 token 预算压缩
  - Layer 1(offload):单 block > 8 KB 或单 message > 20 KB → 写盘 + preview
  - Layer 2(摘要):逼近 `context_window - 13K`(自动)或 `- 3K`(手动)→ 调 LLM 生成 6 段摘要
  - `.baozicode/context/` 自动加 `.gitignore`
  - 摘要 prompt 显式禁止调工具,先写 `---ANALYSIS---` 草稿再写 `---SUMMARY---` 正文
  - 熔断:连续 3 次失败 → `StopReason.COMPACTION_FAILED`
- **`/compact` 手动触发**:Agent 空闲直接跑;运行中通过 `agent.request_compact()` 在下个迭代顶部生效
- **`/clear` + `on_unmount`**:清空 `.baozicode/context/<session>/` 目录
