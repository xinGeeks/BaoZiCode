# agent-loop Specification (v1.1 deltas)

## Purpose
Modifications to the v1.0 Agent loop to integrate the v1.1 hooks lifecycle: pass a
`HookRegistry` into `Agent.__init__`, fire lifecycle events at session / turn / message /
tool boundary points, and replace `_v5_executor` with the v1.1 hook-aware pipeline
(`L1 → hook.pre → L2-L5 → execute → hook.post`). All existing v1.0 semantics (tool calls,
permissions, compaction, memory, sessions, instructions, commands, skills) remain
unchanged when no `hooks:` block is configured.

## ADDED Requirements

### Requirement: Agent accepts HookRegistry via constructor
The system MUST extend `Agent.__init__(...)` to accept an optional
`hook_registry: HookRegistry | None = None` keyword-only parameter. When `None`, the
Agent MUST behave identically to v1.0 — no hook events fire, no pipeline rewiring.
When provided, the Agent MUST use it as the source of hooks for the v1.1 lifecycle
event dispatch and the v1.1 `_v5_executor` pipeline.

The Agent MUST NOT import `baozicode.hooks` at module load time when no `hook_registry`
is provided (lazy import inside methods that need it), preserving v1.0 import
topology for users who never configure hooks.

#### Scenario: Agent without hook_registry preserves v1.0 behavior
- **WHEN** `Agent(...)` is constructed without `hook_registry`
- **THEN** `Agent.run(...)` produces the same `AgentEvent` stream as v1.0
- **AND** `_v5_executor` follows the v0.5 ordering (`L1 → L2 → L3 → L4 → L5 → execute`)
- **AND** no `baozicode.hooks` symbols are imported into the Agent module's globals

#### Scenario: Agent with hook_registry enables v1.1 pipeline
- **WHEN** `Agent(..., hook_registry=registry)` is constructed
- **THEN** lifecycle events (`session.start`, `turn.start`, `message.received`, etc.)
  fire at the corresponding boundary points in `Agent.run(...)`
- **AND** `_v5_executor` follows the v1.1 ordering (`L1 → hook.pre → L2-L5 → execute → hook.post`)
- **AND** `ToolResult.execution_status` is populated for every tool result the Agent
  yields to the TUI

### Requirement: Session lifecycle events fire at Agent.run boundaries
The Agent MUST fire the following lifecycle events at fixed points in `Agent.run(...)`:

| Event | When it fires |
|---|---|
| `session.start` | At the very top of `Agent.run(...)`, before any iteration |
| `session.end` | In the `finally` block of `Agent.run(...)`, after `done(...)` is yielded (always fires, even on exception or cancellation) |
| `turn.start` | At the top of every iteration (before LLM stream) |
| `turn.end` | At the bottom of every iteration, after tool execution completes (success, fail, deny, hook-pre-deny, or exception in the iteration body) |
| `message.received` | Before `conversation.add_user(...)` is called for a new user message |
| `message.sent` | After `conversation.add_turn(...)` for a completed assistant turn |

`session.start` MUST fire exactly once per `Agent.run(...)` call. `session.end` MUST
fire exactly once even if `Agent.run(...)` raises or is cancelled (use `try/finally`).
`turn.start` / `turn.end` MUST fire once per iteration.

#### Scenario: session.start fires once per run
- **WHEN** `Agent.run("hello")` is invoked and completes normally
- **THEN** exactly one `session.start` event is dispatched to registered hooks
- **AND** exactly one `session.end` event is dispatched in the finally block

#### Scenario: turn events bracket every iteration
- **WHEN** `Agent.run("do 3 steps")` causes the LLM to produce 4 iterations
- **THEN** 4 `turn.start` events and 4 `turn.end` events are dispatched
- **AND** the order is `turn.start[1], turn.end[1], turn.start[2], turn.end[2], ...`

