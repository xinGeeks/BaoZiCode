# agent-runtime Specification (v1.2)

## Purpose
`SubAgentRuntime` constructs isolated sub-Agent instances on demand. It is
called by `SubAgentManager.dispatch(...)` after a role definition has been
resolved via `AgentRegistry.lookup(...)`. The runtime is responsible for:

1. Building a fresh `Agent` instance with isolated per-task state
2. Applying a 4-layer tool filter to determine the sub-Agent's visible
   tool set
3. For **fork** mode, sharing the parent Agent's `BuiltPrompt` object so
   that the LLM API's prompt cache hits on the system-prompt + history
   prefix
4. For **definition** mode, constructing a fresh `BuiltPrompt` with the
   role body as the system identity

The runtime holds references to the **shared** infrastructure
(`LLMClient`, `HookDispatcher`, `ToolRegistry`, project root, app config)
but **does not** share any per-task state with the parent Agent.

## Requirements

### Requirement: SubAgentRuntime.spawn signature
`SubAgentRuntime.spawn` MUST accept the following parameters and return a
fully-constructed `Agent` instance ready to `await agent.run(prompt)`:

- `task_id: str` — unique task ID assigned by `SubAgentManager`
- `type: Literal["definition", "fork"]` — spawn mode
- `role_def: AgentDef | None` — required for `type="definition"`, `None` for
  `type="fork"`
- `prompt: str` — the task prompt (already placeholder-substituted if
  applicable)
- `parent_messages: list[Message] | None` — required for `type="fork"`
  (snapshot of parent conversation); `None` for `type="definition"`
- `parent_denied_counts: dict[str, int] | None` — fork mode inherits the
  parent's session-rule counter; `None` for `definition` (starts at zero)
- `parent_agent: Agent | None` — required for `type="fork"` (to share
  `_prompt` object); `None` for `type="definition"`

The function MUST be synchronous (returns the Agent instance; the actual
LLM streaming happens inside `agent.run`).

#### Scenario: Definition spawn
- **WHEN** `spawn(task_id="t-1", type="definition", role_def=explorer_def,
  prompt="look at foo.py", parent_messages=None, parent_denied_counts=None,
  parent_agent=None)` is called
- **THEN** the returned `Agent` has an empty `ConversationManager`
- **AND** the Agent's `plan_mode=False`, `max_iterations=role_def.frontmatter.max_iterations`
- **AND** the Agent's `permission_mode` follows `role_def.frontmatter.permission_mode`
  if set, else inherits from config

#### Scenario: Fork spawn
- **WHEN** `spawn(task_id="t-2", type="fork", role_def=None,
  prompt="continue", parent_messages=<5 messages>, parent_denied_counts={"Bash":2},
  parent_agent=parent)` is called
- **THEN** the returned `Agent` has a `ConversationManager` containing the
  5 parent messages (in order, with `tool_use_id` preserved)
- **AND** the Agent's `_prompt` object IS the parent Agent's `_prompt` (same
  Python object, byte-identical)
- **AND** the Agent's `MergedPermissions.session_rules` is initialized from
  `parent_denied_counts`

### Requirement: Definition mode — empty conversation + role identity
For `type="definition"`, the sub-Agent MUST start with an empty
`ConversationManager` (no parent messages, no history). The first user
message added (by `agent.run(prompt)`) is the task prompt.

The sub-Agent's system prompt MUST be a fresh `BuiltPrompt` constructed by
`PromptBuilder.build()` with the role body substituted into the identity
section. This means the system prompt is **NOT byte-identical** to the
parent's, and the LLM API cache will be cold-started.

