# skill-tool-whitelist Specification (v1.0)

## Purpose
Implements the "tool whitelist" capability for active Skills. When a
Skill declares `allowed-tools: [Read, Grep]`, the LLM can ONLY use those
tools while the Skill is active. This is enforced by two layers:

1. **L0 (LLM visibility)**: `Agent.augmented_tools` is filtered to only
   show the whitelisted tools. The LLM doesn't see other tools, so it
   doesn't try them.
2. **L5' (defense in depth)**: v0.5 `_v5_executor` adds a new
   `skill_whitelist_check(call)` that runs BEFORE the existing
   `permissions.check(call)`. If the active Skill's whitelist doesn't
   include the called tool, the call is denied with
   `layer="L0_skill_whitelist"`.

The `load_skill` tool itself is marked `tool_type="internal"` and
bypasses the whitelist check (otherwise a Skill trying to load another
Skill would be self-denied).

## Requirements

### Requirement: L0 — narrow Agent.augmented_tools
When the active Skills' `allowed_tools` are NOT empty (i.e., at least
one Skill has a whitelist), the `Agent.augmented_tools` passed to the
LLM MUST be filtered to the intersection of all active Skills'
`allowed_tools` ∩ `current_tool_registry`.

#### Scenario: Single Skill narrows to its whitelist
- **WHEN** Skill `review` is active with `allowed_tools=[Read, Grep, Glob]`
- **THEN** `augmented_tools` shows only Read, Grep, Glob
- **AND** Write, Edit, Bash, WebFetch are NOT in the LLM's tool list

#### Scenario: Multiple Skills, intersect whitelists
- **WHEN** Skills `review` (allowed=[Read, Grep, Glob]) and
  `commit` (allowed=[Read, Write, Edit]) are both active
- **THEN** `augmented_tools` shows only Read (the intersection)
- **AND** Grep, Glob, Write, Edit are NOT shown to the LLM

#### Scenario: A Skill with allowed_tools=None means "no restriction"
- **WHEN** Skill `commit` has `allowed_tools=None`
- **THEN** `commit` does NOT contribute to the intersection
- **AND** the effective whitelist is the union of other active Skills'
  whitelists (or "no restriction" if commit is the only active Skill)

#### Scenario: No active Skills, no narrowing
- **WHEN** no Skills are active
- **THEN** `augmented_tools` is the full `ToolRegistry` (all 7 built-in +
  v0.6 MCP tools)
- **AND** no filtering is applied

### Requirement: L5' — defense-in-depth in _v5_executor
The v0.5 `_v5_executor` MUST add a `skill_whitelist_check(call)` step
that runs BEFORE `permissions.check(call)`. If the called tool is NOT
in the active Skills' effective whitelist, the call is denied with:

```python
PermissionDecision(
    decision="deny",
    layer="L0_skill_whitelist",
    reason="tool 'Bash' is not in active Skill 'review' whitelist (allowed: Read, Grep, Glob)",
    matched_pattern="<skill_name>:<tool_name>",
    scope="once"
)
```

#### Scenario: LLM tries to call tool outside whitelist
- **WHEN** Skill `review` is active with `allowed_tools=[Read, Grep, Glob]`
- **AND** the LLM (somehow) calls `Bash(...)` (e.g., because the prompt
  mentions it from a previous turn)
- **THEN** `skill_whitelist_check` returns `decision=deny, layer=L0_skill_whitelist`
- **AND** the tool result is `is_error=True` with the deny reason
- **AND** `permissions.check` is NOT called (short-circuit)

#### Scenario: LLM calls whitelisted tool
- **WHEN** Skill `review` is active with `allowed_tools=[Read, Grep, Glob]`
- **AND** the LLM calls `Read(file_path="...")`
- **THEN** `skill_whitelist_check` returns `decision=fallthrough`
- **AND** `permissions.check` is called (v0.5 5-layer pipeline runs)

#### Scenario: No active Skills, no L0 check
- **WHEN** no Skills are active
- **THEN** `skill_whitelist_check` returns `decision=fallthrough` immediately
- **AND** `permissions.check` is called as before (v0.5 behavior unchanged)

### Requirement: load_skill bypasses whitelist
The `load_skill` tool MUST be marked `tool_type="internal"`. The
`skill_whitelist_check` MUST skip tools with `tool_type="internal"`. This
allows the LLM to load another Skill even when the current Skill's
whitelist would otherwise block it.

#### Scenario: load_skill is not blocked by Skill's whitelist
- **WHEN** Skill `commit` is active with `allowed_tools=[Write]`
- **AND** the LLM calls `load_skill(name="review", args={})`
- **THEN** `skill_whitelist_check` returns `decision=fallthrough` for
  `load_skill` (skipped due to `tool_type="internal"`)
- **AND** the existing `permissions.check` runs (load_skill is allowed)

#### Scenario: Other internal tools also bypass
- **WHEN** a future v1.1 tool is added with `tool_type="internal"`
- **THEN** it is also bypassed by `skill_whitelist_check`
- **AND** only `tool_type="user"` tools are subject to the whitelist

### Requirement: Boot-time whitelist validation
The `SkillRegistry.scan()` MUST validate each Skill's `allowed-tools`
list against the current `ToolRegistry` (built-in 7 + v0.6 MCP tools)
AT BOOT. If a Skill references a non-existent tool, the registry
construction MUST fail with `SystemExit` (similar to v0.9 alias collision
panic).

#### Scenario: Skill references non-existent tool
- **WHEN** Skill `foo` has `allowed-tools: [Read, NonExistentTool]`
- **THEN** at boot, `SystemExit` is raised with message:
  `skill 'foo' allowed-tools contains unknown tool: 'NonExistentTool'`
- **AND** the App exits before any user input is accepted
- **AND** the error message names the file path

#### Scenario: Skill references MCP tool by full name
- **WHEN** Skill `foo` has `allowed-tools: ["mcp__fs__read_file"]`
- **AND** the MCP server `fs` is configured and registered
- **THEN** the Skill passes boot validation
- **AND** the whitelist check matches the tool by its full name

#### Scenario: MCP server fails to connect, tool absent
- **WHEN** Skill `foo` has `allowed-tools: ["mcp__fs__read_file"]`
- **AND** the MCP server `fs` failed to connect (v0.6 broken state)
- **THEN** the tool is NOT in `ToolRegistry` (MCP manager only registers
  successful tools)
- **AND** the Skill boot validation FAILS (treats `mcp__fs__read_file` as
  unknown)
- **AND** the App exits with `SystemExit`

This is the conservative behavior: a Skill depending on an unavailable
MCP tool is treated as broken at boot, not silently degraded.

### Requirement: Whitelist changes apply on next iteration
The whitelist is a snapshot computed at the start of each Agent.run()
iteration. When a Skill is loaded, unloaded, or reloaded mid-conversation,
the new whitelist takes effect on the NEXT iteration (not the current
one).

#### Scenario: Load Skill mid-conversation
- **WHEN** at iter 3, the user types `/skill review` (loads Skill)
- **THEN** iter 3's LLM call was already made with the OLD whitelist
- **AND** iter 4's LLM call uses the NEW whitelist (with Skill `review`)

#### Scenario: Unload Skill mid-conversation
- **WHEN** at iter 5, the user types `/skill unload review`
- **THEN** iter 5's LLM call was made with the whitelist INCLUDING review
- **AND** iter 6's LLM call uses the whitelist WITHOUT review
