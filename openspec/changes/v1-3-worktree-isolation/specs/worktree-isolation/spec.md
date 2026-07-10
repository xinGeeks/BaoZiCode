# worktree-isolation Specification (v1.3)

## Purpose

sub-Agent 通过 git worktree 物理隔离文件系统的子能力。当一个 sub-Agent
的 frontmatter 声明 `isolation: worktree` 时,v1.3 为它在主 repo 内
创建一个独立的 git worktree 目录(独立分支、独立 working tree),让该
sub-Agent 的所有文件操作物理落在该 worktree 内,**既不被主 Agent 改动
打扰,也不污染主 Agent 的工作区**。

worktree 隔离包含 5 块:

- 路径名校验(防 LLM 输入触发路径遍历)
- 完整生命周期(create / enter / exit / remove,含 fast-path 恢复)
- 环境初始化(配置驱动的链接、复制、git hooks、gitignore)
- 退出保护(未提交 / 未推送 commit 拒绝删除)
- 后台清理(按三层过滤自动清理过期目录)

模块名:`baozicode/worktree/`。与 `baozicode/agents/` 平级但正交:
worktree 是"为 isolated sub-Agent 提供执行环境的文件系统抽象",
agents 是"sub-Agent 角色 + 调度 + 工具过滤"。

## ADDED Requirements

### Requirement: WorktreePathValidator

`WorktreePathValidator` MUST 是纯函数 `validate(name: str) -> None`,
**无 IO、无副作用**,只做字符集 + 嵌套结构校验。

合法名定义:

- 字符集:`[a-zA-Z0-9_./-]`
- 长度:1–64 字符
- 允许 `/` 做嵌套(如 `phase1/api-designer`)
- 拒绝首尾 `/`
- 拒绝 `.` 段和 `..` 段(包括嵌套中间的)
- 拒绝空字符串

#### Scenario: Accepted names
- **WHEN** `validate("api-designer")` / `validate("wt_001")` /
  `validate("phase1/api-designer")` / `validate("a/b/c")`
- **THEN** 不抛异常

#### Scenario: Reject `..` traversal
- **WHEN** `validate("../../../etc/passwd")` / `validate("foo/../bar")`
- **THEN** 抛 `PathValidationError` 消息包含 "拒绝 . 或 .. 段"

#### Scenario: Reject length overflow
- **WHEN** `validate("a" * 65)` / `validate("")`
- **THEN** 抛 `PathValidationError` 消息包含 "长度越界"

#### Scenario: Reject leading/trailing `/`
- **WHEN** `validate("/foo")` / `validate("foo/")`
- **THEN** 抛 `PathValidationError` 消息包含 "不允许开头或结尾的 /"

#### Scenario: Reject special characters
- **WHEN** `validate("foo bar")` / `validate("foo;bar")` /
  `validate("foo*")` / `validate("foo\$bar")`
- **THEN** 抛 `PathValidationError` 消息包含 "字符集超出"

#### Scenario: Reject `./` prefix
- **WHEN** `validate("./name")` / `validate(".foo")`
- **THEN** 抛 `PathValidationError`

### Requirement: WorktreeSpec + WorktreeState

`WorktreeSpec` MUST 是 frozen dataclass,包含:

- `name: str` — 用户给出的 sub-agent 名(经过 validate)
- `path: Path` — `<setup_dir>/.worktrees/<name>/` 的绝对路径
- `branch: str` — git 分支名,格式 `wt/<name>`
- `state: WorktreeState` —— 状态机:`creating` → `active` →
  `detached` → `removed`

`WorktreeState` 是 `Literal["creating", "active", "detached", "stale",
"removed"]`;**`active` 表示主任务或后台清理正在用**,**`detached`
表示有未提交 / 未推送 commit 需手工处理**,**`stale` 表示超时未被任
务关联**,**`removed` 表示清理完成**。

