## MODIFIED Requirements

### Requirement: Tools have a unified internal representation
The system MUST represent every available tool as a `ToolDefinition` dataclass with `name: str`, `description: str`, `parameters: dict` (JSON Schema), `risk: Literal["low", "high"]`, `side_effect: bool` (default `False`), and `path_args: list[str]` (default `[]`, naming which arguments are filesystem paths) fields. The system MUST represent a tool invocation emitted by the LLM as a `ToolCall` dataclass with `id: str`, `name: str`, and `arguments: dict` fields. The system MUST represent a tool's outcome as a `ToolResult` dataclass with `tool_call_id: str`, `content: str`, and `is_error: bool` fields. These three dataclasses are the internal contract between the tool implementations, the LLM backends, and the TUI layer — backend SDK types MUST NOT leak past `baozicode/llm/`.

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

## ADDED Requirements

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