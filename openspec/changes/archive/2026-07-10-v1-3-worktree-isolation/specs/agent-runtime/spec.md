# agent-runtime Specification — v1.3 ADDED Requirements

> This is the v1.3 delta on top of v1.2 agent-runtime capability.
> Existing v1.2 behavior (`SubAgentRuntime.spawn` 4-step state
> isolation, `TaskFilter` 4 layers, definition vs fork BuiltPrompt
> divergence) is **unchanged**. New behavior is added below.

## ADDED Requirements

### Requirement: AgentFrontmatter.isolation field (NEW)

`AgentFrontmatter` MUST expose a new field
`isolation: Literal["worktree"] | None = None` in v1.3:

- 合法值仅 `"worktree"`;Pydantic MUST 严格 enum 校验(拼错如
  `"Worktree"` 直接报)
- 缺省 `None` = 不隔离(sub-Agent 走 v1.2 路径,**零变化**)
- 显式 `"worktree"` = 强制走 worktree 路径(
  `SubAgentRuntime.spawn` 必须调 `WorktreeManager.create`)

#### Scenario: Accept legal value
- **WHEN** AGENT.md 含 `isolation: worktree`
- **THEN** `AgentFrontmatter.isolation == "worktree"` + 走
  WorktreeManager.create 路径

#### Scenario: Default None preserved
- **WHEN** AGENT.md 没写 `isolation` 字段
- **THEN** `AgentFrontmatter.isolation is None` — v1.2 路径不变

#### Scenario: Pydantic rejects bad value
- **WHEN** AGENT.md 含 `isolation: Worktree`(大写)
- **THEN** `parse_agent` 抛 `ValueError` 包含
  `unexpected value; permitted: 'worktree'`

### Requirement: SubAgentRuntime.spawn isolation branch (NEW)

`SubAgentRuntime.spawn` MUST 在调 `ToolFilter` + 构造 Agent 实例之
前,检测 `role_def.frontmatter.isolation`:

- `None` → v1.2 路径(零变化)
- `"worktree"` → 进入 isolation 路径:
  1. 调 `WorktreeManager.create(name=task_id)` 拿 `WorktreeSpec`
  2. `effective_project_root = WorktreeSpec.path`
  3. `MergedPermissions(real_root=effective_project_root)`(覆盖主)
  4. `Agent.__init__(project_root=effective_project_root)`
  5. `sub_agent._worktree_state = "active"`

#### Scenario: Definition + isolation happy path
- **WHEN** `spawn(type="definition", role_def=with isolation=
  "worktree", prompt="...")`
- **THEN** 调 `WorktreeManager.create(task_id)` + 创建 sub-Agent
  with `project_root=<worktree_path>` + state="active"

#### Scenario: Type="fork" + isolation rejected
- **WHEN** `spawn(type="fork", role_def=with isolation=
  "worktree")`
- **THEN** 抛 `ValueError("fork mode + isolation=worktree 互
  斥;worktree 强制 definition 模式")`;**不**调
  `WorktreeManager.create`;**不**改 `_tasks` dict

#### Scenario: Type="fork" + isolation=None unchanged
- **WHEN** `spawn(type="fork", role_def=with isolation=None)`
- **THEN** v1.2 fork 路径(共享 `parent_agent._prompt` + byte-
  identical BuiltPrompt);WorktreeManager 零介入

### Requirement: effective_project_root propagates to PathSandbox (NEW)

当 sub-Agent 是 worktree 隔离时,`MergedPermissions.real_root` MUST 是
worktree 路径(`WorktreeSpec.path`),**不是**主 repo
`project_root`。`PathSandbox` 构造时拿这个 `real_root`,所有文件工具
(Read/Write/Edit/Grep/Glob/Bash)的 `path` 校验基线 = worktree 路径。

#### Scenario: Read inside worktree allowed
- **WHEN** sub-Agent 在 worktree 内调
  `Read(file_path=<worktree>/src/foo.py)`
- **THEN** `PathSandbox.check(call)` 判定 sandbox 命中,
  `is_error=False`,文件读到

#### Scenario: Escape attempt blocked
- **WHEN** sub-Agent 调 `Read(file_path=<setup_dir>/src/foo.py)`
  (尝试从 worktree 走绝对路径读主 repo 的文件)
- **THEN** `PathSandbox.real_root = <worktree>`,绝对路径
  `<setup_dir>` 不在 worktree 内 → 拒,`is_error=True`

#### Scenario: Bash command escape blocked
- **WHEN** sub-Agent 调
  `Bash(command="cat /absolute/setup_dir/foo.txt")`
- **THEN** `PathSandbox._check_bash` 抽到路径 `/absolute/...`,
  判定 sandbox 拒,`is_error=True`

### Requirement: BuiltPrompt env_info.cwd uses effective_root (NEW)

当 sub-Agent 是 worktree 隔离时,`_build_definition_prompt` MUST 把
`env_info` 段里的 cwd 渲染成 `effective_project_root`(worktree 路
径),**不是**主 repo `project_root`:

- definition + worktree → env_info.cwd = `<setup_dir>/.worktrees/<name>/`
- fork 模式(无 worktree)→ env_info.cwd = main project_root(v1.2 行为)
- v0.4 BuiltPrompt 因 cwd 不同而 byte-identical 性质分裂(
  无 fork cache 优化被牺牲;v1.3 接受的取舍)

#### Scenario: definition + worktree prompt has worktree cwd
- **WHEN** `spawn(type="definition", role_def=with isolation=
  "worktree")` 成功
- **THEN** `sub_agent._prompt` 的 env_info 段含
  `cwd: <worktree_path>`(**不是** main project_root)

#### Scenario: fork + no isolation prompt has main cwd
- **WHEN** `spawn(type="fork", role_def=with isolation=None)`
- **THEN** `sub_agent._prompt.env_info.cwd == main project_root`;
  因 fork 模式跟主 Agent 共享 BuiltPrompt 对象,**同一份** cwd

#### Scenario: cache key naturally splits
- **WHEN** AnthropicBackend 调 `cache_breakpoints` 算 cache key
  + system prompt 含不同 cwd
- **THEN** 主 Agent cache key 跟 worktree sub-Agent cache key 不
  同;`UsageStats.cache_read_tokens` 反映这次主 Agent cache 命中,
  worktree sub-Agent **不**命中

## MODIFIED Requirements

无。

## REMOVED Requirements

无。
