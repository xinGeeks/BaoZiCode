## ADDED Requirements

### Requirement: System compaction event fires on context compaction
The system MUST fire `system.compaction` at the entry point of `maybe_compact()`,
BEFORE the actual compression work (Layer 1 offload or Layer 2 summarization) begins.
The event payload MUST include `trigger` (`auto` or `manual`) and `tokens_before` (int).

If no hook dispatcher is configured (`hook_dispatcher is None`), the fire MUST be skipped
(v1.0 / v1.1 backward compatibility — projects without `hooks:` block MUST NOT pay any
fire overhead).

The fire MUST be wrapped in try/except — any hook exception MUST be logged at WARNING
level and MUST NOT abort the compaction flow (fail-open).

#### Scenario: Compaction fire before summarization
- **WHEN** `maybe_compact(messages, trigger="manual", ctx)` is called
- **AND** `ctx.hook_dispatcher` is configured with a `system.compaction` hook
- **THEN** that hook fires BEFORE Layer 2 summarization begins
- **AND** the hook payload contains `trigger="manual"` and `tokens_before=<length of messages>`

#### Scenario: Compaction fire skipped when no dispatcher
- **WHEN** `maybe_compact(...)` is called with `ctx.hook_dispatcher = None`
- **THEN** no fire happens
- **AND** the compaction proceeds normally

#### Scenario: Hook exception does not abort compaction
- **WHEN** a `system.compaction` hook raises an exception
- **THEN** a WARNING is logged
- **AND** `maybe_compact` continues normally and returns the compacted messages

### Requirement: System cancel event fires on user cancel
The system MUST fire `system.cancel` when `Agent.run` is about to exit with
`StopReason.USER_CANCELLED`. The event payload MUST include `reason: "user_cancelled"`
and `iteration: int` (the current 1-indexed iteration).

The fire MUST happen BEFORE the `done` AgentEvent is yielded so that hooks can perform
cleanup (close in-flight HTTP connections, write final log lines, etc.).

If `hook_dispatcher is None`, the fire MUST be skipped.

The fire MUST be wrapped in try/except — fail-open.

#### Scenario: Cancel fire on USER_CANCELLED
- **WHEN** `Agent.run` is mid-iteration
- **AND** `agent._cancel_event` is set (USER_CANCELLED detected)
- **AND** `hook_dispatcher` is configured with a `system.cancel` hook
- **THEN** that hook fires with payload `{"reason": "user_cancelled", "iteration": N}`
- **AND** the Agent proceeds to yield the `done` AgentEvent with `StopReason.USER_CANCELLED`

#### Scenario: Cancel fire skipped when no dispatcher
- **WHEN** `Agent.run` exits with USER_CANCELLED and `hook_dispatcher is None`
- **THEN** no fire happens (no-op)

#### Scenario: Hook exception does not block done event
- **WHEN** a `system.cancel` hook raises an exception
- **THEN** a WARNING is logged
- **AND** the Agent still yields `done` with USER_CANCELLED

### Requirement: Clear-control hook actions (clear_sticky_reminders / clear_stable_system_overrides)
The system MUST support two control-kind hook actions that wipe specific runtime hook
state on the Agent, each scoped to exactly one state kind:

| action kind | clears | does NOT touch |
|---|---|---|
| `clear_sticky_reminders` | `Agent._pending_reminders` (sticky `hook_prompt` reminder queue) | `_hook_stable_overrides`, `_temp_reminders` |
| `clear_stable_system_overrides` | `Agent._hook_stable_overrides` (the `## Hook Overrides` appended section) | `_pending_reminders`, `_temp_reminders` |

These actions take NO other fields (`clear_sticky_reminders` has zero required/optional
fields beyond `action`; same for `clear_stable_system_overrides`). They MUST NOT have
`deny` / `deny_reason` / `parse_expr` (they are control actions, not deny-capable).

The clear MUST be synchronous — by the time the action returns, the targeted state
is empty.