#### Scenario: Default state on create
- **WHEN** `WorktreeManager.create(name)` 成功返回
- **THEN** `WorktreeSpec.state == "active"`

#### Scenario: Transition to detached
- **WHEN** `exit(name, force=False)` 检测到 `git status --porcelain`
  非空或 `git log @{u}..HEAD` 非空
- **THEN** `WorktreeSpec.state` 转为 `detached` 且路径**保留**

#### Scenario: Transition to removed
- **WHEN** `exit(name, force=False)` 两项检查都通过
- **THEN** `WorktreeSpec.state` 转为 `removed` 且路径被
  `git worktree remove` 删除

### Requirement: WorktreeManager.create with Fast-Path

`WorktreeManager.create(name)` MUST 异步工作并分两路:

1. **Fast-path(目录已存在健康时)**:`<setup_dir>/.worktrees/<name>/`
   是目录 + 含 `.git` file + `git worktree list --porcelain` 能看到
   + `git status --porcelain` 空 → 直接返回 `WorktreeSpec(state=
   "active")`,**不调 `git worktree add`**
2. **Normal-path**:`git worktree add -b wt/<name>
   .worktrees/<name>/ <start-point>` 默认 start-point = `HEAD`;
   失败 → 清理半成品 + 抛 `WorktreeCreationFailed`

`WorktreePathValidator.validate(name)` 必须在 `create` 第一行调,
失败抛 `PathValidationError`(LLM 输入直传)。

#### Scenario: Happy path fresh create
- **WHEN** `<setup_dir>` 是 git repo + name `"api-designer"` 合
  法 + `.worktrees/` 不存在该目录
- **THEN** 跑 `git worktree add -b wt/api-designer
  .worktrees/api-designer/` + 调 `WorktreeInitializer.run(...)` +
  返回 `WorktreeSpec(state="active", path=<abs>)`

#### Scenario: Fast-path healthy existing
- **WHEN** `.worktrees/api-designer/` 已存在 + 含 `.git` file +
  `git worktree list --porcelain` 含 `worktree
  .worktrees/api-designer/` + `git status --porcelain` 为空
- **THEN** **不**调 `git worktree add`,直接返回
  `WorktreeSpec(state="active")` + 不重复调
  `WorktreeInitializer`(幂等)

#### Scenario: Fast-path fails on dirty
- **WHEN** `.worktrees/api-designer/` 已存在 + `.git` file 在 +
  `git worktree list --porcelain` 含 + `git status --porcelain` 非空
- **THEN** **不**走 fast-path,转 normal-path;若
  `git worktree add` 又失败(目录不空)抛
  `WorktreeExistsDirtyError`

#### Scenario: Reject not in git repo
- **WHEN** `<setup_dir>` 不是 git repo
- **THEN** `WorktreeManager.__init__` 在构造期就抛
  `WorktreeNotInRepoError`(避免延迟失败)

### Requirement: WorktreeInitializer 4-step setup

`WorktreeInitializer.run(worktree_path, setup_dir, config)` MUST 按
顺序跑 4 步,**任一步失败只能 warn 不能阻断**(LLM 工作流不中断):

1. **link** —— 对 `config.worktree.link_paths`(默认
   `[".venv", "node_modules", ".cargo"]`)每项在 `worktree_path`
   下创建**指向 setup_dir 同名路径的 symlink**;
   Windows 默认 `os.symlink` 失败时 fallback `cmd /c mklink /J`;**跨
   盘符时 warn + 跳过**(junction 不能跨盘符)
2. **copy** —— 对 `config.worktree.copy_paths`(默认
   `[".baozicode/BaoZiCode.md", ".env", "config.yaml",
   ".claude/"]`)每项用 `shutil.copytree(dirs_exist_ok=True)`
   复制到 worktree 内;失败 warn + skip
