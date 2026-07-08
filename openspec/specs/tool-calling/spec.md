# tool-calling Specification

## Purpose
TBD - created by archiving change v0-2-tool-calling. Update Purpose after archive.
## Requirements
### Requirement: Tools have a unified internal representation
The system MUST represent every available tool as a `ToolDefinition` dataclass with `name: str`, `description: str`, `parameters: dict` (JSON Schema), `risk: Literal["low", "high"]`, `side_effect: bool` (default `False`), and `path_args: list[str]` (default `[]`, naming which arguments are filesystem paths) fields. The system MUST represent a tool invocation emitted by the LLM as a `ToolCall` dataclass with `id: str`, `name: str`, and `arguments: dict` fields. The system MUST represent a tool's outcome as a `ToolResult` dataclass with `tool_call_id: str`, `content: str`, `is_error: bool`, `offloaded_to: Path | None` (default `None`, populated by the v0.7 Layer-1 offload engine when the content is written to `.baozicode/context/<session>/<block>.json`), and `original_size: int` (default `0`, the original byte length of `content` before any offload replaced it with a preview) fields. These three dataclasses are the internal contract between the tool implementations, the LLM backends, and the TUI layer — backend SDK types MUST NOT leak past `baozicode/llm/`.

#### Scenario: ToolDefinition exposes JSON Schema parameters
- **WHEN** a `Read` tool's `ToolDefinition.parameters` is inspected
- **THEN** it is a JSON Schema dict describing a required `file_path` string property

#### Scenario: ToolCall carries the LLM-provided id
- **WHEN** the LLM emits a tool invocation, the yielded `ToolCall` MUST have `id` equal to the backend-provided identifier
- **AND** `name` equal to the registered tool name
- **AND** `arguments` equal to the parsed JSON object

#### Scenario: ToolResult references the originating ToolCall
- **WHEN** a tool finishes executing
- **THEN** the produced `ToolResult.tool_call_id` equals the `id` of the originating `ToolCall`

#### Scenario: ToolDefinition declares its path arguments
- **WHEN** the `Read` tool's `ToolDefinition.path_args` is inspected
- **THEN** it MUST equal `["file_path"]`
- **AND** the `Write` tool's `path_args` MUST equal `["file_path"]`
- **AND** the `Edit` tool's `path_args` MUST equal `["file_path"]`
- **AND** the `Bash` tool's `path_args` MUST equal `[]` (Bash paths are extracted by PathSandbox regex)

#### Scenario: ToolResult fields default to offload-inactive
- **WHEN** a tool finishes executing and its `content` is below the Layer-1 offload threshold
- **THEN** the produced `ToolResult.offloaded_to` equals `None`
- **AND** the produced `ToolResult.original_size` equals `0`
- **AND** downstream code (permissions, conversation manager, TUI cards) MUST treat these as "no offload happened" without conditional branches

#### Scenario: Offloaded ToolResult carries disk path and original size
- **WHEN** the Layer-1 offload engine replaces the `content` of a 50 KB `ToolResult` with a preview
- **THEN** the resulting `ToolResult.offloaded_to` equals the relative project-root path of the offload file (e.g., `.baozicode/context/<session>/<block>.json`)
- **AND** the `ToolResult.original_size` equals the original byte length of `content` before replacement (e.g., `51200`)
- **AND** the `ToolResult.is_error` field is preserved unchanged across the offload

### Requirement: Seven concrete tools are registered
The system MUST register exactly seven tools in the tool registry, accessible via `get_all_tools() -> list[ToolDefinition]`: `Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`, `WebFetch`. Each tool MUST be implemented in its own module under `baozicode/tools/`.

#### Scenario: Registry returns seven tools in fixed order
- **WHEN** `get_all_tools()` is called
- **THEN** it returns a list of seven `ToolDefinition` objects with `name` values exactly `[Read, Write, Edit, Bash, Grep, Glob, WebFetch]`