#### Scenario: session.end fires on cancellation
- **WHEN** `Agent.run(...)` is interrupted by `USER_CANCELLED`
- **THEN** `session.end` still fires (in finally) before the `done` event is yielded to TUI

### Requirement: Tool lifecycle events wrap every tool_call attempt
The Agent MUST fire `tool.pre` immediately before invoking the v1.1 `_v5_executor`
pipeline (i.e., before L1 evaluation) and `tool.post` immediately after the pipeline
produces a `ToolResult` — regardless of which layer denied (L1, hook.pre, L2-L5) or
whether the tool ran successfully / failed / raised.

`tool.post` MUST fire inside a `try/finally` so it executes even if the tool
implementation itself raises an unexpected exception. The exception is converted to
`ToolResult(execution_status="executed_failed", is_error=True, content=str(exc))`
before `tool.post` sees it.

#### Scenario: tool.pre fires before every tool_call
- **WHEN** the LLM stream yields a `tool_use` for `Bash("ls")`
- **THEN** `tool.pre` fires with `ToolCall` as the payload
- **AND** the v1.1 pipeline runs (L1 → hook.pre → L2-L5 → execute → hook.post)

#### Scenario: tool.post fires after every pipeline outcome
- **WHEN** the pipeline produces a `ToolResult` (whether from L1 deny, hook.pre deny,
  L2-L5 deny, execute success, or execute failure)
- **THEN** `tool.post` fires with that `ToolResult` as the payload
- **AND** the result is yielded to TUI exactly once

#### Scenario: tool.post fires on tool exception
- **WHEN** a tool implementation raises `RuntimeError("boom")` mid-execution
- **THEN** the Agent converts the exception to a failed `ToolResult`
- **AND** `tool.post` still fires with that failed result
- **AND** the exception does NOT propagate out of `_v5_executor`

### Requirement: System events fire but accept no actions in v1.1
The Agent MUST fire the following system events at their natural boundaries; registered
hooks MAY match on these events, but the v1.1 dispatcher MUST accept hooks for them
and run their `actions` exactly like any other event:

| Event | When it fires |
|---|---|
| `system.error` | When `Agent.run(...)` is about to raise an unhandled exception, before the exception propagates |
| `system.compaction` | When v0.7 compaction runs (before offload + summary) |
| `system.cancel` | When `USER_CANCELLED` is set, before the loop exits |

`system.error` and `system.compaction` events carry a payload (exception or compaction
stats respectively); `system.cancel` carries the cancellation reason.

#### Scenario: system.compaction fires before compaction runs
- **WHEN** Layer-2 compaction triggers in `Agent.run(...)`
- **THEN** `system.compaction` fires with the compaction-stats payload
- **AND** registered hooks run their actions
- **AND** the compaction itself proceeds normally regardless of hook outcomes (hooks
  are observers here, not interceptors)

#### Scenario: system.cancel fires before exit
- **WHEN** the user presses Esc and `USER_CANCELLED` is set
- **THEN** `system.cancel` fires with `reason="user_interrupt"` payload
- **AND** the loop then yields `done(reason=USER_CANCELLED)` and exits

### Requirement: Hook failures do not break Agent loop
The Agent MUST wrap every `hook_dispatcher.run(event, payload)` call in a `try/except`
that catches `asyncio.TimeoutError` and any other `Exception`. On exception, the Agent
MUST log a `WARNING` and continue as if the hook returned "no deny / no side-effect".

