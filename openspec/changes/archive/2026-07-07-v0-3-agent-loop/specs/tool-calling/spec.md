## MODIFIED Requirements

### Requirement: Tools have a unified internal representation
The system MUST represent every available tool as a `ToolDefinition` dataclass with `name: str`, `description: str`, `parameters: dict` (JSON Schema), `risk: Literal["low", "high"]`, and `side_effect: bool` fields. `side_effect` defaults to `False` for backward compatibility; each tool module MUST declare it explicitly. The system MUST represent a tool invocation emitted by the LLM as a `ToolCall` dataclass with `id: str`, `name: str`, and `arguments: dict` fields. The system MUST represent a tool's outcome as a `ToolResult` dataclass with `tool_call_id: str`, `content: str`, and `is_error: bool` fields. These three dataclasses are the internal contract between the tool implementations, the LLM backends, and the TUI layer — backend SDK types MUST NOT leak past `baozicode/llm/`.

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

#### Scenario: ToolDefinition declares side_effect flag
- **WHEN** any tool module under `baozicode/tools/` is inspected
- **THEN** its `ToolDefinition.side_effect` is explicitly set (not relying on the default)
- **AND** the value is `False` for `Read`, `Grep`, `Glob`, `WebFetch` and `True` for `Write`, `Edit`, `Bash`

## ADDED Requirements

### Requirement: Three-layer stop guards terminate runaway tool sequences
The Agent MUST apply three independent guards before executing each tool call. Each guard examines the current call and a per-run state (deny counter, failure window, recent unknown names), returning `None` to allow or a `StopReason` to terminate. The Agent MUST trigger `_terminate(reason)` whenever any guard returns non-`None`.

#### Scenario: Unknown tool name guard allows first occurrence
- **WHEN** the LLM calls a tool name not in the registry for the first time in this run
- **THEN** the guard returns `None`
- **AND** the Agent returns an error `ToolResult` to the LLM (treating it as a failed tool, not a stopped run)
- **AND** the unknown name is recorded in the recent-unknown list

#### Scenario: Unknown tool name guard terminates on consecutive repeat
- **WHEN** the LLM calls the same unknown tool name that appeared in the immediately preceding iteration
- **THEN** the guard returns `StopReason.UNKNOWN_TOOL_HALLUCINATION`
- **AND** the Agent triggers `_terminate(UNKNOWN_TOOL_HALLUCINATION)`

#### Scenario: Denial threshold guard
- **WHEN** the same tool name has been denied (by user pressing N OR by `permissions.deny` matching) 3 times cumulatively in this run
- **THEN** the guard returns `StopReason.DENIALS_EXCEEDED`
- **AND** the Agent triggers `_terminate(DENIALS_EXCEEDED)`

#### Scenario: Failed tool loop guard uses precise hash
- **WHEN** the same `(tool_name, sha256(error_msg)[:16])` triple has appeared as a tool_result 3 times consecutively in this run
- **THEN** the guard returns `StopReason.FAILED_TOOL_LOOP`
- **AND** the Agent triggers `_terminate(FAILED_TOOL_LOOP)`
- **AND** a tool call whose error message hashes to a different value does NOT count toward this threshold

#### Scenario: Guards are checked in order, first match wins
- **WHEN** multiple guards would return non-`None` for the same call
- **THEN** the Agent uses the first non-`None` in the order: unknown → denial → failed loop
- **AND** only one `done` event is emitted
