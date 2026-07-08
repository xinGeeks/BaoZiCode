## MODIFIED Requirements

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