3. **hooks** —— `git -C worktree_path config core.hooksPath
   <relative ../_hooks/>`;自动建共享 `_hooks/` 空目录(在
   `.worktrees/` 下,被 gitignore 已经排除)
4. **gitignore** —— 读 `<setup_dir>/.gitignore`,缺
   `^\.worktrees/?$` 行则 append;**幂等**(重复跑不写第二遍)

#### Scenario: link creates symlinks
- **WHEN** `link_paths=[".venv"]` + `<setup_dir>/.venv` 存在
- **THEN** `<worktree>/.venv` 是指向 setup_dir/.venv 的 symlink,
  `pathlib.Path.is_symlink()` 返回 True

#### Scenario: link cross-drive graceful
- **WHEN** Windows 上 `<setup_dir>` 在 C: 盘,worktree 在 D: 盘
  + `link_paths=[".venv"]`
- **THEN** `.venv` 没被链,initializer 写一行 warning 到 stderr/
  log,但**不抛**

#### Scenario: copy idempotent
- **WHEN** `<setup_dir>/.env` 存在 + worktree 已存在
  `.env`(从上次初始化遗留)
- **THEN** `copy` 覆盖,**不**因 `FileExistsError` 抛

#### Scenario: hooks path set
- **WHEN** initializer 跑后
- **THEN** `git -C <worktree> config core.hooksPath` 输出
  相对路径 `../_hooks/`(**不是绝对路径**)

#### Scenario: gitignore idempotent
- **WHEN** `.gitignore` 已含行 `.worktrees/` + initializer 又跑
- **THEN** `\.worktrees/?$` 正则匹配原有行,**不** append 第二行

### Requirement: WorktreeManager.exit force decision

`exit(name, *, force=False)` MUST 按此决策树:

```
out_dir = setup_dir/.worktrees/<name>
if force == True:
    git worktree remove --force <out_dir>
    git branch -D wt/<name>
    return ExitResult(Removed, reason="force")
    # 注意:wt/<name> 可能在分支状态机上有冲突,
    # -D 强制删

# 检测 1:未提交修改
proc = git status --porcelain --untracked-files=all
if proc.stdout.strip():
    return ExitResult(Detached, reason="uncommitted_changes")
    # 不删,留 detached 给用户

# 检测 2:未推送 commit(仅当 branch 有 upstream)
if git rev-parse --verify --quiet @{u}:
    if git log @{u}..HEAD.not.empty:
        return ExitResult(Detached, reason="unpushed_commits")

# 都干净:
git worktree remove <out_dir>
git branch -d wt/<name>  # 注意是 -d 不是 -D
return ExitResult(Removed, reason="clean")
```

#### Scenario: Clean exit removes
- **WHEN** worktree 是 active + `git status --porcelain` 空 +
  branch 无 upstream(或 upstream 已合并)
- **THEN** 跑 `git worktree remove` + `git branch -d` + 状态变
  `removed`

#### Scenario: Uncommitted changes preserve
- **WHEN** worktree 内有 `git status` 不报的脏工作区(untracked 或
  modified 但未 staged)
- **THEN** `git worktree remove` **不**跑,状态变 `detached`,
  路径**保留** + `ExitResult(reason="uncommitted_changes")`

#### Scenario: Unpushed commits preserve
- **WHEN** branch 有 upstream + `git log @{u}..HEAD` 非空
- **THEN** `git worktree remove` **不**跑,状态变 `detached`,
  路径**保留** + `ExitResult(reason="unpushed_commits")`

#### Scenario: Force always removes
- **WHEN** `exit(name, force=True)`
- **THEN** 无视所有检测,跑 `git worktree remove --force` +
  `git branch -D`,状态变 `removed`

#### Scenario: Branch no upstream
- **WHEN** branch `wt/<name>` 没有 upstream(`@{u}` 不存在)
- **THEN** 检测 2 自动跳过,只走检测 1;**任何 commit** 都视为
  "无需推送"(新分支普遍无 upstream)

