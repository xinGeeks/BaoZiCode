# subagent-manager Specification — v1.3 ADDED Requirements

> This is the v1.3 delta on top of v1.2 subagent-manager capability.
> Existing v1.2 behavior (task lifecycle, `task` tool, dispatcher,
> cascade cancel, `TaskInfo` state machine) is **unchanged**. New
> behavior is added below.

## ADDED Requirements

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

## MODIFIED Requirements

无(全部 v1.3 行为以 NEW section ADD,没有改 v1.2 现有 requirement
的语义)。

## REMOVED Requirements

无。
