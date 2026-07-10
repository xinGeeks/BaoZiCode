# v1.3 Worktree Isolation — Proposal

## Why

v1.2 的 SubAgent 系统让主 Agent 可以动态派 sub-Agent 干活,但有一个根本
问题:**sub-Agent 和主 Agent 共用同一个 `project_root`**(sub-Agent 看
到的 BashSession、PathSandbox、文件系统全部跟主 Agent 同根)。

这意味着:
- 主 Agent 跟 sub-Agent **互相覆盖文件**——主 Agent 写代码时被 sub-Agent
  读 / 写干扰,反之亦然。LLM 不知道谁先谁后
- **没法并发安全**——v1.2 物理禁嵌套,但**没有禁并发**。两个并行 sub-Agent
  同时 `Bash(git pull)` + `Edit(src/foo.py)` 一定互相破坏
- `fork` 模式共享 `_prompt` 对象走 cache 优化,这个机制在 v1.2 已经建好,
  **但跟 worktree 隔离天然冲突**——worktree sub-Agent 的 cwd 跟主 Agent
  不同,cwd 会进 `env_info` 段,BuiltPrompt byte-identical 性质被打破,
  cache miss

更深层的问题:**sub-Agent 应该是隔离的执行环境**,而不是共享文件系统
视角下的"另一个 LLM"。v1.2 解决了"sub-Agent 是什么"(角色化 + 状态隔
离),v1.3 解决"sub-Agent 在哪干活"(物理目录 + git 分支 + 缓存 key)。

v1.3 引入 **Worktree Isolation**:
- 每个 **声明 `isolation: worktree`** 的 sub-Agent 用一个独立
  `git worktree` 工作,目录在 **`<repo>/.worktrees/<safe-name>/`**,独立
  分支,跟主 Agent 物理隔离
- 主 Agent 可以放心派活给多个 worktree sub-Agent 并行干不冲突的子任务
- sub-Agent 的所有文件操作发生在自己的隔离目录里,**既不读主 Agent 的
  未提交改动,也不污染主 Agent 的工作区**
- LLM 缓存按绝对路径天然 key(worktree cwd ≠ 主 cwd → BuiltPrompt 不
  byte-identical),无需手动清缓存

技术约束(已锁的决策):

- **用 Git 自带 worktree 机制**做隔离——不是新建 git repo,而是同一个
  repo 的多个 working tree,共享对象库
- **目录名严格安全校验**——`^[a-zA-Z0-9_./-]+$`,长度 1–64,拒绝 `.`
  和 `..` 段,允许 `/` 做嵌套命名(如 `phase1/api-designer`)
- **完整生命周期** —— `create` (含 fast-path 恢复) / `enter` / `exit` /
  `remove`
- **快速恢复**——目录已存在 + `.git` file 在 + `git worktree list` 能
  看到 + 未 dirty → 直接当健康 worktree 接管,不再调 `git worktree add`
- **环境初始化** —— 入口 `WorktreeInitializer.run(setup_dir)`:
  - 软链配置白名单 `subagents.worktree.link_paths`(默认
    `[".venv", "node_modules"]`),用 `os.symlink`,Windows 上 fallback
    到 junction
  - 复制本地配置 `.baozicode/BaoZiCode.md` / `.env` / `config.yaml` /
    `.claude/`
  - 改子目录的 `core.hooksPath` 到 `.worktrees/_hooks/`
  - 把 `.worktrees/` 加进 `<repo>/.gitignore`
- **不 chdir** —— 主 Agent 进程 cwd 永不变;worktree 路径通过新的
  `cwd` 参数显式传到每个工具调用
- **缓存按绝对路径 key** —— 不引入新缓存层;LLM BuiltPrompt 因 `env_info`
  段里的 cwd 不同而自然分裂,跟主 Agent 共享 cache 的解释性更差但安全
- **sub-Agent 声明** —— `AgentFrontmatter` 加 `isolation: Literal["worktree"]`
  字段;**默认不写 = 不开**;定义明确,跟 `tools-deny` 等其他字段平级
