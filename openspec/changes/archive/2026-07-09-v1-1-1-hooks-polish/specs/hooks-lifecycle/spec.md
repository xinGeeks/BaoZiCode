## ADDED Requirements

### Requirement: Prompt slot delivery (sticky_reminder / stable_system / temp)
The system MUST support three delivery slots for `prompt` actions, each with distinct
lifecycle semantics:

| slot | delivery | persistence | consumer |
|---|---|---|---|
| `sticky_reminder` (default) | `Agent.enqueue_reminder(kind="hook_prompt", body, ttl="sticky")` | survives across turns until `/clear` or Agent restart | `_inject_reminders` injects `<system-reminder type="hook_prompt">` into `messages[-2]` each turn |
| `stable_system` | `Agent.set_dynamic_section("hook_overrides", content)` | survives across turns until `/clear` or Agent restart | appended to `BuiltPrompt.stable_system` after the byte-identical prefix; preserves LLM cache key |
| `temp` | append to `Agent._temp_reminders` list | consumed exactly once at the next `_inject_reminders` call, then cleared | injected as a `ttl="once"` `<system-reminder type="hook_prompt">` into the next turn only |

`stable_system` MUST be rejected at startup when used with `event: tool.pre` / `tool.post`
(the timing is wrong for prompt injection). `temp` MUST be allowed for all events.

When `enqueue: false` is set on a `sticky_reminder` action, the system MUST skip
`enqueue_reminder` and only log the body at INFO level.

#### Scenario: sticky_reminder survives across turns
- **WHEN** a `tool.post` hook fires a prompt action with `slot: sticky_reminder`
- **AND** the Agent enters turn N+1
- **THEN** `<system-reminder type="hook_prompt">` with the action body appears in
  `messages[-2]` for turn N+1
- **AND** the reminder continues to appear in turn N+2, N+3, ... until `/clear`

#### Scenario: stable_system appended after byte-identical prefix
- **WHEN** a `turn.start` hook fires a prompt action with `slot: stable_system` and content "use TypeScript"
- **THEN** `Agent._hook_stable_overrides` contains `"use TypeScript"`
- **AND** `BuiltPrompt.stable_system` ends with `\n\n## Hook Overrides\nuse TypeScript`
- **AND** the byte-identical prefix (everything before `## Hook Overrides`) is unchanged
  so LLM prompt caching still hits

#### Scenario: temp consumed once and cleared
- **WHEN** a `turn.start` hook fires a prompt action with `slot: temp` and content "记得 review 一遍"
- **THEN** `Agent._temp_reminders` contains `["记得 review 一遍"]`
- **AND** at the next `_inject_reminders` call, that body is injected once
- **AND** `Agent._temp_reminders` is empty immediately after that injection

#### Scenario: enqueue false on sticky_reminder skips enqueue
- **WHEN** a `tool.post` hook fires a prompt action with `slot: sticky_reminder` and `enqueue: false`
- **THEN** `Agent.enqueue_reminder` is NOT called
- **AND** `Agent._pending_reminders` is NOT extended
- **AND** the body is logged at INFO level

### Requirement: Hook state isolation from /clear
The system MUST ensure that issuing `/clear` resets all hook-injected state so that a new
conversation starts with a clean baseline. Specifically, after `/clear` completes the Agent
MUST have:

- `Agent._pending_reminders == []` (no sticky `hook_prompt` reminders from prior turn)
- `Agent._hook_stable_overrides == []` (no `## Hook Overrides` content appended to stable_system)
- `Agent._temp_reminders == []` (no turn-scoped reminders pending)
- `BuiltPrompt.stable_system` rebuilt WITHOUT any `## Hook Overrides` section

The clear MUST be synchronous — the Agent state must be empty before the next user
message is processed.

Hook definitions in `config.yaml: hooks:` MUST NOT be cleared by `/clear`; only the
runtime-injected state is wiped.

#### Scenario: sticky hook_prompt wiped on /clear
- **WHEN** the Agent has injected a sticky `hook_prompt` reminder via `enqueue_reminder`
  (i.e., `Agent._pending_reminders` is non-empty)
- **AND** the user issues `/clear`
- **THEN** after `_clear_conversation` returns, `Agent._pending_reminders == []`
- **AND** the next turn does NOT inject the prior hook_prompt reminder

#### Scenario: hook_overrides wiped on /clear
- **WHEN** the Agent has accumulated `## Hook Overrides` content via `set_dynamic_section`
  (i.e., `Agent._hook_stable_overrides` is non-empty)
- **AND** the user issues `/clear`
- **THEN** after `_clear_conversation` returns, `Agent._hook_stable_overrides == []`
- **AND** `BuiltPrompt.stable_system` no longer contains `## Hook Overrides`

#### Scenario: temp_reminders wiped on /clear
- **WHEN** the Agent has pending `_temp_reminders` from a prior hook fire
- **AND** the user issues `/clear`
- **THEN** after `_clear_conversation` returns, `Agent._temp_reminders == []`

#### Scenario: hook definitions preserved on /clear
- **WHEN** `config.yaml: hooks:` has 3 hook rules registered
- **AND** the user issues `/clear`
- **THEN** the registry still has 3 hook rules
- **AND** the next tool_call in the new conversation still triggers `tool.pre` /
  `tool.post` hooks as configured

### Requirement: Audit log per-session path
The system MUST write the audit log to a per-session file at
`<project_root>/.baozicode/hooks/<session_id>.audit.jsonl`. The `<session_id>` MUST match
the SessionArchiver session id format (`YYYYMMDD-HHMMSS-xxxx`, 20 chars).

Each line MUST be a valid JSON object terminated by `\n` (JSONL format). The parent
directory `<project_root>/.baozicode/hooks/` MUST be auto-created on first write.

The system MUST rotate the audit log when its size exceeds 100 MB (configurable via
`HookAuditLog(max_bytes=...)`). On rotation, the current file MUST be renamed to
`<original>.YYYYMMDD-HHMMSS` and a fresh empty file created.

Audit write failures MUST be logged at WARNING level and MUST NOT be raised into the
Agent loop (fail-open).

#### Scenario: Audit log path matches session id
- **WHEN** the Agent runs with session id `20260709-143022-a7b3`
- **AND** a `tool.pre` hook fires
- **THEN** the audit log is appended to `<project_root>/.baozicode/hooks/20260709-143022-a7b3.audit.jsonl`
- **AND** each line is a JSON object terminated by `\n`

#### Scenario: Audit log rotates at 100 MB
- **WHEN** `<session_id>.audit.jsonl` reaches 100 MB
- **AND** the next hook invocation triggers a rotation check
- **THEN** the existing file is renamed to `<session_id>.audit.jsonl.YYYYMMDD-HHMMSS`
- **AND** a fresh empty `<session_id>.audit.jsonl` is created
- **AND** subsequent writes append to the new file

#### Scenario: Audit log parent dir auto-created
- **WHEN** `<project_root>/.baozicode/hooks/` does not exist
- **AND** a hook invocation triggers audit write
- **THEN** the directory is auto-created
- **AND** the audit line is appended successfully

#### Scenario: Audit write failure is fail-open
- **WHEN** the audit log write fails (e.g., disk full, permission denied)
- **THEN** a WARNING is logged
- **AND** the Agent loop continues without raising