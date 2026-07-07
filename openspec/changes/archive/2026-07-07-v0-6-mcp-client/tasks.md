## 1. Configuration schema and loader

- [x] 1.1 Add `McpServerConfig` Pydantic model with discriminated union on `type: Literal["stdio", "http"]`; stdio variant has `command: str`, `args: list[str] = []`, `env: dict[str, str] = {}`, `cwd: str | None = None`, `init_timeout_s: float = 5`, `tools_list_timeout_s: float = 8`, `startup_total_timeout_s: float = 15`, `call_timeout_s: float = 60`; http variant has `url: str`, `headers: dict[str, str] = {}`, same timeout fields
- [x] 1.2 Add `mcp_servers: dict[str, McpServerConfig] = {}` to `AppConfig`
- [x] 1.3 In `config/loader.py:load_config()`, after existing Pydantic validation, perform two-layer merge for `mcp_servers` (read both project and user configs, deep-merge dict with project winning per-key); apply `_substitute_env` to the merged dict before final Pydantic validation; log warning if both layers declare the same server name with different `type`
- [x] 1.4 Add unit tests covering: stdio/http variants parse, `${VAR}` expansion works in `command`/`args`/`env`/`url`/`headers`, project overrides user per server, missing `command`/`url` raises Pydantic ValidationError

## 2. MCP types and JSON-RPC primitives

- [x] 2.1 Create `baozicode/mcp/__init__.py` exposing public API: `bootstrap`, `McpClientManager`, `McpSession`, `McpServerStatus`
- [x] 2.2 Create `baozicode/mcp/types.py` with dataclasses: `JsonRpcRequest { jsonrpc, id, method, params }`, `JsonRpcResponse { jsonrpc, id, result }`, `JsonRpcError { jsonrpc, id, error: {code, message, data} }`, `JsonRpcNotification { jsonrpc, method, params }`, `McpTool { name, description, inputSchema, annotations? }`, `McpCallResult { content: list[dict], isError: bool }`, `McpServerStatus = Literal["connected", "failed", "broken"]`
- [x] 2.3 Create `baozicode/mcp/jsonrpc.py` with `JsonRpcDispatcher` class: maintains `dict[int, asyncio.Future]`, `next_id: int = 0` counter (monotonic per dispatcher), `dispatch_incoming(frame)` returns either the resolution of a pending request or an outgoing error frame (for server-initiated requests); `make_request(method, params)` returns `(id, Future)` for the transport to write and await
- [x] 2.4 Unit tests for JsonRpcDispatcher: out-of-order response resolves correct future, server request triggers error response, server notification is logged but not responded to, dispatcher survives N concurrent pending requests

## 3. Stdio transport

- [x] 3.1 Create `baozicode/mcp/transport_stdio.py` with `StdioTransport` class: `__init__(command, args, env, cwd, logger_name)` launches `asyncio.create_subprocess_exec(*args, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=env, cwd=cwd)`; starts a `stderr_drain_task` that loops `await proc.stderr.readline()` and logs at DEBUG
- [x] 3.2 Implement `async send(frame: str)` that writes `frame + "\n"` to `proc.stdin` (encoded UTF-8); implement `async recv_loop()` generator that reads `proc.stdout` line by line, yields parsed JSON-RPC frames, returns when EOF
- [x] 3.3 Implement `is_alive() -> bool` checking `proc.returncode is None`; implement `async close()` that terminates the subprocess (SIGTERM then SIGKILL after 5s grace) and awaits the stderr drain task
- [x] 3.4 Unit tests with a fake subprocess script (Python script that echoes JSON-RPC frames): round-trip send/recv, close terminates within 5s, stderr lines appear in named logger at DEBUG, EOF on stdout causes recv_loop to exit

## 4. Streamable HTTP transport

- [x] 4.1 Create `baozicode/mcp/transport_http.py` with `HttpTransport` class: `__init__(url, headers)` stores config and creates `httpx.AsyncClient(timeout=...)` lazily
- [x] 4.2 Implement `async send_request(method, params, session_id) -> dict` that POSTs JSON-RPC frame to `url` with `Accept: application/json, text/event-stream` header; dispatches response by status code and content-type (202 → None, 200+json → parsed JSON, 200+sse → parsed SSE event stream → list of frames); returns the first frame with matching `id`; updates `self._session_id` from `Mcp-Session-Id` response header on init
- [x] 4.3 Implement `async send_notification(method, params)` that POSTs and ignores 202 response; implement `async send_error_response(id, code, message)` that POSTs error response to server-initiated request callback URL
- [x] 4.4 Implement `is_alive() -> bool` (always True unless client closed); implement `async close()` that awaits the AsyncClient
- [x] 4.5 Unit tests with `httpx.MockTransport`: session ID captured and resent, three response shapes handled correctly, notification POST ignores body, server-initiated request rejected with proper JSON-RPC error

