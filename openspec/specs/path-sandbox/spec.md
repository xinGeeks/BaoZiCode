# path-sandbox Specification

## Purpose
TBD - created by archiving change v0-5-permissions. Update Purpose after archive.
## Requirements
### Requirement: Project root is resolved to real path on startup
The system MUST resolve the project root directory to a real (symlink-free) absolute path at startup using `Path.resolve()`. This `real_root` MUST be the reference for all subsequent path sandbox checks. The real root MUST NOT change during the session.

#### Scenario: Project root with symlink is resolved correctly
- **WHEN** the project root contains a symlink that points to `/tmp/real-project`
- **THEN** `real_root` MUST be `/tmp/real-project` (the resolved path)
- **AND** all subsequent path checks MUST use `/tmp/real-project` as the reference

### Requirement: Read/Write/Edit file_path is sandboxed to project root
The system MUST verify that the `file_path` argument of any `Read`, `Write`, or `Edit` tool call, after `Path.resolve()`, is a descendant of `real_root`. If the resolved path escapes `real_root`, the system MUST return DENY with `layer="L2_sandbox"` and the resolved target path in the `reason`.

#### Scenario: Read of project file is allowed
- **WHEN** the tool call is `Read(file_path="baozicode/app.py")`
- **THEN** the resolved path `<project_root>/baozicode/app.py` MUST be inside `real_root`
- **AND** L2 MUST return fallthrough

#### Scenario: Read of /etc/passwd is denied
- **WHEN** the tool call is `Read(file_path="/etc/passwd")`
- **THEN** the resolved path `/etc/passwd` MUST NOT be inside `real_root`
- **AND** L2 MUST return DENY with `reason` mentioning `/etc/passwd`

#### Scenario: Write via symlink escape is denied
- **WHEN** `<project_root>/link_to_etc` is a symlink to `/etc`
- **AND** the tool call is `Write(file_path="link_to_etc/passwd", content="...")` 
- **THEN** the resolved path `/etc/passwd` MUST NOT be inside `real_root`
- **AND** L2 MUST return DENY

#### Scenario: Path traversal via .. is denied
- **WHEN** the tool call is `Read(file_path="../../../etc/passwd")`
- **THEN** `Path.resolve()` MUST normalize the path to `/etc/passwd`
- **AND** L2 MUST return DENY

### Requirement: Bash command paths are sandboxed via path-literal extraction
For `Bash` tool calls, the system MUST extract path-like literals from the command using a conservative regex, resolve each candidate path, and verify it stays within `real_root`. Any path that escapes MUST trigger L2 DENY.

#### Scenario: rm on project file is allowed
- **WHEN** the tool call is `Bash(command="rm baozicode/old.py")`
- **THEN** the regex extracts `baozicode/old.py`, resolves to `<project_root>/baozicode/old.py`
- **AND** L2 MUST return fallthrough

#### Scenario: rm on /etc/passwd is denied
- **WHEN** the tool call is `Bash(command="rm /etc/passwd")`
- **THEN** the regex extracts `/etc/passwd`
- **AND** L2 MUST return DENY

#### Scenario: Variable expansion in path triggers deny
- **WHEN** the tool call is `Bash(command="cp $HOME/secret /tmp/x")`
- **THEN** L2 MUST detect the `$HOME` shell expansion marker
- **AND** MUST return DENY with `reason` mentioning "shell expansion in path"

#### Scenario: Multiple path literals, one escapes
- **WHEN** the tool call is `Bash(command="mv good.txt /etc/passwd")`
- **THEN** both `good.txt` and `/etc/passwd` are extracted
- **AND** L2 MUST return DENY because `/etc/passwd` escapes

### Requirement: ToolDefinition declares its path arguments
Each `ToolDefinition` MUST declare a `path_args: list[str]` field listing the names of arguments that contain filesystem paths. Read/Write/Edit MUST declare `path_args=["file_path"]`. Bash MUST declare `path_args=[]` (Bash paths are extracted by the sandbox's regex, not from a single argument).

#### Scenario: ToolDefinition.path_args is inspected
- **WHEN** the `Write` tool's `ToolDefinition.path_args` is inspected
- **THEN** it MUST equal `["file_path"]`

#### Scenario: Bash.path_args is empty
- **WHEN** the `Bash` tool's `ToolDefinition.path_args` is inspected
- **THEN** it MUST equal `[]`

### Requirement: Sandbox is opt-in via project root configuration
The system MUST instantiate `PathSandbox` with the resolved `project_root` only if a path-sandbox configuration is enabled. By default, the sandbox MUST be enabled. The system MUST allow disabling per-project via `.baozicode/permissions.yaml` with `path_sandbox: false` (strict-mode-friendly escape hatch).

#### Scenario: Default project enables sandbox
- **WHEN** no `.baozicode/permissions.yaml` exists
- **THEN** PathSandbox MUST be enabled with `real_root` set to the startup project root
- **AND** L2 MUST evaluate every relevant call

#### Scenario: Explicit disable in project YAML
- **WHEN** `.baozicode/permissions.yaml` contains `path_sandbox: false`
- **THEN** L2 MUST return fallthrough for every call without evaluation
- **AND** a warning MUST be logged at startup

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