#### Scenario: Read tool reads a file from disk
- **WHEN** the `Read` tool is invoked with `{"file_path": "README.md"}`
- **THEN** it returns a `ToolResult` whose `content` is the UTF-8 decoded file contents
- **AND** if the file is larger than the configured cap (default 50 KB or 2000 lines, whichever is hit first), the content is truncated and a truncation notice is appended
- **AND** if the file does not exist, `is_error` is `True` and `content` contains the `FileNotFoundError` message

#### Scenario: Write tool creates or overwrites a file
- **WHEN** the `Write` tool is invoked with `{"file_path": "out.txt", "content": "hello"}`
- **THEN** a file at `out.txt` is created (parent directories included) with content `hello`
- **AND** the returned `ToolResult.content` summarizes the write ("Wrote 5 bytes to out.txt")

#### Scenario: Edit tool performs old_string → new_string replacement
- **WHEN** the `Edit` tool is invoked with `{"file_path": "a.py", "old_string": "foo", "new_string": "bar"}`
- **AND** `foo` appears exactly once in `a.py`
- **THEN** `a.py` is updated to replace that single occurrence
- **AND** the returned `ToolResult.content` summarizes the edit

#### Scenario: Edit tool rejects non-unique old_string
- **WHEN** the `Edit` tool is invoked and `old_string` appears zero or more-than-once times in the target file
- **THEN** the file is NOT modified
- **AND** the returned `ToolResult` has `is_error=True` and `content` explaining the count found

#### Scenario: Bash tool runs a shell command via subprocess
- **WHEN** the `Bash` tool is invoked with `{"command": "ls -la"}`
- **THEN** the command is executed via `asyncio.create_subprocess_shell` with a 60-second timeout
- **AND** the returned `ToolResult.content` contains the captured `stdout` followed by `stderr` (if any)
- **AND** `is_error` equals `True` iff the process exits with a non-zero return code

#### Scenario: Bash tool enforces cwd safety boundary
- **WHEN** the `Bash` tool is invoked with a `cd ../../../etc` command
- **THEN** the resolved cwd after the command MUST still be a descendant of the project root
- **AND** if it is not, the command is rejected with `is_error=True` and `content` explains the violation

#### Scenario: Grep tool searches file contents
- **WHEN** the `Grep` tool is invoked with `{"pattern": "TODO", "path": "baozicode/"}`
- **THEN** it returns a `ToolResult.content` containing matching lines in `path:line:content` format
- **AND** if the `ripgrep` binary is available, it uses subprocess; otherwise it falls back to a Python `re` implementation

#### Scenario: Glob tool matches paths against a pattern
- **WHEN** the `Glob` tool is invoked with `{"pattern": "**/*.py"}`
- **THEN** the returned `ToolResult.content` is a newline-separated list of matching file paths (relative to cwd)
- **AND** if no files match, the content is an empty string (not an error)

#### Scenario: WebFetch tool retrieves a URL via HTTP GET
- **WHEN** the `WebFetch` tool is invoked with `{"url": "https://example.com"}`
- **THEN** it issues an `httpx.AsyncClient.get` with a 30-second timeout
- **AND** the returned `ToolResult.content` is the response body decoded as UTF-8 (HTML tags stripped if the response Content-Type is HTML)
- **AND** if the request fails (non-2xx, timeout, network error), `is_error=True` and `content` contains the error message

### Requirement: Tools declare their risk level for permission gating
The system MUST classify every tool as either `risk="low"` (auto-allow) or `risk="high"` (require user confirmation). The classification MUST be: `Read`, `Grep`, `Glob`, `WebFetch` are `low`; `Write`, `Edit`, `Bash` are `high`.

#### Scenario: Registry reports risk per tool
- **WHEN** `get_all_tools()` is called
- **THEN** `Read`, `Grep`, `Glob`, `WebFetch` each have `risk="low"`
- **AND** `Write`, `Edit`, `Bash` each have `risk="high"`

### Requirement: Tool implementations are isolated and testable
Each tool's implementation MUST accept a `dict` of arguments and return a `ToolResult`, without depending on Textual, the LLM layer, or the conversation manager. This allows tools to be unit-tested in isolation.

