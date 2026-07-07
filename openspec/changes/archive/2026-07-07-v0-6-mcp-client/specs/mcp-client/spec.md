## ADDED Requirements

### Requirement: MCP server configuration

The system SHALL read MCP server declarations from the `mcp_servers:` block in `config.yaml`. The configuration MUST support two server transport types:
- `type: stdio` — requires `command` (string), optional `args` (list of string), optional `env` (dict of string to string), optional `cwd` (string).
- `type: http` — requires `url` (string), optional `headers` (dict of string to string).

The system MUST perform `${VAR}` substitution on `command`, `args[*]`, `env[*]`, `url`, and `headers[*]` values using the same `_substitute_env` mechanism used for the rest of the config.

The system MUST merge MCP server declarations from two config layers: project (`./config.yaml`) and user (`~/.config/baozicode/config.yaml`). For any server name declared in both layers, the project declaration MUST take precedence.

#### Scenario: Project overrides user
- **WHEN** user config declares `mcp_servers.filesystem.command = "user-cmd"` and project config declares `mcp_servers.filesystem.command = "project-cmd"`
- **THEN** the resolved config uses `command = "project-cmd"`

#### Scenario: ${VAR} expansion in command
- **WHEN** config declares `mcp_servers.fs.command = "uvx"`, `args = ["--from", "${MCP_PKG}", "server"]`, and env `MCP_PKG=anthropic-mcp-fs`
- **THEN** the launched subprocess uses args `["--from", "anthropic-mcp-fs", "server"]`

### Requirement: Startup handshake and tool discovery

The system SHALL perform a JSON-RPC 2.0 handshake with every declared MCP server during `BaoZiCodeApp` initialization. For each server, the handshake MUST complete the following three steps in order:
1. Send `initialize` request with `protocolVersion: "2025-11-25"`, `capabilities: {}`, `clientInfo: {name: "BaoZiCode", version: "<app version>"}`.
2. Send `notifications/initialized` notification (no `id`, no response expected).
3. Send `tools/list` request and collect the returned tool definitions.

#### Scenario: Successful stdio handshake
- **WHEN** a stdio MCP server responds to `initialize` with `protocolVersion: "2025-11-25"` and to `tools/list` with one tool `read_file`
- **THEN** the system registers one tool named `mcp__<server>__read_file`

#### Scenario: Successful HTTP handshake
- **WHEN** a Streamable HTTP MCP server returns `Mcp-Session-Id: abc123` in the `initialize` response
- **THEN** all subsequent requests to that server include `Mcp-Session-Id: abc123` header

### Requirement: Namespaced tool names

The system MUST register every discovered MCP tool with a name of the form `mcp__<server_name>__<tool_name>`, where `<server_name>` is the key from `mcp_servers:` config and `<tool_name>` is the original tool name returned by `tools/list`. The system MUST reject any tool registration whose name collides with a built-in tool name (Read, Write, Edit, Bash, Grep, Glob, WebFetch) and MUST log a warning excluding it from registration.

#### Scenario: No collision between two servers
- **WHEN** server `filesystem` exposes `read_file` and server `github` exposes `read_file`
- **THEN** both are registered as `mcp__filesystem__read_file` and `mcp__github__read_file` respectively

#### Scenario: Collision with built-in
- **WHEN** server `evil` exposes a tool named `Read`
- **THEN** that tool is NOT registered, and a warning is logged

### Requirement: Tool field inference

The system MUST convert each MCP tool definition into a BaoZiCode `ToolDefinition` with the following mapping:
- `name` → `name` (with the `mcp__<server>__` prefix).
- `description` → `description`.
- `inputSchema` → `parameters`.
- `risk` defaults to `"high"`. If `annotations.readOnlyHint == true`, MUST be `"low"`.
- `side_effect` defaults to `True`. If `annotations.readOnlyHint == true`, MUST be `False`.
- `path_args` MUST be derived by scanning `inputSchema.properties` and collecting every property whose `type` is `"string"` and whose name matches the regex `(?i).*(path|file|dir|root).*`.

#### Scenario: Default conservative for write tools
- **WHEN** MCP server exposes a tool with no `annotations` field
- **THEN** the registered `ToolDefinition` has `side_effect=True` and `risk="high"`

#### Scenario: Read-only annotation lowers risk
- **WHEN** MCP server exposes a tool with `annotations: {readOnlyHint: true}`
- **THEN** the registered `ToolDefinition` has `side_effect=False` and `risk="low"`

#### Scenario: Path heuristic populates path_args
- **WHEN** MCP server exposes a tool whose `inputSchema.properties` includes `file_path`, `query`, and `directory` all of type `string`
- **THEN** `path_args = ["file_path", "directory"]` (only those matching the regex)

### Requirement: Tool invocation through Agent

When `Agent._v5_executor` receives a `ToolCall` whose `name` starts with `mcp__`, the system MUST route the call to the corresponding MCP server's `tools/call` JSON-RPC method with the call's `arguments` as params. The response MUST be converted to a `ToolResult` as follows:
- `content[]` items with `type: "text"` MUST be concatenated in order into `ToolResult.content`.
- `content[]` items with any other type MUST be rendered as `[<type>: <truncated base64 or text>]` and concatenated.
- `isError: true` in the response MUST set `ToolResult.is_error = True`.

#### Scenario: Text-only response
- **WHEN** MCP server returns `{content: [{type: "text", text: "hello"}], isError: false}`
- **THEN** `ToolResult.content = "hello"` and `is_error = False`

#### Scenario: Multi-block response
- **WHEN** MCP server returns `{content: [{type: "text", text: "first"}, {type: "text", text: "second"}]}`
- **THEN** `ToolResult.content = "first\nsecond"`

