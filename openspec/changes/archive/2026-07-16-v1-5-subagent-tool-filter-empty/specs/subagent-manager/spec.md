## ADDED Requirements

### Requirement: Explicit empty tools allowlist is accepted

`ToolFilter.visible_tools` MUST distinguish `role.tools is None`(no constraint, all tools allowed by L2) from `role.tools == []`(explicit empty allowlist). When `role.tools` is a non-`None` empty list `[]`, the filter SHALL return an empty `visible_tools` list without raising `ToolFilterEmptyError`. When any other layer (L1 global deny / L3 role deny / L4 background whitelist) reduces the set to empty, the filter SHALL still raise `ToolFilterEmptyError` as before.

#### Scenario: Explicit empty tools allowlist passes filter
- **WHEN** a sub-Agent `AgentDef` declares `tools: []` in frontmatter and other layers do not independently reduce the set
- **THEN** `ToolFilter.visible_tools` returns `[]` and does NOT raise

#### Scenario: None tools means no constraint
- **WHEN** a sub-Agent `AgentDef` declares `tools:` absent or `tools: null` (Pydantic parses as `None`)
- **THEN** L2 is skipped and `visible_tools` includes all tools from L1 (minus `task` from GLOBAL_DENY)

#### Scenario: L3 reduce-to-empty still errors
- **WHEN** `role.tools_deny` removes all allowed tools (e.g. `tools=["Read"]` + `tools_deny=["Read"]`)
- **THEN** `ToolFilter.visible_tools` raises `ToolFilterEmptyError` (treated as misconfiguration)

### Requirement: summarizer role becomes dispatchable

The builtin `summarizer` role MUST be dispatchable via `SubAgentManager.dispatch(type="definition", role="summarizer")` without raising `ToolFilterEmptyError`. Its frontmatter MUST use `tools: []` (explicit empty allowlist) instead of `tools-deny=[ALL]` to express "I am a tool-less role".

#### Scenario: summarizer frontmatter uses tools: []
- **WHEN** builtin summarizer AGENT.md is read
- **THEN** frontmatter contains `tools: []` (not `tools-deny`)

#### Scenario: summarizer dispatch succeeds
- **WHEN** `SubAgentManager.dispatch(type="definition", role="summarizer", prompt="...")` is called
- **THEN** dispatch returns a `task_id` (async path) or summary string (sync path), no `ToolFilterEmptyError`