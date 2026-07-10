# path-sandbox Specification — v1.3 ADDED Requirements

> This is the v1.3 delta on top of existing path-sandbox capability.
> `PathSandbox(real_root)` API 和现有的判定逻辑(v0.5) **不变**。
> v1.3 新增的场景是:sub-Agent 在 worktree 隔离场景下,`real_root`
> 由 `SubAgentRuntime.spawn` 传成 worktree 路径。

## ADDED Requirements

### Requirement: SubAgent's sandbox binds effective project root (NEW)

In v1.3, `SubAgentRuntime.spawn` 构造 sub-Agent 时 MUST 把
`MergedPermissions.real_root` 设为 **worktree 路径**(不是主
project_root):

- normal sub-Agent(real_root=main)→ v1.2 行为
- worktree sub-Agent(real_root=worktree path)→ 文件操作物理
  隔离

#### Scenario: Main Agent sandbox unchanged
- **WHEN** 主 Agent 跑
- **THEN** `PathSandbox.real_root = main project_root`(v1.2 不变)

#### Scenario: Worktree sub-Agent sandbox scope
- **WHEN** worktree sub-Agent 跑
- **THEN** `PathSandbox.real_root = <worktree_path>` + 所有文件 /
  Bash 路径校验**只**允许在 worktree 内

#### Scenario: Two parallel worktrees don't intersect
- **WHEN** 2 个 worktree sub-Agent 同时跑,各自 worktree A 和
  worktree B
- **THEN** A 的 sandbox **不**允许读 B 内的文件(`/path/to/B/...`)
  (用 `is_relative_to(real_root=A_path)` 判定 = False)

### Requirement: Read/Write/Grep sandbox via tool path_args (NEW SCENARIO)

`PathSandbox` MUST 校验 Read / Write / Grep / Glob 工具的
`path_args`,且 `real_root` MUST 是 worktree 路径(非主 repo
`project_root`),当 sub-Agent 跑在 worktree 下时:

#### Scenario: Read inside worktree allowed
- **WHEN** worktree sub-Agent 调
  `Read(file_path=<worktree>/src/foo.py)`
- **THEN** `PathSandbox.check(call)` 命中沙箱,放行

#### Scenario: Write outside worktree blocked
- **WHEN** worktree sub-Agent 调
  `Write(file_path=<setup_dir>/src/foo.py)`(直接用绝对路径)
- **THEN** `PathSandbox.check(call)` 命中 `path_args[0]=
  "file_path"` + 解析 `<setup_dir>` **不** is_relative_to
  `<worktree>` → deny(L2 sandbox)

#### Scenario: Grep path relative to worktree
- **WHEN** worktree sub-Agent 调
  `Grep(pattern="foo", path="./src")`
- **THEN** PathSandbox 把 path 解析为 `<worktree>/src`,在沙箱
  内,放行

## MODIFIED Requirements

无(没有改 v0.5 现有的 PathSandbox 内部逻辑)。

## REMOVED Requirements

无。