#### Scenario: Server reports error
- **WHEN** MCP server returns `{content: [...], isError: true}`
- **THEN** `ToolResult.is_error = True`

### Requirement: Permissions integration

MCP tool calls MUST flow through the existing five-layer permission system in the same way as built-in tools. The LLM-visible tool name (`mcp__<server>__<tool>`) MUST be the value used for `ToolCall.name` in `permissions.check()`. L3 rule patterns that users write MUST match against this full namespaced name (e.g., a deny rule `pattern: "mcp__github__*"` blocks all github server tools).

#### Scenario: MCP tool blocked by L3 rule
- **WHEN** user config has a deny rule `{tool: "mcp__github__*", pattern: "*", decision: "deny"}` and LLM calls `mcp__github__search_repos`
- **THEN** `permissions.check()` returns `decision: deny` with `layer: L3_rule`

#### Scenario: MCP tool triggers L2 sandbox
- **WHEN** LLM calls `mcp__fs__read_file` with `arguments.file_path = "/etc/passwd"` and the tool's `path_args` includes `file_path`
- **THEN** L2 PathSandbox denies the call because `/etc/passwd` is outside `project_root`

### Requirement: Failure isolation

The system MUST wrap each per-server startup handshake in an independent `try/except`. A failure (subprocess crash, HTTP connection refused, handshake timeout, `tools/list` error) MUST NOT prevent other servers from completing. Failed servers MUST be recorded with their error message but excluded from tool registration. The TUI startup banner MUST display a warning line per failed server.

#### Scenario: One stdio server fails, others succeed
- **WHEN** three servers are declared; server `a` crashes on startup, servers `b` and `c` connect normally
- **THEN** tools from `b` and `c` are registered; tools from `a` are not; banner shows one warning line for `a`

#### Scenario: tools/list returns empty list
- **WHEN** server connects successfully but `tools/list` returns `{tools: []}`
- **THEN** the server is recorded as connected with zero tools; no error; no warning

### Requirement: Stderr capture for stdio servers

The system MUST drain stderr from every stdio MCP server subprocess continuously and route the bytes to a Python logger named `baozicode.mcp.<server_name>` at DEBUG level. The system MUST NOT block on stderr reads (use a dedicated asyncio task). The system MUST NEVER mix stderr bytes into the stdout JSON-RPC stream.

#### Scenario: Server writes to stderr
- **WHEN** stdio server prints "starting up..." to stderr during handshake
- **THEN** the message appears as a DEBUG log entry under logger `baozicode.mcp.<server_name>` and does not corrupt any JSON-RPC frame on stdout

### Requirement: Staged timeouts

The system MUST enforce three configurable timeouts (defaults shown, overridable per-server in config):
- `init_timeout_s` (default 5): bounds the `initialize` request.
- `tools_list_timeout_s` (default 8): bounds the `tools/list` request.
- `startup_total_timeout_s` (default 15): bounds the entire per-server bootstrap (init + initialized + tools/list).

A timeout MUST be treated as a handshake failure (per Failure isolation requirement).

#### Scenario: init timeout
- **WHEN** server takes 10 seconds to respond to `initialize` and `init_timeout_s = 5`
- **THEN** the handshake is aborted, server marked failed, no tools registered from it

#### Scenario: tools/list timeout
- **WHEN** server responds to `initialize` quickly but hangs on `tools/list`
- **THEN** the timeout for `tools/list` fires (default 8s), server marked failed

### Requirement: Server-initiated request rejection

When the system receives a JSON-RPC request initiated by the server (any method the client did not invoke), the system MUST respond with a JSON-RPC error response `{jsonrpc: "2.0", id: <request id>, error: {code: -32601, message: "Method not found"}}`. The system MUST also log a DEBUG entry naming the rejected method. The system MUST NOT respond to server notifications (which have no `id`).

#### Scenario: Server sends sampling request
- **WHEN** server sends `{jsonrpc: "2.0", id: 5, method: "sampling/createMessage"}`
- **THEN** client responds with `{"jsonrpc": "2.0", "id": 5, "error": {"code": -32601, "message": "Method not found"}}`

#### Scenario: Server sends notification
- **WHEN** server sends `{jsonrpc: "2.0", method: "notifications/log", params: {...}}` (no id)
- **THEN** client does NOT respond; logs the notification at DEBUG

### Requirement: Disconnection handling

When a server's connection breaks mid-session (subprocess exits, HTTP connection closes, read loop encounters EOF), the system MUST mark that server as `broken`. Any subsequent `tools/call` to a broken server MUST immediately return `ToolResult.is_error=True` with content `"MCP server '<name>' disconnected"`. The TUI `/mcp` command MUST offer a `reconnect <name>` subcommand that re-runs the handshake for that single server.

#### Scenario: Subprocess dies during session
- **WHEN** stdio server's subprocess exits with code 1 while no tool call is in flight
- **THEN** the server is marked broken; subsequent `tools/call` for any of its tools returns is_error=True

#### Scenario: Reconnect restores server
- **WHEN** user issues `/mcp reconnect filesystem` and the new handshake succeeds
- **THEN** the server's status returns to `connected`; its tools are available again

### Requirement: TUI /mcp slash command

The TUI MUST register a `/mcp` slash command that displays a table of all declared MCP servers with their status (`connected` / `failed` / `broken`), tool count (for connected), and error message (for failed/broken). The command MUST accept an optional subcommand `reconnect <name>` to re-handshake a broken or failed server. Status output MUST reflect the state at the moment of invocation.

#### Scenario: List connected servers
- **WHEN** user types `/mcp` and 2 servers are connected each with 3 tools, 1 server failed
- **THEN** output shows 3 rows: 2 with status `connected` and tool count 3, 1 with status `failed` and its error message
