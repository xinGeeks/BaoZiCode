# hooks-lifecycle Specification

## Purpose

Define the v1.1 hooks system: declarative YAML rules of `event + if + actions` triggered at
specific Agent lifecycle points, supporting four action types (shell / prompt / http /
sub-agent) with hybrid deny semantics. Hooks let users automate repetitive Agent-lifecycle
work (audit logging, pre-flight checks, context injection, sub-Agent dispatch) without
manually monitoring each tool call.
## Requirements
### Requirement: HookRule schema (event + if + actions)
The system MUST accept a list of hook rules from `config.yaml: hooks:`. Each rule MUST be a
dict with three required top-level keys: `event` (one of the event names listed in the
"Event taxonomy" requirement), `if` (a condition expression; omit to mean unconditional), and
`actions` (a non-empty list of action specs evaluated in order). The system MUST validate each
rule against a Pydantic schema at startup; any schema violation MUST cause `SystemExit` with
a message naming the offending rule's `id` and field.

#### Scenario: Valid rule loads
- **WHEN** `config.yaml` contains a hook rule with `id: "audit-bash"`, `event: "tool.pre"`,
  `if: {all: [{tool: "Bash"}]}`, `actions: [{action: shell, command: "echo ok"}]`
- **THEN** the rule loads successfully and is registered for `tool.pre` events

#### Scenario: Missing actions list rejected
- **WHEN** a rule omits `actions` or has an empty `actions` list
- **THEN** startup emits a clear validation error naming the rule id and the missing field
- **AND** the system exits with non-zero status

#### Scenario: Unknown event name rejected
- **WHEN** a rule has `event: "tool.preflight"` (not in the event taxonomy)
- **THEN** startup rejects the rule with a clear error
- **AND** the system does not start

### Requirement: Event taxonomy (4 layers + system)
The system MUST recognize these event names and trigger registered hooks for each:

| Layer | Event | When fired |
|---|---|---|
| session | `session.start` | Agent.run() entry, after `_conversation.add_user` |
| session | `session.end` | Agent.run() exit, before `done` event yields |
| turn | `turn.start` | Top of each iteration, before `_inject_reminders` |
| turn | `turn.end` | End of each iteration, after tool_result is appended |
| message | `message.received` | After `_conversation.add_user` |
| message | `message.sent` | After `_conversation.add_turn` (assistant message) |
| tool | `tool.pre` | Before `_v5_executor` enters permission pipeline |
| tool | `tool.post` | After ToolResult is produced, before `add_tool_result` |
| system | `system.error` | When Agent yields an `error` event |
| system | `system.compaction` | When `maybe_compact` triggers (start or end) |
| system | `system.cancel` | When user requests cancel |

`system.compaction` and `system.cancel` are reserved in this version — the system MUST
recognize them in the event taxonomy but MUST NOT yet execute actions for them (no-op).

#### Scenario: tool.pre fires before permission check
- **WHEN** the Agent loop is about to invoke `_v5_executor(call)`
- **THEN** all registered `tool.pre` hooks run BEFORE any L1 / hook.pre / L2-L5 layer
- **AND** the first denying hook's `ToolResult` is returned to the caller without invoking
  L2-L5 or `execute_tool_call`

#### Scenario: tool.post fires on every tool_call attempt
- **WHEN** any tool_call attempt completes (whether denied by L1, denied by hook.pre,
  denied by L2-L5, executed successfully, or executed with error)
- **THEN** all registered `tool.post` hooks run
- **AND** the hook sees the final `ToolResult` (with `execution_status`, `denied_by`, etc.
  populated)

#### Scenario: session.start fires once per Agent.run
- **WHEN** `agent.run(user_message)` is called
- **THEN** exactly one `session.start` event fires (before any turn.start or message.received)
- **AND** `session.end` fires exactly once before `done`

#### Scenario: turn.start and turn.end pair per iteration
- **WHEN** the Agent loop enters iteration N (1-indexed)
- **THEN** `turn.start` fires at the top of iteration N
- **AND** `turn.end` fires at the bottom of iteration N
- **AND** both fire even if iteration ends with `USER_CANCELLED` or `STREAM_ERROR`

### Requirement: Condition syntax (4 matchers + all/any composition)
The system MUST support these matchers in `if:` conditions:

