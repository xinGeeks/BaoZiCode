# prompt-modular Specification (v1.1 deltas)

## Purpose
Adds support for v1.1 hook-prompt reminder injection into the modular prompt pipeline.
The `prompt` action of a hook injects text into either the stable system segment
(cached), the sticky reminder slot, or the per-turn temp slot. This document specifies
the integration point in `prompt/reminder.py` and `prompt/sections/`.

## ADDED Requirements

### Requirement: Reminder type `hook_prompt` is supported by `_inject_reminders`
The system MUST extend `_inject_reminders(messages, iteration, agent_state)` (or
equivalent v1.0 entry point) to inject reminders of `type="hook_prompt"` in addition
to the existing v0.7 types (`env`, `plan_mode`, `denial_rate_limit`) and v0.8 types
(`time_gap`, `memory_refreshed`).

`hook_prompt` reminders MUST have `ttl="sticky"` by default, persisting across turns
until the conversation is cleared or a `clear_sticky_reminders` action is invoked.
The body MUST be wrapped in `<system-reminder type="hook_prompt" ttl="sticky">`
tags and spliced into `messages[-2]` (between the second-to-last and last user-role
messages), preserving the user-message-last invariant.

#### Scenario: hook_prompt reminder appears in messages[-2]
- **WHEN** a hook's `action: prompt` runs and enqueues a sticky reminder with body
  "本项目代码风格:PEP 8 + 100 字符行宽"
- **THEN** on the next `Agent.run()` iteration, `_inject_reminders` includes:
  ```
  <system-reminder type="hook_prompt" ttl="sticky">
  本项目代码风格:PEP 8 + 100 字符行宽
  </system-reminder>
  ```
- **AND** this reminder appears at `messages[-2]` (before the most recent user message)

#### Scenario: Multiple hook_prompt reminders concatenate
- **WHEN** 3 hooks enqueue sticky reminders with bodies A, B, C
- **THEN** `_inject_reminders` splices all 3 reminders into `messages[-2]` in
  enqueue order
- **AND** each reminder is independently wrapped in `<system-reminder>` tags

#### Scenario: sticky reminder survives across turns
- **WHEN** a sticky hook_prompt reminder is enqueued at turn 1
- **AND** the Agent runs turns 1, 2, 3 without clearing it
- **THEN** the reminder is present in `messages[-2]` for all 3 turns

### Requirement: prompt action slot override controls where reminder goes
The system MUST support 3 slot values for the `action: prompt` slot field, with the
following injection points:

| Slot | Injection point | Persistence | Default? |
|---|---|---|---|
| `sticky_reminder` | `messages[-2]` via `_inject_reminders` | Until cleared or session ends | Yes (default) |
| `stable_system` | `BuiltPrompt.stable_system` segment, before all 11 sections | Until `clear_stable_system_overrides` or app restart | No (opt-in) |
| `temp` | Current turn's assistant message context (one-shot) | Current turn only | No (opt-in) |

`stable_system` injection MUST use the existing `set_dynamic_section` mechanism (or
equivalent) so the change does not break the cache-byte-identical guarantee of the
rest of `stable_system` — the override is added AFTER the byte-identical prefix, and
the cache key reflects this addition explicitly.

#### Scenario: default slot is sticky_reminder
- **WHEN** a hook has `action: prompt` with no `slot` field specified
- **THEN** the system uses `slot="sticky_reminder"`
- **AND** the prompt body is enqueued as a sticky reminder

#### Scenario: stable_system slot persists across cache window
- **WHEN** a hook has `action: prompt` with `slot: stable_system`
- **THEN** the prompt body is injected into `BuiltPrompt.stable_system`
- **AND** it appears in every subsequent `llm.stream()` call's system parameter
- **AND** it is byte-identical across turns (LLM cache hit rate preserved)
- **UNTIL** a `clear_stable_system_overrides` action is invoked

#### Scenario: temp slot is one-shot
- **WHEN** a hook has `action: prompt` with `slot: temp`
- **THEN** the prompt body is injected only into the current turn's assistant context
- **AND** it does NOT appear in subsequent turns
- **AND** it does NOT affect the stable system cache

