# Tool Calling — Role Visibility Delta

This delta adds 2 new requirements to `tool-calling`:
`ToolDefinition.role_visibility` field + `Agent(role)` filtering.

## ADDED Requirements

### Requirement: ToolDefinition carries role_visibility

`ToolDefinition` MUST add a `role_visibility: list[str] | None = None`
field with the following semantics:

- `None` (default) — any role can see and use this tool
- `['lead']` — only Agent constructed with `role='lead'` (or future
  `'coordinator'` if added to the list) sees it
- `['lead', 'coordinator']` — multiple roles; union semantics

`role_visibility` MUST be `None` for the 7 built-in tools (Read, Write,
Edit, Bash, Grep, Glob, WebFetch) — all roles see them.

`role_visibility` MUST be non-None only on tools whose semantics depend
on the agent's role (e.g., team-coordination tools are Lead-only).

#### Scenario: Default role_visibility is None
- **WHEN** `Read` tool's `ToolDefinition.role_visibility` is inspected
- **THEN** it MUST equal `None` (any role sees it)

#### Scenario: team_dispatch has lead-only visibility
- **WHEN** `team_dispatch` tool's `ToolDefinition.role_visibility` is
  inspected
- **THEN** it MUST equal `['lead']`

#### Scenario: role_visibility rejects unknown role names
- **WHEN** `ToolDefinition(role_visibility=['superuser'])` is
  constructed
- **THEN** `__post_init__` MUST raise `ValueError` listing permitted
  roles: `('lead', 'member', 'subagent')`

### Requirement: ToolRegistry filters by Agent role

`ToolRegistry.get_all_tools(role)` MUST filter tools by role visibility
for any non-None role argument, while preserving v1.3 behavior when
`role=None`:

- If `role is None`, return all tools (backward-compatible; identical to
  v1.3 behavior)
- If `role is not None`, return tools where `tool.role_visibility is
  None` OR `role in tool.role_visibility`

`Agent.__init__(..., role: Literal['lead', 'member', 'subagent'] = 'subagent')`
MUST store `self._role` and pass it to `ToolRegistry.get_all_tools(self._role)`
when building its tool list.

A future `role='coordinator'` value MUST be allowed; `__post_init__`
validation MUST accept `'coordinator'` as well (preparation for the
`v1-4-team-coordinator` proposal).

#### Scenario: Default Agent role is subagent
- **WHEN** `Agent(tool_registry, llm, ...)` is constructed without
  `role=`
- **THEN** `agent._role == 'subagent'`
- **AND** `agent.available_tools` is `tool_registry.get_all_tools(role='subagent')`
  — never includes team_*

#### Scenario: Lead Agent sees team tools
- **WHEN** Lead Agent is constructed with `role='lead'`
- **THEN** `agent._role == 'lead'`
- **AND** `agent.available_tools` includes all 7 built-in tools + 6 team_*

#### Scenario: Member Agent sees only built-ins
- **WHEN** Member Agent is constructed with `role='member'`
- **THEN** `agent.available_tools` contains only the 7 built-in tools
- **AND** `team_dispatch` / `team_send_message` / `team_cancel` /
  `team_merge` / `team_task_create` / `team_task_query` are NOT in the
  list

#### Scenario: get_all_tools backward compatibility
- **WHEN** old code calls `tool_registry.get_all_tools()` with no
  argument
- **THEN** it returns the full list (built-in + MCP + runtime tools) with
  no role filter applied (v1.3 behavior preserved)

#### Scenario: Roles can be extended (coordinator preparation)
- **WHEN** `Agent(role='coordinator')` is constructed (allowed even
  before coordinator is implemented)
- **THEN** it MUST succeed (no validation rejection)
- **AND** its available tools are those with `'coordinator' in
  role_visibility` or `role_visibility is None`
