# skill-loader Specification (v1.0)

## Purpose
The single entry point that activates a Skill. The `load_skill` function is
exposed via two surfaces that share the same underlying implementation:

1. **Slash command**: `/skill <name> [--key=value ...]` (user-driven, via
   v0.9 `CommandRegistry` dispatch)
2. **Internal tool**: `load_skill(name: str, args: dict[str, str] | None) -> dict`
   (LLM-driven, via `ToolDefinition` registered in `ToolRegistry`)

On activation, the loader:
1. Looks up the Skill in `SkillRegistry`
2. Substitutes `{var}` placeholders in the body using the args dict
3. Adds the Skill to `SkillActivation.active` (idempotent)
4. Registers a slash command `/<name>` in v0.9 `CommandRegistry` (if not already)
5. Applies the tool whitelist (see `skill-tool-whitelist` spec)
6. Returns a result dict

The `load_skill` tool is marked `tool_type="internal"` so it bypasses the
whitelist check (otherwise a Skill trying to load another Skill would be
self-denied).

## Requirements

### Requirement: Slash command /skill <name>
The `/skill` slash command MUST be registered in v0.9 `CommandRegistry` as
a `UI_STATE` command (or similar) that parses `<name> [--key=value ...]`
and calls `SkillLoader.load_skill(name, args)`.

#### Scenario: Basic load
- **WHEN** user types `/skill review`
- **THEN** `load_skill("review", args={})` is called
- **AND** a chat info line is shown: `✓ Skill 'review' activated (mode=shared)`

#### Scenario: Load with key=value args
- **WHEN** user types `/skill review --since="5 轮前" --focus_area="权限层"`
- **THEN** `load_skill("review", args={"since": "5 轮前", "focus_area": "权限层"})` is called
- **AND** placeholders `{since}` and `{focus_area}` in the Skill body are
  substituted with the values

#### Scenario: Load unknown Skill
- **WHEN** user types `/skill nonexistent`
- **THEN** an error is shown: `WARN: no such skill: nonexistent. Try /skill list`
- **AND** no state is changed

#### Scenario: Load already-active Skill
- **WHEN** user types `/skill review` and `review` is already active
- **THEN** the activation is a no-op (idempotent)
- **AND** a chat info line is shown: `Skill 'review' is already active`

#### Scenario: Reload and unload subcommands
- **WHEN** user types `/skill reload review`
- **THEN** `SkillRegistry.reload("review")` is called
- **AND** if `review` is active, the active body is also updated
- **WHEN** user types `/skill unload review`
- **THEN** `SkillActivation.deactivate("review")` is called
- **AND** `/<name>` slash command is unregistered from `CommandRegistry`
- **AND** the tool whitelist is removed (full tool set restored)

### Requirement: Internal tool load_skill
The `load_skill` tool MUST be registered in `ToolRegistry` with
`tool_type="internal"`, so the v0.5 `_v5_executor` `skill_whitelist_check`
bypasses it. The tool signature:

```python
{
    "name": "load_skill",
    "description": "Activate a Skill by name. Skills are reusable AI workflows. Use this when a Skill's description matches the current task. Pass args as a dict for placeholder substitution.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name (lowercase a-z + `-`)"},
            "args": {"type": "object", "description": "Placeholder values; {var} in Skill body is replaced by args[var]"}
        },
        "required": ["name"]
    },
    "tool_type": "internal"
}
```

#### Scenario: LLM calls load_skill during Agent run
- **WHEN** the Agent calls `load_skill(name="review", args={"since": "5 轮前"})`
- **THEN** the Skill is activated
- **AND** a tool result is returned:
  ```python
  {
      "name": "review",
      "mode": "shared",
      "description": "审查自 {since} 起的改动",
      "body_preview": "请审查自 5 轮前 以来的所有改动..."  # first 200 chars
  }
  ```
- **AND** the next Agent iteration sees the active Skill via the dynamic
  section (see `skill-activation` spec)

#### Scenario: LLM calls load_skill with bad name
- **WHEN** the Agent calls `load_skill(name="nonexistent")`
- **THEN** the tool result is an `is_error=True` payload:
  `{"error": "no such skill: nonexistent", "available": ["commit", "review", "test", ...]}`

#### Scenario: load_skill bypasses skill whitelist
- **WHEN** Skill `commit` is active with `allowed-tools=[Write]`
- **AND** the Agent calls `load_skill(name="test")`
- **THEN** `load_skill` is invoked normally (not denied by the whitelist)
- **AND** `test` is activated; the new whitelist is the UNION of
  `[Write]` (existing) ∩ `test.allowed-tools`