#### Scenario: Read tool can be called without a running TUI
- **WHEN** `Read().execute({"file_path": "README.md"})` is called from a plain Python script
- **THEN** it returns a `ToolResult` with the file content and `is_error=False`

#### Scenario: Each tool module exposes a single ToolDefinition
- **WHEN** any module under `baozicode/tools/` is imported
- **THEN** it exports exactly one `ToolDefinition` instance and one `execute(arguments: dict) -> ToolResult` function

### Requirement: Tool call permission check precedes execution
The system MUST evaluate every `ToolCall` through `permissions.check(call)` BEFORE invoking `execute_tool_call(call)`. If `permissions.check` returns a decision with `decision="deny"`, the system MUST NOT invoke `execute_tool_call` and MUST synthesize a `ToolResult(tool_call_id=call.id, content=<deny reason>, is_error=True)` that is fed back to the LLM as the tool's outcome.

#### Scenario: Denied call produces is_error result without execution
- **WHEN** a `Bash` call with command `rm -rf /` is denied by L1
- **THEN** `execute_tool_call` MUST NOT be invoked
- **AND** a `ToolResult` with `is_error=True` and `content` containing the L1 deny reason MUST be appended to the conversation
- **AND** the Agent loop MUST continue to the next iteration (not terminate)

#### Scenario: Allowed call proceeds to execution
- **WHEN** a `Bash` call with command `git status` is allowed by L3 rule `Bash(git *)`
- **THEN** `execute_tool_call(call)` MUST be invoked
- **AND** the returned `ToolResult` MUST be appended to the conversation

### Requirement: Denial does not terminate the Agent loop
The system MUST NOT terminate the Agent loop on a single tool denial. The Agent loop MUST continue with the next iteration after a denied tool call, providing the LLM the denial `reason` as the tool result content so the LLM can adjust its strategy.

#### Scenario: LLM adjusts strategy after denial
- **WHEN** a `Write` call to `*.env` is denied by L3
- **AND** the denial `reason` is fed back to the LLM
- **THEN** the Agent loop MUST yield `tool_result` event with the denial content
- **AND** MUST continue to the next iteration
- **AND** MUST NOT yield a `done` event with `StopReason.DENIALS_EXCEEDED`

#### Scenario: Repeated denials trigger soft warning reminder
- **WHEN** the same tool name (e.g., `Write`) is denied `denial_warn_threshold` times (default 5) consecutively
- **THEN** the system MUST inject a `<system-reminder type="denial_rate_limit">` into the next LLM call
- **AND** the reminder content MUST name the repeatedly-denied tool and suggest the LLM consider an alternative approach
- **AND** the Agent loop MUST still continue

#### Scenario: Same call_id retry re-prompts the user
- **WHEN** the same `ToolCall.id` with identical `arguments` is denied and the LLM retries it in a subsequent iteration
- **THEN** the PermissionModal MUST be re-displayed (not silently denied again)
- **AND** the modal title MUST include "(previously denied)" suffix

### Requirement: FAILED_TOOL_LOOP guard remains active
The system MUST continue to apply the `FAILED_TOOL_LOOP` guard: when the same tool name fails (regardless of denial or execution error) `failed_loop_threshold` times (default 3) consecutively with similar failure content, the Agent loop MUST terminate with `StopReason.FAILED_TOOL_LOOP`.

#### Scenario: Repeated execution failures terminate loop
- **WHEN** a `Read` call fails 3 times consecutively with `FileNotFoundError` for the same path
- **THEN** the Agent loop MUST terminate with `StopReason.FAILED_TOOL_LOOP`

#### Scenario: Denial does not count toward FAILED_TOOL_LOOP
- **WHEN** a tool call is denied by L1/L2/L3 (not an execution failure)
- **THEN** the denial MUST NOT increment the `FAILED_TOOL_LOOP` counter
- **AND** MUST increment only the `consecutive_denials` counter (used for soft warning)