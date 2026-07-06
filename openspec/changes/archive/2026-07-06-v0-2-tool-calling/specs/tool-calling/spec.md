# tool-calling Specification (NEW)

## Purpose
Define the tool-calling capability for BaoZiCode: the seven concrete tools (Read / Write / Edit / Bash / Grep / Glob / WebFetch), the unified internal data model (`ToolDefinition` / `ToolCall` / `ToolResult`), the registry that exposes them to LLM backends, and the streaming-protocol rules that govern how tool_use deltas appear in the assistant stream.

## ADDED Requirements

### Requirement: Tools have a unified internal representation
The system MUST represent every available tool as a `ToolDefinition` dataclass with `name: str`, `description: str`, `parameters: dict` (JSON Schema), and `risk: Literal["low", "high"]` fields. The system MUST represent a tool invocation emitted by the LLM as a `ToolCall` dataclass with `id: str`, `name: str`, and `arguments: dict` fields. The system MUST represent a tool's outcome as a `ToolResult` dataclass with `tool_call_id: str`, `content: str`, and `is_error: bool` fields. These three dataclasses are the internal contract between the tool implementations, the LLM backends, and the TUI layer — backend SDK types MUST NOT leak past `baozicode/llm/`.

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