# subagent-manager Specification

## Purpose
TBD - created by archiving change v1-3-worktree-isolation. Update Purpose after archive.
## Requirements
### Requirement: Worktree state on TaskInfo (NEW)

In v1.3, each `TaskInfo` MUST carry a `_worktree_state: WorktreeState |
None` field (default `None`):

- `None`:task 不涉及 worktree(v1.2 行为)
- `"active"`:task 跑在 active worktree 上;`exit()` 时按决策树决定
- `"detached"`:task 已跑完,worktree 因 dirty/unpushed 保留

#### Scenario: Default v1.2 task untouched
- **WHEN** sub-Agent frontmatter `isolation=None` 或缺省
- **THEN** `task._worktree_state is None` — 等价 v1.2

#### Scenario: Definition sub-Agent gets active state
- **WHEN** `dispatch(type="definition", role="<name>")` + role
  frontmatter 含 `isolation: worktree`
- **THEN** `task._worktree_state == "active"` after spawn succeeds

### Requirement: SubAgentManager post-task exit decision (NEW)

`SubAgentManager._run_subagent` 的 finally 块 MUST,在 sub-Agent
终态(`done` / `failed` / `canceled`)后,根据 `task._worktree_state`
调度 `WorktreeManager.exit()`:

- `None`:no-op(v1.2 行为零变化)
- `"active"`:调 `WorktreeManager.exit(name, force=False)`;`policy`
  决定清洗干净 / 保留 detached
- TUI 路径 `cancel_subagent(task_id, force=True)`:允许显式调
  `exit(name, force=True)`

#### Scenario: Definition + worktree clean removed
- **WHEN** definition sub-Agent 跑完 + worktree 干净(`git status`
  空 + upstream 已合并)
- **THEN** `exit(name, force=False)` 跑 + state 转 `removed` +
  `.worktrees/<name>/` 物理删除

#### Scenario: Definition + worktree detached preserved
- **WHEN** definition sub-Agent 跑完 + worktree 有未提交文件
- **THEN** `exit(name, force=False)` 返 `Detached(reason=
  "uncommitted_changes")` + state 转 `detached` + `.worktrees/`
  目录**保留**

#### Scenario: Manual force cancel cleans
- **WHEN** TUI 用户手动点 "abort + cleanup" 一个 worktree task
- **THEN** `cancel_subagent(task_id, force=True)` 调
  `WorktreeManager.exit(name, force=True)` + 强制 `git worktree
  remove --force` + 状态 `removed`

#### Scenario: Failed sub-Agent preserves worktree
- **WHEN** sub-Agent 在 worktree 改了一半文件 + state machine 转
  `failed`(LLM API 超时)
- **THEN** 仍然走 `exit(name, force=False)` 检测树(dirty 检
  测照样跑;**不**默认 force);失败 task **不**自动清掉
  dirty worktree

### Requirement: exit force option exposed to TUI (NEW)

`SubAgentManager` MUST 提供新方法 `cancel_subagent(task_id, force=
False)`,TUI 用 `force=True` 显式传:

- `force=False`(默认) → 不传 WorktreeManager.exit 任何 force 参数
- `force=True` → 调 `WorktreeManager.exit(name, force=True)`

#### Scenario: TUI cancel without force
- **WHEN** 用户正常 cancel sub-Agent(不选中"强制清理")
- **THEN** `cancel_subagent(task_id)` 等价 `force=False` + 走
  标准 exit 决策树

#### Scenario: TUI cancel with force
- **WHEN** 用户 cancel sub-Agent 时勾选 "force cleanup worktree"
- **THEN** `cancel_subagent(task_id, force=True)` + worktree 强
  删

### Requirement: Explicit empty tools allowlist is accepted

`ToolFilter.visible_tools` MUST distinguish `role.tools is None`(no constraint, all tools allowed by L2) from `role.tools == []`(explicit empty allowlist). When `role.tools` is a non-`None` empty list `[]`, the filter SHALL return an empty `visible_tools` list without raising `ToolFilterEmptyError`. When any other layer (L1 global deny / L3 role deny / L4 background whitelist) reduces the set to empty, the filter SHALL still raise `ToolFilterEmptyError` as before.

#### Scenario: Explicit empty tools allowlist passes filter
- **WHEN** a sub-Agent `AgentDef` declares `tools: []` in frontmatter and other layers do not independently reduce the set
- **THEN** `ToolFilter.visible_tools` returns `[]` and does NOT raise

#### Scenario: None tools means no constraint
- **WHEN** a sub-Agent `AgentDef` declares `tools:` absent or `tools: null` (Pydantic parses as `None`)
- **THEN** L2 is skipped and `visible_tools` includes all tools from L1 (minus `task` from GLOBAL_DENY)

#### Scenario: L3 reduce-to-empty still errors
- **WHEN** `role.tools_deny` removes all allowed tools (e.g. `tools=["Read"]` + `tools_deny=["Read"]`)
- **THEN** `ToolFilter.visible_tools` raises `ToolFilterEmptyError` (treated as misconfiguration)

### Requirement: summarizer role becomes dispatchable

The builtin `summarizer` role MUST be dispatchable via `SubAgentManager.dispatch(type="definition", role="summarizer")` without raising `ToolFilterEmptyError`. Its frontmatter MUST use `tools: []` (explicit empty allowlist) instead of `tools-deny=[ALL]` to express "I am a tool-less role".

#### Scenario: summarizer frontmatter uses tools: []
- **WHEN** builtin summarizer AGENT.md is read
- **THEN** frontmatter contains `tools: []` (not `tools-deny`)

#### Scenario: summarizer dispatch succeeds
- **WHEN** `SubAgentManager.dispatch(type="definition", role="summarizer", prompt="...")` is called
- **THEN** dispatch returns a `task_id` (async path) or summary string (sync path), no `ToolFilterEmptyError`

### Requirement: dispatch async_=False is removed

`SubAgentManager.dispatch()` MUST raise `NotImplementedError` (not return a Task, not hang) when called with `async_=False`. The error message SHALL explain that `async_=True` is the only supported path from v1.5 onwards and point callers to poll `task.state` or listen for idle notifications.

#### Scenario: async_=False raises NotImplementedError
- **WHEN** `SubAgentManager.dispatch(type=..., role=..., prompt=..., async_=False)` is invoked
- **THEN** it raises `NotImplementedError` immediately (not a `ToolResult`, not a Task, not a hang)

#### Scenario: async_=True still returns task_id
- **WHEN** `SubAgentManager.dispatch(type=..., role=..., prompt=..., async_=True)` is invoked
- **THEN** it returns a `str` task_id and the sub-Agent runs in the background

### Requirement: _dispatch_sync_blocking helper is deleted

The `_dispatch_sync_blocking` method MUST be removed from `SubAgentManager`. Any reference to it (in `dispatch()`, in `task_executor`, in docs) MUST be deleted or updated to point to the `async_=True` path.

#### Scenario: Method no longer exists
- **WHEN** `SubAgentManager` is imported and inspected
- **THEN** `_dispatch_sync_blocking` is NOT a member of the class