- **fork 模式拒绝 worktree** —— spawn 阶段直接报错
  (`fork mode + isolation=worktree 互斥;worktree 跟 byte-identical
  BuiltPrompt 冲突`),语义跟 v1.2 物理禁嵌套一致
- **退出保护** —— 移除 worktree 前 `git status --porcelain`
  (未提交) + `git log @{u}..` (未推送) 二者皆为空才允许删;否则提示用
  户接管
- **后台清理** —— `WorktreeCleanupDaemon` (asyncio task) 定期扫:
  三层过滤(1. worktree 是否已 detached;2. 最后 access time > 阈值;
  3. task_id 不在 SubAgentManager 活跃表)
- **不改 Bash 工具的契约下传 cwd** —— Bash 新增 optional `cwd: str` 参
  数,默认走既有的 module-level `_sessions` (兼容 v1.2),显式传则用临时
  cwd 跑(不污染 _sessions.cwd),执行完恢复

## What Changes

新增 1 个能力模块 + 改 4 处现有契约:

- **新增 `baozicode/worktree/`** — 顶层包,跟 `agents/` 平级;5 个子模块:
  - `schema.py` —— `WorktreePathValidator` (字符集 + 嵌套 + `.`/`..` 段
    拒绝) + `WorktreeSpec` (路径 + 分支名 + 创建元数据) + `WorktreeState`
    状态机
  - `manager.py` —— `WorktreeManager.create(name) → WorktreeSpec`
    (fast-path 恢复 + 真正 `git worktree add`) / `enter(name)` /
    `exit(name, force=False)` / `remove(name, policy)`;`create` 返回
    `Result[WorktreeSpec, WorktreeError]` 风格
  - `initializer.py` —— `WorktreeInitializer.run(worktree_path,
    setup_dir, config)` 串联链接 + 复制 + hooks + gitignore
  - `cleanup.py` —— `WorktreeCleanupDaemon` (asyncio task, 默认 60s
    一扫)
  - `builtin/` —— 不在 v1.3 加新样板 builtin agent(隔离默认关)
- **`baozicode/agents/schema.py`** —— `AgentFrontmatter` 加
  `isolation: Literal["worktree"] | None = None` 字段,Pydantic 严格校验
  enum
- **`baozicode/agents/runtime.py`** —— `SubAgentRuntime.spawn` 加
  `isolation` 分支:
  - 检测 `role_def.frontmatter.isolation == "worktree"`
  - 是 → 调 `WorktreeManager.create(name=task_id or
    role_def.name+short-id)` 拿 worktree 路径
  - 把 worktree 路径作为 `effective_project_root` 传给 `Agent.__init__`
    的 `project_root` 参数(覆盖默认值)
  - 把 worktree 路径塞进 sub-Agent BuiltPrompt 的 `env_info.cwd` 段
  - 是 `type="fork"` → `ValueError("fork mode + isolation=worktree
    互斥;worktree 强制 definition 模式")`
- **`baozicode/agents/manager.py`** —— `SubAgentManager._run_subagent`
  在 sub-Agent 跑完 `done` / `failed` / `canceled` 后:
  - 看 `task.agent._worktree_state == "active"` 决定是否调
    `WorktreeManager.exit(name, force=False)`
  - 默认行为:有未提交 / 未推送 → `force=False` 保留 worktree(`state` 标
    `detached`);无变更 → 干净清理
  - 新增 `force=True` 选项(TUI 手动 cancel 时用)
- **`baozicode/tools/bash.py`** —— `Bash.execute` 加 `cwd` 参数:
  - 不传 → 走既有 `_sessions` dict + `plan_cd`,**行为完全等价 v1.2**
  - 传了 → 用 `cwd` 作为执行 cwd(不污染 _sessions),`subprocess.run`
    显式 `cwd=...`;执行完不更 _sessions.cwd