The Agent MUST NOT swallow exceptions raised by tool implementations — those are
converted to failed `ToolResult` and exposed via `tool.post` (see "tool.post fires on
tool exception"). Only hook-dispatcher-level failures are silenced.

#### Scenario: hook.pre timeout does not stall tool_call
- **WHEN** a `tool.pre` hook exceeds its `timeout` (default 30s)
- **THEN** `asyncio.TimeoutError` is caught
- **AND** a `WARNING` is logged naming the hook id and event
- **AND** the pipeline proceeds as if the hook allowed the call

#### Scenario: hook.pre exception does not deny tool_call
- **WHEN** a `tool.pre` hook raises `ValueError` while evaluating its condition
- **THEN** the exception is caught and logged as WARNING
- **AND** the pipeline proceeds as if the hook allowed the call
- **AND** `tool.post` still fires after execution

### Requirement: Agent pipeline is L1 → hook.pre → L2-L5 → execute → hook.post
The Agent's `_v5_executor(call) -> ToolResult` MUST follow this exact order:

1. **L1** — call `permissions.blacklist.check(call)`. If denied, return `ToolResult(execution_status="block_l1", denied_by="l1_blacklist", is_error=True, content=L1_reason)` and skip steps 2-5 of pipeline (still fire hook.post in step 5).
2. **hook.pre** — call `hook_dispatcher.run("tool.pre", call)`. If any hook denies, return `ToolResult(execution_status="block_hook_pre", denied_by="hook_pre", denied_hook_id=<first-deny-id>, is_error=True, content=hook_reason)`. Skip steps 3-4, still fire hook.post.
3. **L2-L5** — call `permissions.check(call, merged_permissions)` (note: this re-runs L1 internally — the result is consistent; if L1 fires again, it produces `block_permission` with `denied_by="l2_l5_permission"`, which is acceptable). If denied, return `ToolResult(execution_status="block_permission", denied_by="l2_l5_permission", is_error=True, content=perm_reason)`. Skip step 4, still fire hook.post.
4. **execute** — invoke the tool. On success, return `ToolResult(execution_status="executed_success", is_error=False, content=<output>)`. On tool error or exception, return `ToolResult(execution_status="executed_failed", is_error=True, content=<error>)`.
5. **hook.post** — fire on every ToolResult produced by steps 1-4, via `try/finally`. The hook.post call MUST NOT be inside the same try as execute — it wraps the entire pipeline.

#### Scenario: L1 deny bypasses hook.pre
- **WHEN** L1 denies `Bash("rm -rf /")` and 3 `tool.pre` hooks are registered
- **THEN** the returned ToolResult has `execution_status="block_l1"`, `denied_by="l1_blacklist"`
- **AND** NO `tool.pre` hooks fire (step 2 is skipped)
- **AND** `tool.post` still fires on the L1-denied result (step 5)

#### Scenario: hook.pre deny bypasses L2-L5
- **WHEN** L1 allows and a `tool.pre` hook denies `Bash("chmod 777 /tmp/x")`
- **THEN** the returned ToolResult has `execution_status="block_hook_pre"`, `denied_by="hook_pre"`, `denied_hook_id=<hook.id>`
- **AND** L2-L5 is NOT evaluated
- **AND** `tool.post` fires on this hook-pre-denied result

#### Scenario: hook.post fires exactly once per tool_call attempt
- **WHEN** any tool_call attempt completes (L1/hook.pre/L2-L5 deny, success, failure, exception)
- **THEN** `tool.post` fires exactly once for that attempt
- **AND** the payload is the final `ToolResult` (with execution_status set)

### Requirement: HookDispatcher is dependency-injected into Agent
The Agent MUST obtain a `HookDispatcher` instance from `hook_registry.create_dispatcher()`
at construction time. The Agent MUST NOT instantiate hooks or call into the `baozicode.hooks`
package directly — all hook execution goes through the dispatcher's `run(event, payload)`
method.

This separation keeps `agent/` from depending on hook internals: if v1.2 changes the
dispatcher implementation, the Agent signature stays stable.

#### Scenario: Agent uses injected dispatcher
- **WHEN** `Agent(..., hook_registry=registry)` is constructed
- **THEN** `Agent._hook_dispatcher` is `registry.create_dispatcher()`
- **AND** `Agent.run(...)` calls `self._hook_dispatcher.run(event, payload)` at every
  lifecycle boundary point
- **AND** no `from baozicode.hooks import ...` appears in `agent/loop.py` at module scope