### Requirement: stable_system slot forbidden on tool.* events
The system MUST reject any hook config where `event ∈ {tool.pre, tool.post}` AND
`action: prompt` AND `slot: stable_system`. The rationale is that tool events fire
inside an iteration, after `stable_system` was already used to start the LLM stream —
injecting into `stable_system` mid-iteration is meaningless AND would invalidate the
LLM cache by mutating the byte-identical prefix.

Validation happens in `HookRegistry.freeze()`; the offending hook is named in the
startup error.

#### Scenario: tool.pre + stable_system rejected at freeze time
- **WHEN** a hook has `event: tool.pre`, `actions: [{action: prompt, slot: stable_system, content: "x"}]`
- **THEN** `HookRegistry.freeze()` raises `HookValidationError`:
  `hooks[<id>]: slot=stable_system not allowed for tool.pre events`
- **AND** startup exits with non-zero status

#### Scenario: session.start + stable_system accepted
- **WHEN** a hook has `event: session.start`, `actions: [{action: prompt, slot: stable_system, content: "x"}]`
- **THEN** `HookRegistry.freeze()` accepts it (session.start fires once at Agent.run
  entry, before the first LLM stream — safe to mutate stable_system)
- **AND** the prompt body is set via `set_dynamic_section("hook_overrides", ...)`
  before the first LLM call

### Requirement: enqueue_reminder is the public injection entry point
The system MUST expose `Agent.enqueue_reminder(kind: Literal["hook_prompt"], body: str,
ttl: Literal["sticky", "once"] = "sticky")` as a public method on the `Agent` class.
The hook dispatcher calls this method after a `prompt` action runs.

The system MUST thread `agent` reference into the hook dispatcher via the `HookRegistry`
construction (registry holds a back-reference to the Agent for reminder enqueue only —
no other Agent state is exposed to hooks).

#### Scenario: prompt action enqueues sticky reminder
- **WHEN** a `tool.post` hook has `action: prompt` with body "audit complete"
- **AND** `enqueue: true` is set on the hook OR the action runs synchronously
- **THEN** `Agent.enqueue_reminder(kind="hook_prompt", body="audit complete", ttl="sticky")` is called
- **AND** the reminder appears in `_inject_reminders` for the next iteration

#### Scenario: enqueue default behavior for async post hooks
- **WHEN** an async post hook has `action: prompt` and `enqueue: false` (the default)
- **THEN** the prompt body is logged via `log.warning("hook_prompt: %s", body)`
- **AND** `Agent.enqueue_reminder` is NOT called
- **AND** the prompt body does NOT appear in any future LLM context

### Requirement: hook_prompt reminders can be cleared
The system MUST support clearing sticky hook_prompt reminders via either:
1. `/clear` slash command — clears all sticky reminders (existing v0.8 behavior, now
   extended to include `hook_prompt` type).
2. A `clear_sticky_reminders` hook action — clears all sticky reminders (any kind) at
   the next `Agent._inject_reminders` call.

Stable_system slot overrides MUST persist across `/clear` (they are part of the
cached prefix, not a reminder); only the dedicated `clear_stable_system_overrides`
action clears them.

#### Scenario: /clear wipes hook_prompt sticky reminders
- **WHEN** the user runs `/clear` and 3 sticky `hook_prompt` reminders are present
- **THEN** all 3 are removed from the agent state
- **AND** the next `Agent.run(...)` iteration does NOT include them in messages[-2]

#### Scenario: clear_sticky_reminders action wipes all sticky reminders
- **WHEN** a hook has `action: clear_sticky_reminders` and fires on `turn.start`
- **THEN** all sticky reminders (any kind: env / plan_mode / denial_rate_limit /
  memory_refreshed / hook_prompt) are removed from agent state at the top of the
  next turn
- **AND** stable_system overrides are NOT affected

#### Scenario: stable_system overrides survive /clear
- **WHEN** a hook has set a `stable_system` slot override and the user runs `/clear`
- **THEN** the stable_system override is still in effect
- **AND** only `clear_stable_system_overrides` action (or app restart) clears it