These actions MUST NOT touch `Agent._temp_reminders` — temp reminders are turn-scoped
by design and are cleared automatically by `_inject_reminders` after consumption.

#### Scenario: clear_sticky_reminders wipes pending reminders only
- **WHEN** the Agent has `_pending_reminders = [m1, m2]`, `_hook_stable_overrides = ["x"]`,
  `_temp_reminders = ["t"]`
- **AND** a `tool.post` hook fires `clear_sticky_reminders`
- **THEN** `Agent._pending_reminders == []`
- **AND** `Agent._hook_stable_overrides == ["x"]` (unchanged)
- **AND** `Agent._temp_reminders == ["t"]` (unchanged)

#### Scenario: clear_stable_system_overrides wipes overrides only
- **WHEN** the Agent has `_pending_reminders = [m1]`, `_hook_stable_overrides = ["x", "y"]`,
  `_temp_reminders = ["t"]`
- **AND** a `turn.start` hook fires `clear_stable_system_overrides`
- **THEN** `Agent._hook_stable_overrides == []`
- **AND** `Agent._pending_reminders == [m1]` (unchanged)
- **AND** `Agent._temp_reminders == ["t"]` (unchanged)

#### Scenario: Clear actions do not have deny capability
- **WHEN** a rule has `actions: [{action: clear_sticky_reminders, deny: true}]`
- **THEN** startup rejects the rule with a clear error (extra `deny` field forbidden)

#### Scenario: Clear actions safe on missing agent
- **WHEN** a `clear_sticky_reminders` action runs with `ctx.agent is None`
- **THEN** a WARNING is logged
- **AND** the action returns `ActionResult(deny=False)` (no exception)

### Requirement: ToolResult rendering colors per execution_status
The TUI `ToolResultCard` widget MUST render ToolResults with distinct visual
indicators based on `ToolResult.execution_status`:

| execution_status | visual indicator | semantic |
|---|---|---|
| `block_l1` | red (`-block-l1` CSS class) | L1 hard blacklist denial |
| `block_hook_pre` | yellow (`-block-hook-pre` CSS class) | hook.pre denial |
| `block_permission` | orange (`-block-permission` CSS class) | L2-L5 permission denial |
| `executed_success` | green (`-executed-success` CSS class) | tool ran successfully |
| `executed_failed` | red (`-executed-failed` CSS class) | tool itself raised |
| `None` (backward compat) | default class (no special styling) | v1.0-era ToolResult without `execution_status` |

The CSS class MUST be applied as a widget class so the styling is purely
presentational and does not affect data flow. The `is_error` field continues to be
the source of truth for whether the result is an error (semantics unchanged).

#### Scenario: L1 denial renders red
- **WHEN** a `ToolResult(execution_status="block_l1", denied_by="l1_blacklist")` is
  rendered as `ToolResultCard`
- **THEN** the card's CSS classes include `-block-l1`
- **AND** it does NOT include `-block-hook-pre` or `-block-permission`

#### Scenario: hook.pre denial renders yellow
- **WHEN** a `ToolResult(execution_status="block_hook_pre", denied_hook_id="audit-risky")`
  is rendered
- **THEN** the card's CSS classes include `-block-hook-pre`

#### Scenario: Successful execution renders green
- **WHEN** a `ToolResult(execution_status="executed_success", is_error=False)` is rendered
- **THEN** the card's CSS classes include `-executed-success`

#### Scenario: Failed execution renders red (distinct class from L1)
- **WHEN** a `ToolResult(execution_status="executed_failed", is_error=True)` is rendered
- **THEN** the card's CSS classes include `-executed-failed`
- **AND** it does NOT include `-block-l1` (different semantic, different class)

#### Scenario: Backward-compat — no execution_status renders with default class
- **WHEN** a `ToolResult(tool_call_id="x", content="y", is_error=False)` is rendered
  (v1.0-style construction without `execution_status`)
- **THEN** the card's CSS classes do NOT include any `-block-*` or `-executed-*` class
- **AND** it falls back to the default rendering style