## 5. MCP session (per-server handshake and tool calls)

- [x] 5.1 Create `baozicode/mcp/client.py` with `McpSession` class: `__init__(name, transport)` stores config and creates a `JsonRpcDispatcher`; methods `initialize()`, `send_initialized_notification()`, `list_tools() -> list[McpTool]`, `call_tool(name, arguments) -> McpCallResult`
- [x] 5.2 Implement `initialize()`: sends `initialize` request with `protocolVersion: "2025-11-25"`, `capabilities: {}`, `clientInfo: {name: "BaoZiCode", version: APP_VERSION}`; awaits response with `init_timeout_s`; stores returned `serverInfo` and `capabilities` on self
- [x] 5.3 Implement `send_initialized_notification()`: writes notification frame, no wait
- [x] 5.4 Implement `list_tools()`: sends `tools/list`, awaits with `tools_list_timeout_s`, returns `result.tools` list; on error raises `McpError`
- [x] 5.5 Implement `call_tool(name, arguments)`: sends `tools/call` with `{name, arguments}`, awaits with `call_timeout_s`, returns `result` (cast to `McpCallResult`)
- [x] 5.6 Implement `start_recv_loop()` background task that continuously pulls frames from transport and feeds dispatcher
- [x] 5.7 Implement `disconnect()`: cancels recv loop task, closes transport
- [x] 5.8 Unit tests with mocked transport: full handshake sequence, list_tools returns expected list, call_tool round-trip, timeout aborts with `asyncio.TimeoutError`

## 6. Tool adapter (MCP → ToolDefinition, McpCallResult → ToolResult)

- [x] 6.1 Create `baozicode/mcp/adapter.py` with `adapt_tool(server_name, mcp_tool: McpTool) -> ToolDefinition`: name becomes `f"mcp__{server_name}__{mcp_tool.name}"`; description and parameters map directly; risk defaults `"high"`, becomes `"low"` if `annotations.readOnlyHint is True`; side_effect defaults `True`, becomes `False` if `annotations.readOnlyHint is True`; path_args derived by scanning `inputSchema.properties` for type=="string" and name matching `(?i).*(path|file|dir|root).*`
- [x] 6.2 Create `adapt_call_result(call: McpCallResult) -> ToolResult`: iterate `call.content[]`; for `type=="text"` append `.text`; for other types append `[<type>: <truncated representation>]`; set `is_error = call.isError`
- [x] 6.3 Unit tests: name prefixing, default conservative values, readOnlyHint lowers values, path_args heuristic catches file_path/dir/query correctly, call_result with mixed text+image blocks renders correctly

## 7. McpClientManager (multi-server lifecycle)

- [x] 7.1 Create `baozicode/mcp/manager.py` with `McpClientManager` class: `__init__(configs: dict[str, McpServerConfig])`; `statuses: dict[str, tuple[McpServerStatus, str, list[ToolDefinition], Executor | None]]`; `_lock: asyncio.Lock` for status mutations
- [x] 7.2 Implement `async bootstrap()`: for each server config, wraps `connect_and_list(server_name, config)` in try/except; on success, registers adapted tools via `tools.registry.register_mcp_tool`; on failure, records `("failed", error_message, [], None)`; runs all servers concurrently with `asyncio.gather(return_exceptions=True)` and a `startup_total_timeout_s` outer cap
- [x] 7.3 Implement `async connect_and_list(server_name, config)`: instantiate transport (Stdio or Http based on `config.type`), launch transport, instantiate `McpSession`, run handshake (initialize + initialized notification + list_tools), return `(tools, executor)` where executor is a closure capturing the session for `tools/call`
- [x] 7.4 Implement `get_all_tools() -> list[ToolDefinition]`: iterate statuses, flatten tools of `connected` servers only
- [x] 7.5 Implement `async invoke_tool(tool_name, arguments) -> ToolResult`: look up tool by `mcp__<server>__<tool>` format, find executor, call `session.call_tool`, adapt result; if server is `failed`/`broken` return `ToolResult.error_result(...)` immediately
- [x] 7.6 Implement disconnect detection: when a session's recv_loop raises (subprocess exit, HTTP EOF), mark server `broken` via status update; subsequent `invoke_tool` calls return error
- [x] 7.7 Implement `async reconnect(server_name)`: tear down existing session (if any), re-run `connect_and_list`, update status; return new tool list
- [x] 7.8 Unit tests: bootstrap with 3 servers where 1 fails → 2 connected + 1 failed in statuses; invoke_tool on broken server returns error_result; reconnect restores broken server; concurrent bootstrap with timeout aborts slow server