### Requirement: WorktreeCleanupDaemon three-layer filter

`WorktreeCleanupDaemon.run_once()` MUST 按三层过滤扫所有现存
`.worktrees/<name>/` 目录,返回 `list[CleanupAction]` 其中每项是
`SKIPPED` / `BLOCKED` / `CLEANED`:

1. **第 1 层 — 状态过滤**:`WorktreeSpec.state in {"detached",
   "stale"}` 才考虑清理;`active` / `creating` 一律 skip
2. **第 2 层 — 时间过滤**:`last_access > now - retention_minutes`
   的跳过(默认 60 分钟)
3. **第 3 层 — 活跃任务关联**:`name` 在
   `SubAgentManager._tasks with state in {"running", "pending"}`
   一律 skip(防止清理中任务 worktree)

任一层 skip → 标记 `CleanupAction.SKIPPED` + 原因;**全部通过 →
调 `WorktreeManager.exit(name, force=False)`**,其
`ExitResult` 决定 `CLEANED`(removed)还是 `BLOCKED`(
detached + dirty)。

#### Scenario: Active worktree never cleaned
- **WHEN** worktree `api-designer` state = `active` + 有 task 关
  联(`SubAgentManager._tasks["wt-api-designer"].state == "running"`)
- **THEN** 第 1 层拒绝(status 不在 detached/stale),daemon
  返回 `SKIPPED(reason="active")` + **`run_once` 不调 exit**

#### Scenario: Stale + outside retention cleaned
- **WHEN** worktree `old-agent-1` state = `detached` + last access
  70 分钟前(default retention_minutes=60)
- **THEN** 第 1 层过(state detached)+ 第 2 层过(70 > 60)+ 第
  3 层过(无 task)→ 调 exit;exit 检测到 clean → 删除 → 返回
  `CLEANED(reason="clean")`

#### Scenario: Detached + dirty blocked
- **WHEN** worktree state = `detached` + last access 90 分钟前
  + `git status --porcelain` 非空
- **THEN** 前 2 层过 + 第 3 层过 → 调 `exit(force=False)` → dirty
  → 返回 `BLOCKED(reason="uncommitted_changes")`(**不删**,保留)

#### Scenario: Active task blocks cleanup
- **WHEN** worktree state = `detached` + 时间过 + name 在
  `SubAgentManager._tasks[...].state == "running"`
- **THEN** 第 3 层拒绝,daemon 返回 `SKIPPED(reason="task_active")`

### Requirement: isolation frontmatter field (NEW)

`AgentFrontmatter.isolation: Literal["worktree"] | None = None` MUST
在 v1.3 加入:

- 合法值:**仅 `"worktree"`**(拼错如 `"Worktree"` Pydantic 直接报)
- 默认 None:不隔离,sub-Agent 跟主 Agent 共享 `project_root`(v1.2
  行为,0 变化)
- 写 `"worktree"`:spawn 时强制走 `WorktreeManager.create` 路径
- `nesting-depth: 0` 的 builtin sub-agent **不**自动开(v1.3 显式声
  明才开)

#### Scenario: Pydantic rejects bad value
- **WHEN** YAML frontmatter 含 `isolation: Worktree`(大写)
- **THEN** `parse_agent` 抛 `ValueError` 包含
  `unexpected value; permitted: 'worktree'`

#### Scenario: Default None means no isolation
- **WHEN** AGENT.md 没写 `isolation` 字段
- **THEN** `AgentFrontmatter.isolation is None` + v1.2 行为完全等
  价

#### Scenario: Builtin agents keep v1.2 behavior
- **WHEN** `agents/builtin/explorer/AGENT.md` 和
  `agents/builtin/summarizer/AGENT.md` frontmatter **不**修改
- **THEN** 这 2 个 builtin 跟 v1.2 行为完全一致(sub-Agent 共享主
  Agent cwd)

### Requirement: fork + isolation=worktree mutual exclusion

