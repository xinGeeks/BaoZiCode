## ADDED Requirements

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