- **`baozicode/config/schema.py`** —— `SubAgentsConfig` 加
  `worktree: WorktreeConfig | None` 子配置;`WorktreeConfig` 字段:
  - `enabled: bool = True`(冗余字段;由 `isolation: worktree` 决定
    实际启用,这里是"总开关")
  - `link_paths: list[str] = [".venv", "node_modules"]` (白名单)
  - `copy_paths: list[str] = [".baozicode/BaoZiCode.md", ".env",
    "config.yaml", ".claude/"]`
  - `retention_minutes: int = 60` (后台清理阈值)
  - `daemon_interval_seconds: int = 60`
  - `max_concurrent_worktrees: int = 5`(硬上限,跟 `max_concurrent` 平
    行,防 worktree 数量爆炸)

## Capabilities

### New Capabilities

- **`worktree-isolation`** —— sub-Agent 通过 git worktree 物理隔离文件
  系统:路径名校验、`create`(含 fast-path 恢复)/`enter`/`exit`/
  `remove` 生命周期、配置驱动的环境初始化(链接 / 复制 / hooks /
  gitignore)、退出保护 + 后台清理

### Modified Capabilities

- **`agent-runtime`** —— `SubAgentRuntime.spawn` 新增 `isolation` 分支,
  `effective_project_root` 切到 worktree 路径;`fork` + `worktree` 互
  斥报错;`env_info.cwd` 段动态填 worktree 路径
- **`subagent-manager`** —— `_run_subagent` 终点调 `exit` 决策;新
  `task._worktree_state` 字段;TUI 可调 `force=True`
- **`path-sandbox`** —— 不改 PathSandbox 实现(real_root 本来就是构造时
  传的),但**改 dispatch 路径**:`SubAgentManager` 创 permission
  callback 时把 `effective_project_root` 传给 `MergedPermissions.real_
  root`;spec 加 1 个 requirement 描述 sub-Agent 场景
- **`tool-calling`** —— Bash 工具 schema 加 optional `cwd: str` 参数;
  spec 加 1 个 requirement 描述 absolute path 必须是 worktree 内子目录
- **`configuration`** —— `SubAgentsConfig.worktree` 子块 schema,默认
  全开;`config.example.yaml` 加 `subagents.worktree:` 段;spec 加 1 个
  requirement

## Impact

**代码**(`baozicode/`):

- `worktree/__init__.py`(新) —— 公开 API re-export
- `worktree/schema.py`(新) —— `WorktreePathValidator` +
  `WorktreeSpec` + `WorktreeState` + 错误枚举
  (`PathValidationError` / `WorktreeExistsDirtyError` / `WorktreeNotInRepoError`)
- `worktree/manager.py`(新) —— `WorktreeManager.create`(含 fast-path) /
  `enter` / `exit` / `remove`,所有 git 调用走 `asyncio.create_subprocess_exec`
  跑 `git worktree add/list/remove/--porcelain`
- `worktree/initializer.py`(新) —— `WorktreeInitializer.run` 串联 4
  步:link → copy → hooks → gitignore;Windows symlink fallback 用
  `mklink /J`
- `worktree/cleanup.py`(新) —— `WorktreeCleanupDaemon`(asyncio task),
  启动时挂 `BaoZiCodeApp._on_mount` 末尾,`_on_unmount` 取消
- `agents/schema.py`(改) —— `AgentFrontmatter` 加 `isolation` 字段
- `agents/runtime.py`(改) —— `SubAgentRuntime.spawn` 分支:
  `isolation == "worktree"` 调 WorktreeManager + 传 effective_project_root
- `agents/manager.py`(改) —— `_run_subagent` 终态 `exit` 决策
- `tools/bash.py`(改) —— 加 `cwd` optional 参数 + 临时 cwd 执行路径
- `permissions/sandbox.py`(不改)**:`PathSandbox(real_root)` 已支持外部传,
  只在 spec 加 requirement 描述 sub-Agent 路径回退到 worktree