| Matcher key | Match semantics |
|---|---|
| `tool: "X"` | Exact match: `ToolCall.name == "X"` (no wildcards) |
| `arg.<name>: glob "<pattern>"` | `fnmatch.fnmatch(str(ToolCall.arguments[<name>]), "<pattern>")` |
| `arg.<name>: regex "<pattern>"` | `re.fullmatch(str(ToolCall.arguments[<name>]), "<pattern>")` |
| `arg.<name>: not_match "<value>"` | Inverse of exact equality |
| `arg.<name>: not_glob "<pattern>"` | Inverse of glob match |
| `arg.<name>: not_regex "<pattern>"` | Inverse of regex match |

The system MUST support `if.all: [cond1, cond2, ...]` (ALL must hold) and `if.any: [cond1,
cond2, ...]` (ANY must hold). The system MUST reject a rule whose `if` contains BOTH `all`
and `if.any` keys simultaneously (configuration error → SystemExit).

Omitting `if` MUST be treated as unconditional (always matches).

#### Scenario: Exact tool match
- **WHEN** `if: {tool: "Bash"}`
- **AND** the tool call is `Bash(command="ls")`
- **THEN** the condition matches

#### Scenario: Glob on argument
- **WHEN** `if: {arg.command: glob "git *"}`
- **AND** the tool call is `Bash(command="git status")`
- **THEN** the condition matches
- **AND** for `Bash(command="npm install")` the condition does NOT match

#### Scenario: Regex on argument
- **WHEN** `if: {arg.command: regex "rm\\s+-rf"}`
- **AND** the tool call is `Bash(command="rm -rf /")`
- **THEN** the condition matches
- **AND** for `Bash(command="rm foo")` the condition does NOT match

#### Scenario: Inverted match (not_regex)
- **WHEN** `if: {arg.command: not_regex "^(ls|cat|echo)\\b"}`
- **AND** the tool call is `Bash(command="npm test")`
- **THEN** the condition matches (because command does NOT start with ls/cat/echo)
- **AND** for `Bash(command="ls -la")` the condition does NOT match

#### Scenario: all/any composition
- **WHEN** `if: {all: [{tool: "Bash"}, {arg.command: glob "git *"}]}`
- **AND** the tool call is `Bash(command="git commit")`
- **THEN** the condition matches (both clauses hold)

#### Scenario: Mixed all+any rejected
- **WHEN** `if: {all: [...], any: [...]}` (both keys present)
- **THEN** startup rejects the rule with a clear error naming the conflicting keys

### Requirement: Action types and hybrid deny semantics
The system MUST support four action types, each with its own deny semantics:

| action | Deny判定 | Deny reason | Special constraint |
|---|---|---|---|
| `shell` | `exit_code ≠ 0` | `stdout` first non-empty line; fallback `"hook shell 拦截高危工具调用"` | MUST NOT have `deny` or `deny_reason` field — startup rejects |
| `http` | `parse_expr` sets `res.deny = true` | `res.deny_reason` (required when deny=true) | HTTP 4xx/5xx, connection errors do NOT auto-trigger deny |
| `sub-agent` | `parse_expr` sets `res.deny = true` | `res.deny_reason` (required when deny=true) | Sub-agent `executed_failed` does NOT auto-trigger deny |
| `prompt` | (no deny capability) | — | MUST NOT have `deny` or `deny_reason` field — startup rejects |

`parse_expr` MUST be evaluated with `simpleeval` (safe sandbox; no imports, no function
calls, no lambda). The expression receives a single `res` object (HTTP response or sub-agent
output) and may read attributes and assign `res.deny` / `res.deny_reason`.

#### Scenario: shell action deny on non-zero exit
- **WHEN** an action is `shell` with `command: "exit 1"`
- **THEN** the action result is deny with reason `"hook shell 拦截高危工具调用"` (default fallback)

#### Scenario: shell action with stdout reason
- **WHEN** an action is `shell` with `command: 'echo "禁止 rm -rf"; exit 1'`
- **THEN** the action result is deny with reason `"禁止 rm -rf"` (first stdout line)

#### Scenario: shell action allow on zero exit
- **WHEN** an action is `shell` with `command: "exit 0"`
- **THEN** the action result is allow (does not deny)