#### Scenario: Definition mode cold cache
- **WHEN** a definition-style sub-Agent is spawned with role `explorer`
- **THEN** the sub-Agent's `_prompt.stable_system` is a NEW string
  (not the parent's)
- **AND** the identity section of the system prompt contains the role body
- **AND** the parent Agent's system prompt is unchanged (no mutation)

#### Scenario: First user message is the task prompt
- **WHEN** the user calls `task(type="definition", role="explorer",
  prompt="look at foo.py")`
- **THEN** the sub-Agent's `ConversationManager` after `agent.run(prompt)`
  starts has exactly 1 user message: `Message(role="user",
  content="look at foo.py")`

### Requirement: Fork mode — snapshot + cache reuse
For `type="fork"`, the sub-Agent MUST start with a `ConversationManager`
containing the full snapshot of the parent conversation (in order, with
`tool_use_id` preserved verbatim). The sub-Agent's `agent.run(prompt)` will
append the task prompt as a new user message at the end.

The sub-Agent's system prompt MUST be the **same Python object** as the
parent Agent's `_prompt` (shared reference, not a copy). This guarantees
byte-identical system-prompt output to the LLM API, which combined with
the prefix-identical history allows Anthropic/OpenAI prompt cache to hit.

#### Scenario: Fork mode hot cache
- **WHEN** a fork-style sub-Agent is spawned from a parent Agent
- **THEN** `sub_agent._prompt is parent_agent._prompt` evaluates to `True`
- **AND** the sub-Agent's `ConversationManager` has the same message count
  as the parent's

#### Scenario: Fork snapshot preserves tool_use_id
- **WHEN** the parent has an assistant message with `tool_use_id="tu_123"`
- **AND** a fork sub-Agent is spawned
- **THEN** the sub-Agent's `ConversationManager` contains the same
  assistant message with `tool_use_id="tu_123"`

#### Scenario: Fork snapshot does not include in-flight reminders
- **WHEN** the parent has pending `<system-reminder>` messages in its
  `_pending_reminders` queue (not yet appended to conversation)
- **AND** a fork sub-Agent is spawned
- **THEN** the sub-Agent's `ConversationManager` does NOT include those
  pending reminders (they are not yet part of the conversation history)

### Requirement: State isolation between sub-Agent and parent
The sub-Agent's runtime state MUST be **fully independent** of the parent's
in the following dimensions:

- **Conversation messages** — separate `ConversationManager` (no shared
  `_messages` list)
- **Permission deny/allow counters** — separate `MergedPermissions.session_rules`
  dict (forks inherit the parent's snapshot as initial state, then
  diverge)
- **File read cache** — separate per-task cache (if implemented; v1.2 may
  share read cache for performance — implementation detail)
- **Token counters** — separate `UsageStats` instance

Shared state (read-only by sub-Agent, never mutated):
- `LLMClient` (LLM calls go through the same client)
- `HookDispatcher` (lifecycle events fire on the same dispatcher, with
  `subagent` payload field)
- `ToolRegistry` global definitions (sub-Agent sees a filtered view, but
  the underlying registry is shared)
- `AppConfig` (read-only at spawn time)

#### Scenario: Sub-Agent message isolation
- **WHEN** a sub-Agent adds a user message to its conversation
- **THEN** the parent Agent's `ConversationManager` is unchanged
- **AND** the sub-Agent's `ConversationManager` has the new message

#### Scenario: Sub-Agent token isolation
- **WHEN** a sub-Agent completes with `usage.in=1000, usage.out=500`
- **THEN** the parent Agent's `session_usage` is unchanged
- **AND** the sub-Agent's `TaskInfo.usage` has `in=1000, out=500`

#### Scenario: Sub-Agent permission counter divergence
- **WHEN** a fork sub-Agent inherits `parent_denied_counts={"Bash": 2}`
- **AND** the sub-Agent has 1 more `Bash` denial
- **THEN** the sub-Agent's `MergedPermissions.session_rules` has
  `{"Bash": 1}` (its own counter, started fresh from the snapshot as
  initial state — implementation choice: counters may inherit as
  initial-only and not sync back)
- **AND** the parent Agent's `MergedPermissions.session_rules` is unchanged
  (or its own counter advanced independently — implementation choice:
  the spec requires divergence, not specific direction)

### Requirement: 4-layer tool filter
`ToolFilter` MUST compute the sub-Agent's visible tool set as the
intersection of 4 layers (AND):

1. **L1 — Global deny**: A hard-coded `frozenset({"task"})`. Any tool in
   this set is **always removed**, regardless of role. This is the
   anti-nesting defense.
2. **L2 — Role allow (`role_def.frontmatter.tools`)**: If non-None, only
   tools in this list are allowed. If `None`, all tools pass this layer.
3. **L3 — Role deny (`role_def.frontmatter.tools_deny`)**: Tools in this
   list are removed. (Takes precedence over L2 if there's a conflict.)
4. **L4 — Background whitelist**: When `is_background=True` (i.e.,
   `async=true` in the `task` call), only tools in
   `AppConfig.subagents.background_whitelist` are allowed. The default
   whitelist is `[Read, Grep, Glob, WebFetch, notify_complete]`.

If the final intersection is empty, `ToolFilter` MUST raise
`ToolFilterEmptyError` with a message detailing all 4 layer states (so
`SubAgentManager.dispatch` can return a clear error to the LLM).

#### Scenario: L1 bans task tool globally
- **WHEN** `ToolFilter(role_def=None, is_background=False, ...)` is
  constructed
- **THEN** `visible_tools` does NOT include any tool named `task`
- **AND** the filter is the same regardless of role settings

#### Scenario: L2 narrows by role whitelist
- **WHEN** role `explorer` has `tools: [Read, Grep, Glob, WebFetch]`
- **THEN** `visible_tools` includes only those 4 (plus always-present
  internal tools if any)

#### Scenario: L3 overrides L2
- **WHEN** role `explorer` has `tools: [Read, Grep, Write]` and
  `tools-deny: [Write]`
- **THEN** `visible_tools` is `[Read, Grep]` (Write removed by L3)

#### Scenario: L4 restricts background mode
- **WHEN** `is_background=True` and `background_whitelist=[Read, Grep]`
- **AND** role has `tools: [Read, Grep, Write]`
- **THEN** `visible_tools` is `[Read, Grep]` (Write excluded by L4 even
  though L2 allows it)

#### Scenario: Empty result raises ToolFilterEmptyError
- **WHEN** `is_background=True` and `background_whitelist=[]`
- **AND** role has `tools: [Read]`
- **THEN** `ToolFilter.visible_tools` raises `ToolFilterEmptyError`
  with message containing all 4 layer states

#### Scenario: L1 still applied after L4
- **WHEN** `is_background=True` and `background_whitelist=[Read, task]`
- **THEN** `visible_tools` does NOT include `task` (L1 wins)

### Requirement: Sub-Agent Agent construction
The `Agent` instance returned by `spawn` MUST be constructed with:

- `llm_client=<shared LLMClient>`
- `tools=<filtered visible_tools>` (NOT `self._tools`)
- `conversation=<fresh per-task ConversationManager>`
- `permissions=None` (use merged_permissions path)
- `config=self._config`
- `max_iterations=role_def.frontmatter.max_iterations` for definition
  mode; for fork mode, the parent's max_iterations (or a separate
  per-role override if role is given)
- `plan_mode=False`
- `permission_callback=None` (sub-Agent has no L5 user decision)
- `session_mode=<role_def.frontmatter.permission_mode or inherited>`
- `merged_permissions=<fresh per-task MergedPermissions>`
- `permissions_engine=<fresh RuleEngine>`
- `compact_ctx=None` (sub-Agent does not auto-compact)
- `instructions_text=""` (sub-Agent does not inherit parent's BaoZiCode.md)
- `project_root=self._project_root`
- `skill_filter=None`, `skill_activation=None`, `skill_registry=None`
  (sub-Agent does not load skills)
- `hook_dispatcher=self._hooks` (shared)
- `subagent_meta=<dict with task_id, role, type, depth>` (new v1.2 kwarg)

#### Scenario: Sub-Agent has empty skill state
- **WHEN** a sub-Agent is spawned
- **THEN** the sub-Agent's `skill_registry`, `skill_activation`, and
  `skill_filter` are all `None`
- **AND** the sub-Agent's tool list does NOT include `load_skill` (it's
  filtered out by the 4-layer filter; the `load_skill` tool is `internal`
  but the filter does not preserve internal tools — the spec requires
  sub-Agents to NOT load skills)

#### Scenario: Sub-Agent has no permission_callback
- **WHEN** a sub-Agent encounters a fallthrough decision (L2-L5 say
  fallthrough)
- **THEN** the sub-Agent's `_handle_user_decision` returns
  `is_error=True` (default deny — no L5 user decision available)
- **AND** the parent Agent's `session_usage` does not change

### Requirement: Sub-agent metadata propagation
The `subagent_meta` dict MUST be set on the Agent instance and used by
`_fire_lifecycle_safe` to annotate all lifecycle event payloads. The
dict has shape:

```python
{
    "task_id": str,       # 形如 "task-2026-07-09-abc123"
    "role": str | None,   # definition 模式的 role 名;fork 模式 None
    "type": Literal["definition", "fork"],
    "depth": int,         # 永远 1 (v1.2 L1 物理禁嵌套)
}
```

#### Scenario: Sub-agent events have subagent field
- **WHEN** a sub-Agent runs and fires `turn.start`, `tool.pre`,
  `tool.post`, `session.end` events
- **THEN** each event's payload (when it is a dict) has a `subagent` key
  matching the sub-agent metadata
- **AND** main Agent's events have `subagent=None` (or the key is absent
  in payloads, depending on implementation)

