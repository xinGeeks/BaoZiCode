## Context

BaoZiCode currently has 7 hard-coded tools registered via a module-level list in `baozicode/tools/registry.py`. The five-layer permissions system (`baozicode/permissions/`) already gates every `ToolCall` regardless of source, and `Agent._v5_executor` already routes any `ToolCall` through `execute_tool_call()` from the registry. The natural integration point is to make the registry dynamic — accepting tools registered at runtime — without disturbing the existing 7 tools or the Agent Loop contract.

The Model Context Protocol (spec 2025-11-25) defines a JSON-RPC 2.0 channel between client and server with two transport types (stdio and Streamable HTTP) and a three-step handshake (`initialize`, `notifications/initialized`, `tools/list`). `tools/call` is a regular JSON-RPC request invoked on demand.

Constraints from existing architecture:
- New code in `baozicode/mcp/` cannot import from `agent/`, `llm/`, or `tui/` (preserves dependency direction).
- `baozicode/mcp/` MAY import `config/` types and `tools/base.py` (matches `permissions/` precedent).
- `httpx` (≥0.27.0) is already in `pyproject.toml`; no new dependency.
- `${VAR}` substitution is already implemented recursively in `config/loader.py:_substitute_env` and must be reused, not duplicated.

## Goals / Non-Goals

**Goals:**
- Auto-discover tools from user-declared MCP servers at startup, register them under namespaced names, and make them available to the Agent with zero change to existing tool-call flow.
- Support both stdio (subprocess pipe) and Streamable HTTP transports per MCP spec.
- Make per-server failures non-fatal — one broken server must not break the App.
- Reuse every existing cross-cutting concern: five-layer permissions, PlanMode filtering, PromptBuilder rules injection, scheduler concurrency classification, ToolCall/ToolResult data model.
- Provide a `/mcp` TUI command for visibility and manual recovery.

**Non-Goals:**
- Implementing an MCP **server** (BaoZiCode is only a client).
- Supporting server-initiated requests (sampling, elicitation, roots). We respond with `Method not found` per spec but never act on them.
- Automatic reconnection on disconnect (only on explicit `/mcp reconnect`).
- Caching the tools list to disk — re-list on every reconnect.
- Custom transport types beyond stdio and Streamable HTTP.

## Decisions

### D1: Tool namespace prefix `mcp__<server>__<tool>`