#### Scenario: http action deny via parse_expr
- **WHEN** an action is `http` with `parse_expr: "if res.data.level == 'danger': res.deny = True; res.deny_reason = '风控告警'"`
- **AND** the HTTP response body parses to `{"data": {"level": "danger"}}`
- **THEN** the action result is deny with reason `"风控告警"`

#### Scenario: http action 5xx is not auto-deny
- **WHEN** an action is `http` with `parse_expr: "pass"` (no deny assignment)
- **AND** the HTTP response is 503 Service Unavailable
- **THEN** the action result is allow (HTTP error does not auto-deny)

#### Scenario: prompt action has no deny capability
- **WHEN** an action is `prompt` with `content: "..."` and `deny: true`
- **THEN** startup rejects the rule with a clear error (deny not allowed for prompt)

### Requirement: Multi-action short-circuit within a single hook
The system MUST execute actions within a single hook in array order. When any action returns
deny, the system MUST stop executing subsequent actions in the same hook. When all actions
return allow, the hook returns allow.

#### Scenario: First deny short-circuits
- **WHEN** a hook has `actions: [shell_a, http_b, shell_c]`
- **AND** `shell_a` returns deny
- **THEN** `http_b` and `shell_c` MUST NOT execute
- **AND** the hook returns deny with the reason from `shell_a`

#### Scenario: All allow proceeds
- **WHEN** a hook has `actions: [shell_a, http_b]`
- **AND** both return allow
- **THEN** the hook returns allow

### Requirement: Multi-hook short-circuit within a single event
The system MUST execute multiple hooks registered for the same event in the order they appear
in `config.yaml: hooks:`. When any hook returns deny, the system MUST stop executing
subsequent hooks for that event. The system MUST record the first denying hook's `id` in
the ToolResult's `denied_hook_id` field.

#### Scenario: First deny hook wins
- **WHEN** two `tool.pre` hooks are registered: `id=hook_a` (allow) and `id=hook_b` (deny)
- **AND** the call would otherwise pass both
- **THEN** `hook_a` runs and returns allow
- **AND** `hook_b` runs and returns deny
- **AND** the produced ToolResult has `denied_hook_id="hook_b"`

#### Scenario: Second hook does not run after first deny
- **WHEN** `id=hook_a` returns deny and `id=hook_b` is registered after it
- **THEN** `hook_b` MUST NOT execute

### Requirement: Pipeline integration (L1 → hook.pre → L2-L5 → execute → hook.post)
The system MUST integrate hooks into the Agent.run() tool_call pipeline as follows:

1. **L1 hard blacklist** runs first (v0.5 behavior). On deny: ToolResult(execution_status=block_l1, denied_by=l1_blacklist).
2. **hook.pre** runs next. On deny: ToolResult(execution_status=block_hook_pre, denied_by=hook_pre, denied_hook_id=<id>). On allow: continue.
3. **L2-L5 permission pipeline** runs (v0.5 behavior). On deny: ToolResult(execution_status=block_permission, denied_by=l2_l5_permission).
4. **execute_tool_call** runs. On success: ToolResult(execution_status=executed_success). On tool error: ToolResult(execution_status=executed_failed, is_error=True).
5. **hook.post** runs last (regardless of which earlier step denied or executed).

L1 MUST be invoked before hook.pre — hook.pre MUST NOT be able to allow a call that L1
would deny.

#### Scenario: L1 denies before hook.pre
- **WHEN** L1 hard blacklist denies `Bash("rm -rf /")`
- **THEN** the ToolResult is `execution_status=block_l1`, `denied_by=l1_blacklist`
- **AND** NO `tool.pre` hooks fire for this call

#### Scenario: hook.pre denies before L2-L5
- **WHEN** L1 allows `Bash("chmod 777 /tmp/x")` and a `tool.pre` hook denies it
- **THEN** the ToolResult is `execution_status=block_hook_pre`, `denied_by=hook_pre`, `denied_hook_id=<hook id>`
- **AND** L2-L5 MUST NOT be evaluated

#### Scenario: L2-L5 denies after hook.pre allows
- **WHEN** hook.pre allows `Bash("rm foo.txt")` and L2 path sandbox denies it
- **THEN** the ToolResult is `execution_status=block_permission`, `denied_by=l2_l5_permission`
- **AND** hook.post still fires

#### Scenario: hook.post fires after execution
- **WHEN** `Bash("ls")` is allowed by all layers and executes successfully
- **THEN** the ToolResult is `execution_status=executed_success`
- **AND** `tool.post` hooks fire on this result