### Requirement: Sub-Agent has no LLM prompt cache benefits for definition mode
Definition-mode sub-Agents MUST NOT inherit the parent's `_prompt`
object. A new `BuiltPrompt` is constructed via `PromptBuilder.build()`
with the role body as the identity section. The first LLM call is a cold
cache start (no prompt cache hit on the system prompt).

#### Scenario: Definition cold start
- **WHEN** a definition-mode sub-Agent is spawned
- **THEN** `sub_agent._prompt is not parent_agent._prompt`
- **AND** `sub_agent._prompt.stable_system != parent_agent._prompt.stable_system`
- **AND** the first LLM call's `cache_read_tokens` is 0 (cold cache)

### Requirement: Sub-Agent max_iterations enforcement
The sub-Agent's `Agent` MUST be constructed with the role's
`max-iterations` (definition mode) or the parent's `max-iterations` (fork
mode) as the upper bound. The sub-Agent MUST terminate with
`StopReason.MAX_ITERATIONS_REACHED` if it exceeds the bound.

#### Scenario: Sub-Agent respects max_iterations
- **WHEN** a definition sub-Agent has `max-iterations=3`
- **AND** the Agent.run loop iterates 4 times (all requesting tool calls)
- **THEN** the sub-Agent terminates with `MAX_ITERATIONS_REACHED`
- **AND** `TaskInfo.state` is set to `failed` (or `timeout` if reached
  via wall-clock timeout — see subagent-manager spec)