`SubAgentRuntime.spawn` MUST 抛 `ValueError("fork mode + isolation=worktree 互斥")`
当调用 `spawn(type="fork", role_def=with isolation="worktree")` 时:

- 错误信息清晰指出冲突的两个字段名
- LLM 看到立即知道修改方法(改 `type=definition` 或删
  `isolation`)
- fail-fast 在 spawn 早期,**不**半创建

#### Scenario: definition + worktree happy
- **WHEN** `spawn(type="definition", role_def=with
  isolation="worktree", prompt="...")`
- **THEN** 不抛 ValueError;调 `WorktreeManager.create` +
  `effective_project_root` 设为 worktree 路径 +
  sub-Agent 写出文件落在 worktree 内

#### Scenario: fork + worktree rejected
- **WHEN** `spawn(type="fork", role_def=with
  isolation="worktree")`
- **THEN** 抛 `ValueError("fork mode + isolation=worktree 互
  斥;worktree 强制 definition 模式")`;**不**调
  `WorktreeManager.create`(no side effect);**不**改
  `_tasks` 状态

#### Scenario: fork + no isolation unchanged
- **WHEN** `spawn(type="fork", role_def=with isolation=None)` 或
  isolation 缺省
- **THEN** 跟 v1.2 完全一致(parent_agent._prompt 共享 +
  byte-identical BuiltPrompt)—— 无 worktree 介入

### Requirement: effective_project_root flows through Agent

当 `SubAgentRuntime.spawn` 处理 `isolation="worktree"` 时,MUST 满
足:

- `WorktreeSpec.path` 作为 `Agent.__init__` 的 `project_root` 参数传
- sub-Agent 实例的 `PathSandbox.real_root` MUST 指向 worktree(经
  `MergedPermissions(real_root=...)` 在 runtime 里设)
- `sub_agent._worktree_state` 字段 MUST 等于 `"active"`(供
  `_run_subagent` 终态决策读)

#### Scenario: PathSandbox checks inside worktree
- **WHEN** sub-Agent 在 worktree 内调
  `Bash(command="cat ../foo.txt")`(尝试跳出 worktree)
- **THEN** `PathSandbox.check` 判定 escape,`is_error=True`,**主
  worktree 内文件不**被读到

#### Scenario: Bypass attempt blocked
- **WHEN** sub-Agent 调
  `Bash(command="cat /absolute/setup_dir/foo.txt")`
- **THEN** `PathSandbox.real_root = worktree_path`,绝对路径 escape
  → 拒

### Requirement: cache naturally splits on cwd (no new mechanism)

v1.3 MUST NOT 引入新的缓存层。不同 worktree cwd → 不同
BuiltPrompt.env_info.cwd → byte-identical 性质被打破 → Anthropic
cache miss。这是有意识的取舍(失去 fork cache 优化换文件隔离安
全)。

#### Scenario: definition + worktree misses parent cache
- **WHEN** `spawn(type="definition", role_def=with
  isolation="worktree")`
- **THEN** BuiltPrompt 跟主 Agent BuiltPrompt **不**共享对象
  (没有 `parent_agent._prompt` 用);cache key 因 cwd 不同分裂

#### Scenario: fork + no isolation still shares
- **WHEN** `spawn(type="fork", role_def=with isolation=None)`
- **THEN** BuiltPrompt 跟主 Agent 同对象(走 v1.2 fork 优化)—— v1.3
  对 fork 没破坏

#### Scenario: Instruction + memory cross-share
- **WHEN** worktree sub-Agent 跑时,v0.8 instructions loader 读
  `<project_root>/.baozicode/BaoZiCode.md`
- **THEN** 由于 `<project_root>` 是 setup_dir(不是 worktree_path),
  sub-Agent 拿到**主**repo 的指令,自然跟主 Agent 一致;同样
  v0.8 双层 memory 也按 project_root 取