- `config/schema.py`(改) —— `WorktreeConfig` Pydantic + `SubAgentsConfig.worktree`
- `app.py`(改) —— `_build_worktree_manager()` 单例;`_on_mount` 末尾
  start cleanup daemon;`_on_unmount` cancel daemon + remove all
  active worktrees (`force=True`)
- `tui/chat_screen.py`(改) —— sub-Agent 卡片加 worktree 路径行
  (`cwd: <repo>/.worktrees/<name>/`)

**测试**(`tests/test_worktree_v13.py`全新 + 散落追加):

- `worktree-isolation`:
  - `WorktreePathValidator`:~12 个 — 合法(纯名、嵌套、太长拒、空
    拒、`.` 拒、`..` 拒、`/../` 拒、特殊字符拒、`/` 起头拒、`/` 收
    尾拒、嵌套合法、`./name` 拒)
  - `WorktreeManager.create`:~8 个 — happy path / 目录已存在 fast-path /
    非 git repo 拒 / 嵌套 worktree 拒 / `_hooks/` 共享目录自动建 /
    `core.hooksPath` 自动 set / 分支名规范 / `git worktree add` 失败
    recovery(脏分支清理)
  - `WorktreeManager.exit`:`force=True` vs `force=False` 决策树(
    `git status` clean + `git log @{u}..` empty → 删;否则保)
  - `WorktreeInitializer`:~4 个 — link_paths 软链 / copy_paths 复制 /
    hooks 路径 set / gitignore 添加
  - `WorktreeCleanupDaemon`:~3 个 — 阈值过滤 / task_id 活跃关联拒绝
    / dry-run 标记
- `agent-runtime`(追加):~5 个 — `isolation: worktree` definition 模式
  happy / `isolation: worktree` + `type="fork"` 报错 / `isolation` 字段
  缺省默认关 / `effective_project_root` 传到 Agent /
  `env_info.cwd` 段含 worktree 路径
- `subagent-manager`(追加):~4 个 — sub-Agent 跑完无变更 → 删 / 有
  未提交改动 → 保(`detached`) / `force=True` 删除 dirty / TUI cancel
  路径正确
- `tool-calling`(追加):~3 个 — `cwd` 参数 happy path / 不传走旧
  `plan_cd` / cwd 越界(`!is_relative_to(real_root)`)拒
- `path-sandbox`(追加):~2 个 — sub-Agent 的 call 走 worktree real_root
  校验 / 主 Agent vs sub-Agent 沙箱不互相干扰
- `configuration`(追加):~2 个 — `subagents.worktree:` 缺省默认 /
  字段校验(链接名单不存在报错)

**文档**:

- `openspec/specs/worktree-isolation/spec.md`(新)—— 9–12 个
  Requirement(`WorktreePathValidator` / `WorktreeManager.create` /
  fast-path / `WorktreeInitializer` 4 步 / 退出保护 / 后台清理 / 三
  层过滤 / `isolation` 字段 / 路径安全)
- `openspec/specs/agent-runtime/spec.md`(改)—— 加 3 个 requirement
  (`isolation` 字段 / worktree + fork 互斥 / `effective_project_root`)
- `openspec/specs/subagent-manager/spec.md`(改)—— 加 2 个
  requirement(worktree state / `force` 决策)
- `openspec/specs/path-sandbox/spec.md`(改)—— 加 1 个 requirement
  (sub-Agent 沙箱回退)
- `openspec/specs/tool-calling/spec.md`(改)—— 加 1 个 requirement
  (Bash `cwd` 参数)
- `openspec/specs/configuration/spec.md`(改)—— 加 1 个 requirement
  (`subagents.worktree:` 块)
- `docs/migrations/v1.2-to-v1.3.md`(新)—— 迁移指南 + 缓存策略变化
  + Bash `cwd` 参数兼容
- `README.md` 加一段 `Worktree Isolation` 介绍 + 一个 `isolation: worktree`
  示例 AGENT.md
- `config.example.yaml` 加 `subagents.worktree:` 完整示例

**可能的 breaking change**(评估):