## 8. Tools registry refactor

- [x] 8.1 In `baozicode/tools/registry.py`, define `ToolRegistry` class with `_builtin_tools: list[ToolDefinition]`, `_mcp_tools: dict[str, ToolDefinition]`, `_executors: dict[str, Executor]`, `_lock: threading.Lock` (or `asyncio.Lock`); constructor registers the 7 built-in tools and their executors
- [x] 8.2 Implement `register_mcp_tool(tool: ToolDefinition, executor: Executor)` — appends to `_mcp_tools` and `_executors` under the same lock; rejects (raises ValueError) if name collides with built-in
- [x] 8.3 Implement `get_all_tools() -> list[ToolDefinition]` returning `_builtin_tools + list(_mcp_tools.values())` (built-in first, fixed order)
- [x] 8.4 Implement `get_tool(name)` looking up either bucket
- [x] 8.5 Implement `async execute_tool_call(call: ToolCall) -> ToolResult`: look up executor; if not found return error_result with message listing available tool names
- [x] 8.6 Keep module-level `_default = ToolRegistry()` singleton and re-export all module functions (`get_all_tools`, `get_tool`, `execute_tool_call`, ...) as thin wrappers around `_default` to preserve every existing import site
- [x] 8.7 Unit tests: existing 7 tools still discoverable; register_mcp_tool adds new tool; collision with built-in rejected; execute_tool_call routes correctly; old module API surface unchanged

## 9. App integration

- [x] 9.1 In `baozicode/app.py`, add `mcp_manager: McpClientManager | None` field; in `__init__`, after `permissions_bootstrap`, call `await mcp.bootstrap(config)` which returns the manager; manager's `get_all_tools()` is added to the built-in tool list when constructing `Agent`
- [x] 9.2 Modify the `current_agent()` path (or wherever `Agent(tools=...)` is constructed) to use `_default.get_all_tools()` so MCP tools are included
- [x] 9.3 Ensure `mcp_manager` is closed on app shutdown (`on_unmount` or app exit hook) — calls `disconnect()` on every still-connected session
- [x] 9.4 Add startup banner line(s) to `cli.py`: when MCP servers are configured, print a short summary (N connected, M failed, K broken) before TUI mounts; failed servers get a one-line warning each
- [x] 9.5 Integration test: end-to-end with a fake stdio server (in-process Python script) — App starts, manager connects, agent.run() can call the fake tool

## 10. TUI /mcp slash command

- [x] 10.1 In `baozicode/tui/chat_screen.py`, register `/mcp` command in the existing slash command dispatcher
- [x] 10.2 Implement `cmd_mcp(args: list[str])`: with no args, render a Rich table with columns `name`, `status`, `tools`, `error`; pull data from `self.app.mcp_manager.statuses`
- [x] 10.3 Implement `reconnect <name>` subcommand: parse args, call `await self.app.mcp_manager.reconnect(name)`, print status
- [x] 10.4 Implement `help` subcommand listing subcommands
- [x] 10.5 Unit tests: command dispatches to manager; reconnect updates displayed status

## 11. Testing infrastructure

- [x] 11.1 Create `tests/mcp/` directory with `conftest.py` providing a fixture `fake_stdio_server` that launches an in-process Python script implementing the four MCP handshake methods; another fixture `fake_http_server` using `aiohttp.web` running on `localhost:0`
- [x] 11.2 Test fixtures: a fake server that returns a known tools list; a fake server that delays `initialize`; a fake server that returns a tools/call error; a fake server that sends a server-initiated request mid-session
- [x] 11.3 Add `pytest-asyncio` configuration if not present (mode = auto, default fixture loop scope)

## 12. Documentation and example config

- [x] 12.1 Update `README.md` "MCP Integration" section (or create if missing) with config example showing both stdio and http servers; explain naming convention and permission rules
- [x] 12.2 Add `config.example.yaml` snippet for `mcp_servers:` block
- [x] 12.3 Update `CLAUDE.md` to mention the new `baozicode/mcp/` package and the modified `tools/registry.py` (now a class); update dependency diagram if needed