**Decision:** All MCP tools are registered with the literal prefix `mcp__<server_name>__<tool_name>` (matching Anthropic's MCP naming convention).

**Rationale:** MCP allows servers to expose tools with any name. Two different servers might both expose `search`, and one server might even expose a tool whose name collides with a built-in (Read, Write, ...). The prefix is the only zero-conflict solution that preserves the LLM's ability to call any tool unambiguously. It also gives L3 permission rules a natural namespace (`mcp__github__*`) for fine-grained control.

**Alternatives considered:**
- *Keep raw names, reject on collision.* Breaks user expectations when two servers each legitimately expose `search`.
- *Keep raw names, first wins, second's tool dropped silently.* Surprising and hard to debug.
- *User-configured namespace per server.* Pushes the burden onto the user.

### D2: ToolDefinition field inference is conservative by default

**Decision:** `side_effect` defaults to `True`, `risk` defaults to `'high'`. Only `annotations.readOnlyHint = true` lowers them (`side_effect = False`, `risk = 'low'`). `path_args` is derived by scanning `inputSchema.properties` and collecting string-typed properties whose names match `(?i).*(path|file|dir|root).*`.

**Rationale:** MCP doesn't define `side_effect` or `risk`. Defaulting to the conservative value means: (a) the LLM cannot parallelize MCP tools against built-in tools without thinking twice; (b) the five-layer permissions engine denies-by-default for tools it doesn't recognize. Both behaviors are recoverable — the user can configure a different default in config — but erring safe is much cheaper than erring dangerous. `path_args` heuristic is best-effort: better to catch `file_path` and `directory` and miss `target_pathway` (rare) than to widen the regex and accidentally sweep up `query`, `pattern`, `prompt`.

**Alternatives considered:**
- *Default `side_effect = False`.* Premature concurrency for unknown write tools is a footgun.
- *User-configured per-server metadata.* Configuration overhead for every server a user tries.
- *Pass through MCP annotations verbatim.* The spec's `destructiveHint` and `openWorldHint` aren't a 1:1 map to our `side_effect`; manual mapping is cleaner.

### D3: `tools/registry.py` becomes a class with backward-compatible module API

**Decision:** Replace the module-level `_TOOLS` / `_BY_NAME` / `_EXECUTORS` constants with a `ToolRegistry` class. Keep a module-level `_default = ToolRegistry()` instance and keep the existing module functions (`get_all_tools`, `get_tool`, `execute_tool`, ...) as thin pass-throughs to `_default`. The MCP bootstrap calls `_default.register_mcp_tool(...)` and `_default.add_executor(...)`.

**Rationale:** All 12 existing call sites in `agent/`, `permissions/`, `prompt/`, `tui/` import from `baozicode.tools.registry` as a module. Rewriting them to take a registry parameter would ripple across the codebase. Keeping the module-level singleton preserves the call sites and adds a single explicit registration path for MCP. The class internals are mutable but the public surface is unchanged.

**Alternatives considered:**
- *Pass `ToolRegistry` through every constructor.* Cleaner long-term but larger diff.
- *Use a plugin entry-point system (e.g., `importlib.metadata.entry_points()`).* Too heavy for this use case.

### D4: JSON-RPC dispatcher via `asyncio.Future` per request id

**Decision:** Each transport maintains a `dict[int, asyncio.Future]` map. Sending a request writes the message, increments a monotonic `id` counter, registers a future, and `await`s it. The transport's read loop, running in its own asyncio task, parses each incoming frame and resolves the future whose key matches the frame's `id`. Frames with `id` not in the map are server-initiated requests/notifications (rejected with `Method not found` per spec) or unsolicited notifications (logged and ignored).

**Rationale:** `Future`-based pairing is the canonical pattern for asynchronous request/response in asyncio. It composes naturally with `async/await`, supports timeouts via `asyncio.wait_for`, and serializes concurrent in-flight calls without locks.

**Alternatives considered:**
- *Single response queue with header tag.* More fragile; out-of-order responses get misattributed.
- *Per-call socket/connection.* Way too expensive.

### D5: Stdio transport uses `asyncio.create_subprocess_exec` with `PIPE` for stdin/stdout, `DEVNULL`-not for stderr

**Decision:** Each stdio server is launched with `stdin=PIPE, stdout=PIPE, stderr=PIPE`. A dedicated `asyncio.Task` reads lines from stderr and forwards them to `logging.getLogger("baozicode.mcp.<server_name>")` at DEBUG. The main reader reads JSON-RPC frames from stdout.

**Rationale:** Discarding stderr (`stderr=DEVNULL`) is simple but loses debugging signal — server stack traces go nowhere. Reading stderr in a dedicated task with a logger makes them greppable without coupling to TUI panels. Critically, the task MUST keep draining or the pipe fills up and the subprocess blocks — a subtle bug that's hard to catch.

**Alternatives considered:**
- *Pass stderr to TUI panel.* Couples MCP to TUI and forces UI work for a backend concern.
- *Write to per-server log file.* Adds cleanup responsibility; logger is sufficient for now.

### D6: Streamable HTTP transport uses `httpx.AsyncClient.stream` and handles three response shapes

**Decision:** Every POST to a Streamable HTTP server sets `Accept: application/json, text/event-stream`. The response is dispatched by status + content-type:
- `202 Accepted` (no body) → notification/response accepted, nothing to do.
- `200 + Content-Type: application/json` → read JSON body, treat as a single JSON-RPC message.
- `200 + Content-Type: text/event-stream` → parse SSE event stream, each `data:` line is a JSON-RPC message.
- The session ID from the first response's `Mcp-Session-Id` header is cached and sent on every subsequent request.

**Rationale:** Per spec 2025-11-25. Servers may legitimately pick any of these three for any given request, so the client MUST handle all three. `httpx.AsyncClient.stream()` is already a dep and supports the streaming response shape. SSE parsing is small enough (~30 lines) to inline rather than pull a new dep.

**Alternatives considered:**
- *Always use SSE.* Doesn't help — server decides per request.
- *Pull in `sse-starlette` or `aiortc`.* New dep for ~30 lines of code.

### D7: Staged timeouts with per-server override

**Decision:** Three named timeouts: `init_timeout_s` (default 5), `tools_list_timeout_s` (default 8), `startup_total_timeout_s` (default 15). Each is a field on `McpServerConfig` so users can override per-server. A timeout is treated identically to a connection error — server marked failed, tools not registered.

**Rationale:** A single global timeout hides which phase is slow. Splitting into init/tools_list/total gives the user actionable info from logs (`init failed for server X after 5s`). Total cap prevents a single slow server from delaying the entire App startup. Per-server override covers weird cases (large filesystem servers that take 20s to list tools).

**Alternatives considered:**
- *Single 30s timeout.* Forgiving but bad UX when one server hangs and everything else waits.
- *No timeout.* Risk of indefinite hang on subprocess stdin pipe.

### D8: Disconnect → mark broken, manual reconnect only

**Decision:** When a server's read loop exits (subprocess returns, HTTP stream EOFs), the manager marks the server `broken`. Subsequent `tools/call` to any of its tools returns immediately with `ToolResult.is_error = True` and content `"MCP server '<name>' disconnected"`. The user invokes `/mcp reconnect <name>` to retry the handshake. No automatic background reconnection.

**Rationale:** Background reconnection adds threading, exponential backoff, race conditions, and a second source of truth for tool availability. Manual reconnect via slash command is explicit, debuggable, and never surprises the user with sudden tool reappearance. The cost is that a tool call in flight when the server dies returns an error rather than transparently retrying — acceptable for an interactive coding assistant.

**Alternatives considered:**
- *Background reconnect with backoff.* Powerful but adds a lot of moving parts for v0.6.
- *Synchronous reconnect on next call.* Freezes the Agent Loop for the duration of the new handshake — bad UX.

### D9: Reuse `_substitute_env` and existing two-layer merge pattern

**Decision:** `config/loader.py:load_config()` already has `_substitute_env(value)` that walks dict/list/str and replaces `${VAR}` recursively. We reuse it by calling it on the resolved `mcp_servers` dict before Pydantic validation. The two-layer merge (project > user) follows the same pattern as the rest of the config: read both, deep-merge, project wins on collision.

**Rationale:** Avoids re-implementing `${VAR}` semantics (one bug source instead of two). Reuses the same precedence rules users already know from the rest of the config. Keeps the code review surface small.

**Alternatives considered:**
- *Independent mcp_servers.yaml sidecar file.* Inspired by `permissions*.yaml` sidecars, but more files = more places to look. The main config is the canonical place; sidecars are reserved for the dynamic rules layer.
- *Separate file for users, separate for projects.* Splits the user's mental model.

## Risks / Trade-offs

- **[Risk] Subprocess stderr pipe blocks if not drained** → Dedicated `asyncio.Task` per stdio server, drains in a `while True: line = await proc.stderr.readline()` loop. Tested with a fake server that floods stderr.
- **[Risk] HTTP server assigns session ID after init only, not on reconnect** → We treat the first `initialize` response's `Mcp-Session-Id` as authoritative; if a server fails to send it, all subsequent requests omit the header (spec says servers MAY reject without it, in which case the user sees a clear error).
- **[Risk] MCP spec evolves (it has at least once a year)** → `protocolVersion` is sent on every init; if server rejects ours, log warning and continue with whatever version the server prefers. We don't pin to a specific version in code.
- **[Risk] `path_args` heuristic is too narrow or too wide** → Default is narrow (only `path|file|dir|root`). Users can extend with `path_args_override` field per server if needed (future enhancement, not in v0.6 scope).
- **[Risk] LLM prompt gets crowded with many MCP tools** → Namespaced names are longer than bare ones. With 5 servers × 10 tools, that's 50 tools each prefixed to ~25 chars = noticeable. Acceptable for v0.6; future enhancement: prompt-side filtering by tool description embedding similarity.
- **[Risk] Tool call timeout — a slow MCP tool hangs the Agent Loop** → Per-call timeout enforced via `asyncio.wait_for(call, timeout=call_timeout_s)` where `call_timeout_s` defaults to 60 and is configurable per-server. On timeout, return is_error with content `"MCP call to '<name>' timed out after Xs"`.
- **[Risk] Two servers registering tools in parallel racing on `_default._BY_NAME`** → Wrap `register_mcp_tool` in `asyncio.Lock` on the registry instance. Negligible contention in practice (registration only happens during bootstrap).
- **[Trade-off] Conservative `side_effect` default hurts performance** → PlanMode and LLM parallel-call both pessimistically treat MCP tools as serial. We accept this; v0.6 prioritizes safety over speed for unknown code.

## Migration Plan

No data migration. New config block is additive — existing `config.yaml` files without `mcp_servers:` are unaffected and load exactly as before.

Deployment sequence:
1. Merge PR with new `baozicode/mcp/` package + config + registry class + `/mcp` command.
2. Existing 7 tools continue to work identically.
3. Users opt in by adding `mcp_servers:` to their config.
4. Rollback = revert the PR; no schema migration needed.

## Open Questions

- *Should the JSON-RPC dispatcher handle batched requests (`jsonrpc: "2.0"` with an array of messages)?* Spec allows it; no observed server uses it. v0.6: send one-at-a-time, ignore incoming batches (log warning). v0.7+: revisit if a server needs it.
- *Should `tools/call` response include the tool's raw `content[]` somewhere (e.g., for multi-modal display)?* v0.6 renders text blocks only and tags the rest. Future TUI enhancement could add image rendering for MCP image blocks.
- *What happens if the LLM is given 50 MCP tools — is that too many for the context window?* v0.6 ships all. v0.7 may add per-server enable/disable slash command or a per-prompt tool filter.