### Requirement: Placeholder substitution
The loader MUST replace `{var}` placeholders in the Skill body using the
args dict. Unmatched `{var}` placeholders MUST be preserved as literal
text (NOT removed, NOT cause an error). This lets LLM-written Skills
degrade gracefully when a var is missing.

#### Scenario: All placeholders matched
- **WHEN** body is `请审查自 {since} 以来的改动` and args = `{"since": "5 轮前"}`
- **THEN** the rendered body is `请审查自 5 轮前 以来的改动`

#### Scenario: Some placeholders unmatched
- **WHEN** body is `请审查自 {since} 起,关注 {focus}` and args = `{"since": "5 轮前"}`
- **THEN** the rendered body is `请审查自 5 轮前 起,关注 {focus}`
- **AND** no error is raised; `{focus}` is preserved literally

#### Scenario: Empty args dict
- **WHEN** body has `{since}` but args = `{}`
- **THEN** the rendered body preserves `{since}` as literal
- **AND** the activation succeeds

#### Scenario: Escaped braces
- **WHEN** body has `{{literal}}` (double braces)
- **THEN** the rendered body has `{literal}` (single braces) — Markdown
  convention; the substitution regex does NOT touch `{{...}}`

### Requirement: Slash auto-registration
After `load_skill` succeeds, the loader MUST register a slash command
`/<name>` in the v0.9 `CommandRegistry` if not already present. The
registered handler dispatches to `SkillLoader.run_skill(name, args)` which
re-injects the Skill body as a user-role message in the current
conversation (for `shared` mode) or spawns a sub-Agent (for `independent`).

#### Scenario: New Skill registers slash
- **WHEN** `review` is not registered as a slash command
- **AND** `load_skill("review", {})` succeeds
- **THEN** `CommandRegistry.lookup("/review")` returns a non-None `CommandDef`

#### Scenario: Same Skill reloads (no double register)
- **WHEN** `review` is already registered as a slash command
- **AND** `load_skill("review", {})` is called again
- **THEN** the existing registration is preserved (no error, no duplicate)

#### Scenario: Deactivation unregisters slash
- **WHEN** `review` is registered as a slash command
- **AND** `unload("review")` is called
- **THEN** `CommandRegistry.lookup("/review")` returns `None`

### Requirement: Slash execution: shared mode
In `mode=shared`, calling `/review` (or the registered `/<name>`) appends
the placeholder-substituted body as a user-role message in the current
`ConversationManager` via `ctx.send_to_agent`. The Agent's next iteration
processes the Skill body as if the user had typed it.

#### Scenario: /review in shared mode
- **WHEN** Skill `review` is active with `mode=shared`
- **AND** body is `请审查自 {since} 以来的改动` and `{since}` is replaced at
  load time, so the body is static
- **AND** user types `/review`
- **THEN** the body is appended as a user-role message
- **AND** the Agent processes it (likely calls Read / Grep on recent changes)
- **AND** the response text stays in the main conversation history

### Requirement: Slash execution: independent mode
In `mode=independent`, calling `/review` spawns a sub-Agent:

1. Snapshot the last N main-conversation bubbles (`N=history_bubbles`)
2. Create a fresh `ConversationManager`
3. Create a new `Agent` (same backend, same Skill activation state)
4. Send the Skill body as the first user message in the sub-conversation
5. Run the sub-Agent to completion (`COMPLETED` or `MAX_ITERATIONS_REACHED`)
6. Generate a 3-section summary (`## 任务执行` / `## 关键发现` / `## 后续建议`)
   via LLM (default: sonnet; configurable via `config.skills.summary_model`)
7. Append the summary as a user-role message in the main conversation
8. Discard the sub-ConversationManager and sub-Agent

#### Scenario: /test in independent mode, no history
- **WHEN** Skill `test` is active with `mode=independent`, `history_bubbles=0`
- **AND** user types `/test`
- **THEN** a sub-Agent is created with empty history
- **AND** the Skill body is sent to the sub-Agent
- **AND** the sub-Agent runs to completion
- **AND** the summary is returned to the main conversation

#### Scenario: /test with 5 history bubbles
- **WHEN** Skill `test` is active with `history_bubbles=5`
- **AND** the main conversation has 12 user/assistant turns
- **THEN** the last 5 turns of the main conversation are copied as static
  context into the sub-conversation's messages (as user-role messages
  before the Skill body)
- **AND** the sub-Agent sees the recent context

#### Scenario: Sub-Agent abort propagates
- **WHEN** the sub-Agent stops with `MAX_ITERATIONS_REACHED`
- **THEN** the summary includes a `## 关键发现` section noting
  "sub-Agent did not complete (max iterations reached)"
- **AND** the main conversation receives a warning but no error