#### Scenario: hook.post fires after execution error
- **WHEN** `Read("missing.txt")` returns `is_error=True` (file not found)
- **THEN** the ToolResult has `execution_status=executed_failed` (and is_error=True)
- **AND** `tool.post` hooks fire

### Requirement: ToolResult new fields (execution_status, denied_by, denied_hook_id)
The system MUST extend `ToolResult` (in `baozicode/tools/base.py`) with three optional
fields: `execution_status: Literal["block_l1", "block_hook_pre", "block_permission",
"executed_success", "executed_failed"] | None = None`, `denied_by: Literal["l1_blacklist",
"hook_pre", "l2_l5_permission"] | None = None`, and `denied_hook_id: str | None = None`.

The system MUST compute `is_error` as the derived value:
`is_error = (execution_status is not None and execution_status != "executed_success")`.

Code that constructs `ToolResult` without setting `execution_status` MUST continue to work —
in that case `execution_status` defaults to `None`, and `is_error` is whatever the caller
explicitly set (no auto-derivation when `execution_status` is None).

#### Scenario: Backward-compatible construction
- **WHEN** old code constructs `ToolResult(tool_call_id="x", content="y", is_error=True)`
- **THEN** the ToolResult has `execution_status=None`, `denied_by=None`, `denied_hook_id=None`
- **AND** `is_error=True` (as set by the caller)
- **AND** downstream code reading `is_error` still works

#### Scenario: New code sets all fields
- **WHEN** `_v5_executor` denies a call via hook.pre with hook id `audit-risky`
- **THEN** the returned ToolResult has `execution_status="block_hook_pre"`, `denied_by="hook_pre"`, `denied_hook_id="audit-risky"`, `is_error=True` (derived), `content="<deny reason>"`

#### Scenario: is_error derived correctly
- **WHEN** a tool executes successfully and the new code sets `execution_status="executed_success"`
- **THEN** `is_error=False` (derived)
- **WHEN** a tool executes with error and `execution_status="executed_failed"`
- **THEN** `is_error=True` (derived)
- **WHEN** `execution_status="block_l1"`
- **THEN** `is_error=True` (derived)

### Requirement: Execution control (timeout, async for post only)
The system MUST support per-hook `timeout: int` (seconds, default 30) for shell and http
actions. The system MUST support per-hook `async: bool` (default false) ONLY for `tool.post`
events — startup MUST reject `async: true` on `tool.pre` and other pre-deny events.

When `async: true`, the system MUST execute the hook via `asyncio.create_task` and MUST NOT
await it; the main Agent loop continues without waiting.

#### Scenario: Timeout aborts shell action
- **WHEN** an action is `shell` with `timeout: 5` and `command: "sleep 30"`
- **THEN** the action aborts after 5 seconds (SIGTERM, then SIGKILL after grace)
- **AND** the hook continues to the next action (timeout is logged, not raised)

#### Scenario: async not allowed on tool.pre
- **WHEN** a rule has `event: "tool.pre"` and `async: true`
- **THEN** startup rejects the rule with a clear error

#### Scenario: async on tool.post does not block
- **WHEN** a rule has `event: "tool.post"` and `async: true`
- **THEN** the hook fires via `asyncio.create_task` and the Agent loop does not wait for completion

### Requirement: Hook failure isolation (log only, never break Agent)
The system MUST wrap each hook action's execution in try/except. Any exception, timeout, or
configuration mismatch during hook execution MUST be logged at WARNING level and MUST NOT be
raised into the Agent loop. The Agent loop continues normally regardless of hook failures.

A deny returned by an action is a normal-path result and is NOT a failure — denial MUST
NOT be logged as a warning and MUST NOT trigger the failure-isolation branch.

#### Scenario: Shell command throws exception
- **WHEN** a hook's shell action's subprocess raises OSError (binary not found)
- **THEN** the hook logs `WARNING hook <id> shell action failed: <exc>`
- **AND** the Agent loop continues normally

#### Scenario: Hook timeout logged as warning
- **WHEN** a hook's action exceeds its timeout
- **THEN** the hook logs `WARNING hook <id> timed out after <N>s`
- **AND** the Agent loop continues normally

