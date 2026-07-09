## MODIFIED Requirements

### Requirement: Tools have a unified internal representation
The system MUST represent every available tool as a `ToolDefinition` dataclass with `name: str`, `description: str`, `parameters: dict` (JSON Schema), `risk: Literal["low", "high"]`, `side_effect: bool` (default `False`), `path_args: list[str]` (default `[]`, naming which arguments are filesystem paths), and `tool_type: Literal["internal", "external"]` (default `external`, set to `internal` for system tools like `load_skill` that bypass user Skill whitelists) fields. The system MUST represent a tool invocation emitted by the LLM as a `ToolCall` dataclass with `id: str`, `name: str`, and `arguments: dict` fields. The system MUST represent a tool's outcome as a `ToolResult` dataclass with `tool_call_id: str`, `content: str`, `is_error: bool` (derived value — see Scenario), `offloaded_to: Path | None` (default `None`, populated by the v0.7 Layer-1 offload engine when the content is written to `.baozicode/context/<session>/<block>.json`), `original_size: int` (default `0`, the original byte length of `content` before any offload replaced it with a preview), `execution_status: Literal["block_l1", "block_hook_pre", "block_permission", "executed_success", "executed_failed"] | None` (default `None`, populated by the v1.1 hook-aware executor; `None` means "constructed without v1.1 pipeline awareness"), `denied_by: Literal["l1_blacklist", "hook_pre", "l2_l5_permission"] | None` (default `None`, populated only when `execution_status` is a `block_*` value), and `denied_hook_id: str | None` (default `None`, populated only when `denied_by == "hook_pre"` to record which hook's denial produced this result) fields. These three dataclasses are the internal contract between the tool implementations, the LLM backends, and the TUI layer — backend SDK types MUST NOT leak past `baozicode/llm/`.

The `is_error` field MUST be computed as `is_error = (execution_status is not None and execution_status != "executed_success")` whenever `execution_status` is set. When `execution_status is None` (legacy or out-of-band construction), `is_error` is whatever the caller explicitly passed.

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

#### Scenario: ToolDefinition declares tool_type
- **WHEN** the `load_skill` tool (added in v1.0) is registered
- **THEN** its `tool_type` MUST equal `"internal"`
- **AND** the seven built-in tools (Read / Write / Edit / Bash / Grep / Glob / WebFetch) MUST have `tool_type="external"` by default

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

#### Scenario: Legacy ToolResult construction still works
- **WHEN** old code (v1.0 or earlier) constructs `ToolResult(tool_call_id="x", content="y", is_error=True)` without setting `execution_status`
- **THEN** the resulting ToolResult has `execution_status=None`, `denied_by=None`, `denied_hook_id=None`
- **AND** `is_error=True` (as set by the caller, not derived since `execution_status is None`)
- **AND** downstream code reading `is_error` still works correctly

#### Scenario: is_error derived from execution_status
- **WHEN** new code (v1.1) sets `execution_status="executed_success"` and `is_error` is not explicitly provided
- **THEN** `is_error=False` (derived)
- **WHEN** `execution_status="executed_failed"` and `is_error` is not explicitly provided
- **THEN** `is_error=True` (derived)
- **WHEN** `execution_status="block_l1"` (or any `block_*` value)
- **THEN** `is_error=True` (derived)

#### Scenario: Hook denial populates denied_by and denied_hook_id
- **WHEN** the v1.1 executor denies a call via hook.pre and the denying hook has `id="audit-risky"`
- **THEN** the returned ToolResult has `execution_status="block_hook_pre"`, `denied_by="hook_pre"`, `denied_hook_id="audit-risky"`, `is_error=True` (derived), `content="<deny reason>"`

### Requirement: Tool call pipeline integration with hooks (L1 → hook.pre → L2-L5 → execute → hook.post)
The system MUST evaluate every `ToolCall` through the v1.1 hook-aware pipeline in this fixed order:

1. **L1 DangerousCommandBlacklist** — if deny, return `ToolResult(execution_status="block_l1", denied_by="l1_blacklist", is_error=True, content=L1_reason)`. Continue to step 2 otherwise.
2. **v1.1 hook.pre** — if any registered hook returns deny, return `ToolResult(execution_status="block_hook_pre", denied_by="hook_pre", denied_hook_id=<first-deny-hook-id>, is_error=True, content=hook_reason)`. Continue to step 3 otherwise.
3. **L2 PathSandbox → L3 RuleEngine → L4 PermissionMode → L5 PermissionCallback** (v0.5 pipeline) — if deny, return `ToolResult(execution_status="block_permission", denied_by="l2_l5_permission", is_error=True, content=perm_reason)`. Continue to step 4 otherwise.
4. **execute_tool_call** — invoke the tool implementation. On success, return `ToolResult(execution_status="executed_success", is_error=False, content=<tool output>)`. On tool error, return `ToolResult(execution_status="executed_failed", is_error=True, content=<error msg>)`.
5. **v1.1 hook.post** — fire ALL registered `tool.post` hooks on the produced ToolResult, regardless of which earlier step produced it.

The system MUST invoke L1 BEFORE hook.pre — a hook.pre rule MUST NOT be able to allow a call that L1 would deny. The system MUST invoke hook.post on EVERY tool_call attempt — including L1 denials, hook.pre denials, L2-L5 denials, successful executions, and execution errors.

#### Scenario: L1 denies before hook.pre
- **WHEN** L1 hard blacklist denies `Bash("rm -rf /")` and there are 3 `tool.pre` hooks registered
- **THEN** the ToolResult is `execution_status="block_l1"`, `denied_by="l1_blacklist"`
- **AND** NO `tool.pre` hooks fire for this call
- **AND** `tool.post` hooks still fire on this denied ToolResult

#### Scenario: hook.pre denies before L2-L5
- **WHEN** L1 allows `Bash("chmod 777 /tmp/x")` and a `tool.pre` hook denies it
- **THEN** the ToolResult is `execution_status="block_hook_pre"`, `denied_by="hook_pre"`, `denied_hook_id=<hook id>`
- **AND** L2-L5 MUST NOT be evaluated
- **AND** `tool.post` still fires

#### Scenario: L2-L5 denies after hook.pre allows
- **WHEN** hook.pre allows `Bash("rm foo.txt")` and L2 path sandbox denies it (path outside project root)
- **THEN** the ToolResult is `execution_status="block_permission"`, `denied_by="l2_l5_permission"`
- **AND** `tool.post` still fires

#### Scenario: hook.post fires on successful execution
- **WHEN** `Bash("ls")` is allowed by all layers and executes successfully
- **THEN** the ToolResult is `execution_status="executed_success"`, `is_error=False`
- **AND** all registered `tool.post` hooks fire on this result

#### Scenario: hook.post fires on execution error
- **WHEN** `Read("missing.txt")` returns `is_error=True` (file not found)
- **THEN** the ToolResult has `execution_status="executed_failed"`, `is_error=True`
- **AND** all registered `tool.post` hooks fire on this result

#### Scenario: Hook failure does not break pipeline
- **WHEN** a `tool.pre` hook raises an exception during execution
- **THEN** the hook is logged as a warning
- **AND** the pipeline continues as if the hook returned allow (hook.pre failures do not deny)
- **AND** `tool.post` still fires after the resulting permission check / execution

## ADDED Requirements

### Requirement: No new requirement body
This section is intentionally a placeholder. The MODIFIED block above captures all
v1.1 deltas to this capability. (Required by spec format.)