- v1.3 引入了 **`task` 工具调 `type="fork" + isolation: worktree`** 的
  错误路径;LLM 即便犯这个错也只是 spawn 失败,**不是 silent behavior 改
  变** — 兼容
- Bash `cwd` 是**新增 optional 参数**,LLM 旧 prompt 不传就走旧行为 —
  兼容
- `SubAgentsConfig` 新增 `worktree` 子块是**新增 optional 字段**,缺省走
  bootstrap 默认 — 兼容
- v1.2 builtin agent(`explorer` / `summarizer`)frontmatter 不变,
  `isolation` 默认 None = 不开 worktree — 兼容

## Risks

[R1] **worktree 跟 fork 模式 byte-identical BuiltPrompt 冲突** — Mitigation:
fork + worktree 在 spawn 阶段 fail-fast 报清楚原因;LLM 看到立即知道怎么改

[R2] **worktree 创建失败吞掉大量 git 状态**(分支创建一半失败) — Mitigation:
`WorktreeManager.create` 用 try/except + `git worktree prune` 兜底 + 把
分支和目录都清干净;失败返回 `WorktreeCreationFailed` enum 让上层决定

[R3] **环境初始化链接是 LLM 间接可写路径** — Mitigation:`link_paths` /
`copy_paths` 是**配置白名单**(不是 LLM 输入);任何 LLM 输出要走这些路
径必须通过 Spawn 前配置,LLM 不能 spawn 时 override

[R4] **退出保护"未推送 commit"误判** — Mitigation:用
`git log @{u}..HEAD`(branch 没 upstream 时 git 报 warning)检测前先
`git rev-parse --verify @{u}` 探一下;无上游 → 当成"无需推送"(但保留
worktree 给用户)

[R5] **后台清理不小心删掉活跃 task 的 worktree** — Mitigation:第三层过
滤 = `task_id not in SubAgentManager._tasks with state in
{"running", "pending"}`;**只能清理 `detached` 或更老的状态**

[R6] **Windows 软链 fallback 到 junction** — junction 不支持跨盘符;
Mitigation:跨盘符时 warn + 跳过 link(只 copy);不阻断

[R7] **`isolation` 字段误拼**(`isolation: Worktree`) — Pydantic
`Literal["worktree"]` 强校验,LLM 拼错 boot 时直接报错

[R8] **BuiltPrompt 因 cwd 不同分裂丢失 fork cache 优势** — 这是承认
的取舍:definition 模式 + worktree 优先于 fork cache 命中;fork + 同一
project_root 仍可共享

[R9] **多个 worktree 同时跑 git push/pop stash** — Mitiga
tion:配置白名单可能 link `.git` 目录(不能);initializer 显式拒绝链
`.git`;worktree sub-Agent 默认 `permission-mode: default`(用户可改
permissive)

[R10] **worktree 路径不在 `.gitignore` 导致被 commit** — Mitigation:
initializer 第 4 步 if `.gitignore` 缺 `.worktrees/` 行就插入(vs append);
幂等

## Migration Plan

无用户可见 breaking change。新功能全部默认开(配 `subagents.worktree:`
有合理 default),不开的用户只需不写 `isolation: worktree` in AGENT.md。

部署:
1. 合并 `v1-3-worktree-isolation` → main
2. bump version 1.2.0 → 1.3.0
3. release notes:
   - "新增 Worktree Isolation:sub-Agent 可声明 `isolation: worktree`
     物理隔离文件系统"
   - "Bash 工具支持 `cwd` 参数(显式执行目录,不污染 session 状态)"
   - "新增 `subagents.worktree:` 配置块:链接名单 / 复制名单 / 保留
     策略"

回滚:
- 单 commit revert
- v1.3 sub-Agent 默认 `isolation=None` (=不隔离),revert 后**默认行为
  完全等价 v1.2**
- 用户显式声明的 `isolation: worktree` AGENT.md revert 后 Pydantic
  校验报错,需要删字段(但 LLM 工作流会迅速 fail 出)