#### Scenario: Deny is not treated as failure
- **WHEN** a hook's shell action returns exit_code=1 (deny path)
- **THEN** no warning is logged for the action
- **AND** the deny is propagated normally as a hook.pre denial

### Requirement: Audit log (every hook invocation recorded)
The system MUST append a JSON line to `.baozicode/audit.log` for every hook invocation
(invocation means a hook matched the event and started executing, regardless of allow/deny
result). Each line MUST be a JSON object with fields: `ts` (ISO8601), `session_id`, `hook_id`,
`event`, `tool_name` (or null for non-tool events), `result` (`allow` | `deny` | `error`),
`reason` (string or null), `duration_ms` (int).

The system MUST rotate the audit log when it exceeds 100 MB, moving the current file to
`.baozicode/audit.log.YYYYMMDD-HHMMSS` and starting a new one.

#### Scenario: Every hook invocation logged
- **WHEN** 3 hooks fire on `tool.pre` for a single Bash call (2 allow, 1 deny)
- **THEN** 3 JSON lines are appended to `.baozicode/audit.log`
- **AND** the deny line has `result="deny"`, the allow lines have `result="allow"`

#### Scenario: Audit log rotation
- **WHEN** `.baozicode/audit.log` exceeds 100 MB
- **THEN** the file is renamed to `.baozicode/audit.log.YYYYMMDD-HHMMSS`
- **AND** a fresh empty `.baozicode/audit.log` is created
- **AND** subsequent hook invocations write to the new file

### Requirement: Validation at startup (centralized freeze)
The system MUST call `HookRegistry.freeze()` once during bootstrap. This single call MUST
validate ALL of the following:

- Each rule has `id` (unique), `event` (in taxonomy), non-empty `actions` list
- `event: tool.pre` rules do NOT have `async: true`
- `action: shell` rules do NOT have `deny` / `deny_reason` fields
- `action: http` and `action: sub-agent` rules have `parse_expr`
- `action: prompt` rules do NOT have `deny` / `deny_reason` fields
- `slot: stable_system` is NOT used with `event: tool.pre` / `tool.post`
- `if:` with both `all` and `any` keys → reject
- Total hook count ≤ 50 (configurable upper bound)

Any validation failure MUST cause `SystemExit` with a message naming the offending rule id
and the specific field violation. Hooks that pass validation MUST be locked — no mutation
allowed after `freeze()`.

#### Scenario: Forbidden field rejected
- **WHEN** an action is `shell` with extra field `deny: true`
- **THEN** startup rejects the rule with message naming the rule id and the `deny` field

#### Scenario: Duplicate id rejected
- **WHEN** two rules have `id: "audit-bash"`
- **THEN** startup rejects the second one with a duplicate-id error

#### Scenario: Too many hooks rejected
- **WHEN** `config.yaml: hooks:` has more than 50 rules
- **THEN** startup rejects with a count-exceeded error
- **AND** no hooks are registered

### Requirement: HookBootstrap and lifecycle integration
The system MUST provide `baozicode.hooks.bootstrap.load_hooks(config: AppConfig, project_root:
Path) -> HookRegistry` that:
1. Parses `config.hooks` from the loaded AppConfig
2. Calls `HookRegistry.freeze()` for centralized validation
3. Returns a frozen `HookRegistry` ready for use by Agent

The bootstrap MUST be invoked from `BaoZiCodeApp.__init__` AFTER `permissions_bootstrap` and
BEFORE `instructions_bootstrap` (so hook config errors block instructions loading and give
the user clear errors early).

The Agent MUST accept a `hook_registry` parameter via `Agent.__init__` and use it to dispatch
events at the lifecycle points defined in the "Event taxonomy" requirement.

#### Scenario: Bootstrap order
- **WHEN** `BaoZiCodeApp(config)` is constructed
- **THEN** `permissions_bootstrap` runs first
- **AND** `hooks_bootstrap` (load_hooks) runs second
- **AND** `instructions_bootstrap` runs third
- **AND** the v0.8 banner shows hook count

#### Scenario: Missing hooks block is valid v1.0 config
- **WHEN** `config.yaml` from v1.0 (no `hooks:` block) is loaded
- **THEN** `AppConfig.hooks` is `None` or empty list
- **AND** `load_hooks` returns a `HookRegistry` with zero hooks
- **AND** the Agent.run() pipeline skips all hook dispatch points
- **AND** behavior is identical to v1.0

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

