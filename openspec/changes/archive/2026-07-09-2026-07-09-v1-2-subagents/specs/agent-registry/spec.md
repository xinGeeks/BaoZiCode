# agent-registry Specification (v1.2)

## Purpose
Defines how SubAgent **role definitions** are loaded, merged, and queried at
runtime. A role definition is a Markdown file with YAML frontmatter (similar
to Skills v1.0 `SKILL.md`) that declares a sub-Agent's identity, tools,
model, and execution constraints. The registry is owned by `BaoZiCodeApp`
(not by `ChatScreen` or `ConversationManager`) and is read by
`SubAgentManager` to resolve `task(type="definition", role=...)` calls.

The registry loads from four sources in priority order:
**project > user > builtin > plugin**, with later sources completely
overriding earlier ones for the same name. Plugin sources come from MCP
servers and are loaded dynamically after MCP bootstrap completes.

## Requirements

### Requirement: AgentFrontmatter schema
An `AgentFrontmatter` Pydantic model MUST validate the YAML frontmatter of
an Agent file. Required fields: `name` (matching `^[a-z][a-z0-9-]*$`),
`description`. Optional fields with defaults:

- `tools: list[str] | None` — whitelist; `None` = no narrowing
- `tools-deny: list[str] | None` — blacklist (overrides tools if overlap)
- `model: "inherit" | "haiku" | "sonnet" | "opus" | None` — default `inherit`
  (use parent Agent's model)
- `max-iterations: int` — default `20`, `1 ≤ n ≤ 100`
- `permission-mode: "strict" | "default" | "permissive" | None` — default
  `None` (inherit parent Agent's mode)
- `nesting-depth: int` — default `0`, `0 ≤ n ≤ 3`
- `hidden: bool` — default `False`

A `mode` field MUST NOT be accepted (Agents are always "independent" —
adding it back is a schema error).

#### Scenario: Valid minimal Agent
- **WHEN** an Agent file has frontmatter `name: explorer` and
  `description: "..."` only
- **THEN** `parse_agent` succeeds
- **AND** the resulting `AgentDef.frontmatter` has all optional fields
  set to their defaults

#### Scenario: Invalid name rejected
- **WHEN** an Agent file has `name: "Explorer"` (uppercase)
- **THEN** `parse_agent` raises `ValueError` with message
  "agent name 不合法 (需 ^[a-z][a-z0-9-]*$): 'Explorer'"

#### Scenario: Invalid max-iterations rejected
- **WHEN** an Agent file has `max-iterations: 0` or `max-iterations: 200`
- **THEN** `parse_agent` raises `ValueError` with bounds error message

#### Scenario: mode field rejected
- **WHEN** an Agent file has `mode: shared` in frontmatter
- **THEN** `parse_agent` raises `ValueError` ("extra fields not permitted" or
  similar from Pydantic `extra="ignore"` behavior — implementation detail)

#### Scenario: Missing required field rejected
- **WHEN** an Agent file has frontmatter with only `name: foo`
- **THEN** `parse_agent` raises `ValueError` ("description: Field required")

### Requirement: Agent file location and naming
Agent files MUST live in a directory containing one subdirectory per Agent,
with the Agent file named `AGENT.md`. Layout mirrors Skills v1.0
`<name>/SKILL.md` convention but uses `AGENT.md` to make the two
distinguishable on disk and to tooling.

Built-in Agents: `<pkg>/baozicode/agents/builtin/<name>/AGENT.md`
User-level: `~/.config/baozicode/agents/<name>/AGENT.md`
Project-level: `<root>/.baozicode/agents/<name>/AGENT.md`

#### Scenario: File at correct location loaded
- **WHEN** `<root>/.baozicode/agents/explorer/AGENT.md` exists with valid
  frontmatter
- **THEN** `AgentRegistry.scan(project_dir=<root>/.baozicode/agents)` finds
  it under name `explorer`

#### Scenario: File with wrong name skipped
- **WHEN** `<root>/.baozicode/agents/explorer/README.md` exists (not
  `AGENT.md`)
- **THEN** the directory is skipped during scan

#### Scenario: Loose .md files in agents dir skipped
- **WHEN** `<root>/.baozicode/agents/orphan.md` exists (not in a subdirectory)
- **THEN** the file is skipped (only subdirectories with `AGENT.md` are
  considered)

### Requirement: 4-level priority merge
`AgentRegistry.scan(builtin_dir, user_dir, project_dir, plugin_agents, ...)`
MUST scan all 4 sources in the order: builtin → user → project → plugin.
For each source, files are added to the registry. If a name already exists
in the registry, the new entry **completely replaces** the old entry
(frontmatter and body both replaced — no merging).

The final registry reflects the highest-priority definition for each name.

#### Scenario: Project overrides builtin
- **WHEN** builtin has `explorer` with `model=sonnet`
- **AND** project has `explorer` with `model=haiku`
- **THEN** `registry.lookup("explorer")` returns the project's definition
  (`model=haiku`)

#### Scenario: Plugin overrides project
- **WHEN** project has `summarizer` with `tools=[Read, Grep]`
- **AND** an MCP plugin provides `summarizer` with `tools=[Read, Write]`
- **THEN** `registry.lookup("summarizer")` returns the plugin's definition
  (`tools=[Read, Write]`)

#### Scenario: Same name from two plugins — last wins
- **WHEN** two MCP servers both expose an `explorer` agent
- **AND** both are in the `plugin_agents` list passed to `scan`
- **THEN** the later entry in the list wins (implementation-defined order,
  but the result is deterministic for a given input)

#### Scenario: Missing source dir is silently skipped
- **WHEN** `user_dir` does not exist
- **THEN** scan proceeds without error
- **AND** `scan_errors` does not include a "directory not found" error

### Requirement: Plugin source from MCP
MCP servers MAY expose Agent definitions as MCP `resources` of type
`agent`. The `fetch_plugin_agents(mcp_manager)` function MUST:

- For each MCP server in `mcp_manager.states` with `status=="ready"`,
  call `read_resource("agents://list")` to get a directory
- For each entry in the directory, call
  `read_resource("mcp://<server>/agents/<name>")` to get the detail
  (frontmatter dict + body string)
- Parse the frontmatter via `AgentFrontmatter.model_validate`
- Return a list of `AgentDef` with `source="plugin"` and
  `path=Path("<mcp://<server>/<name>>")`

Any exception (server crashed, resource missing, frontmatter invalid) MUST
be caught, the offending server skipped, and a `ScanError` recorded. The
function MUST NOT raise.

#### Scenario: Successful plugin load
- **WHEN** MCP server `my-server` is ready and exposes 2 agents
- **THEN** `fetch_plugin_agents(mcp_manager)` returns 2 `AgentDef` with
  `source="plugin"`

#### Scenario: MCP server crashed during fetch
- **WHEN** MCP server `my-server` has `status="ready"` at scan start
- **AND** `read_resource` raises an exception during fetch
- **THEN** the function catches the exception
- **AND** records a `ScanError` with the server name and exception message
- **AND** the function continues to fetch from other servers
- **AND** the function returns a list (possibly shorter) without raising

#### Scenario: MCP server exposes invalid frontmatter
- **WHEN** MCP server returns an agent with frontmatter missing `name`
- **THEN** the function catches the validation error
- **AND** records a `ScanError` for that specific agent
- **AND** other agents from the same server are still loaded

#### Scenario: MCP plugin not enabled
- **WHEN** `SubAgentsConfig.plugins_enabled == False`
- **THEN** `AgentRegistry.scan` does NOT call `fetch_plugin_agents`
- **AND** no plugin agents are loaded

### Requirement: Registry query API
The registry MUST expose:

- `lookup(name: str) -> AgentDef | None` — return the definition for a name
  or `None` if not found
- `list_visible() -> list[tuple[str, str, str]]` — return
  `(name, description, source)` tuples for non-hidden agents, sorted by name
- `list_all() -> list[AgentDef]` — return all agents (including hidden),
  sorted by name
- `reload(name: str) -> AgentDef` — re-read the file from disk and replace
  the in-memory entry; raise `KeyError` if name not present; on parse
  failure, retain old entry and append a `ScanError`
- `__contains__(name) -> bool` and `__len__() -> int`

#### Scenario: Lookup found
- **WHEN** `explorer` is registered
- **THEN** `registry.lookup("explorer")` returns its `AgentDef`
- **AND** `registry.lookup("nonexistent")` returns `None`
- **AND** `"explorer" in registry` is `True`

#### Scenario: list_visible excludes hidden
- **WHEN** registry contains `explorer` (hidden=False) and `secret` (hidden=True)
- **THEN** `list_visible()` returns only `explorer`
- **AND** `list_all()` returns both

#### Scenario: Reload after on-disk edit
- **WHEN** `explorer/AGENT.md` is edited to change `description`
- **AND** `registry.reload("explorer")` is called
- **THEN** the in-memory `AgentDef` has the new description
- **AND** no error is raised

#### Scenario: Reload with broken file retains old
- **WHEN** `explorer/AGENT.md` is edited to invalid YAML
- **AND** `registry.reload("explorer")` is called
- **THEN** the old in-memory `AgentDef` is retained
- **AND** a `ScanError` is appended to `scan_errors`

### Requirement: Boot-time scan errors do not crash
If any Agent file fails to parse, the file is skipped, a `ScanError` is
appended to `registry.scan_errors`, and the scan continues. The function
MUST NOT raise for individual file parse failures. The function MAY raise
`SystemExit` for boot-time validation errors (e.g., `tools` references a
tool that does not exist in the registry — if `valid_tools` is provided).

#### Scenario: Single bad file does not block others
- **WHEN** `agents/explorer/AGENT.md` has invalid frontmatter
- **AND** `agents/summarizer/AGENT.md` is valid
- **THEN** scan completes
- **AND** `registry.lookup("summarizer")` succeeds
- **AND** `registry.lookup("explorer")` returns `None`
- **AND** `registry.scan_errors` has 1 entry for `explorer/AGENT.md`

#### Scenario: Tool whitelist validation against ToolRegistry
- **WHEN** an Agent has `tools: [Read, WriteMadeUpTool]`
- **AND** `valid_tools=["Read", "Write", ...]` is passed to `scan`
- **THEN** scan raises `SystemExit` with a message naming the unknown tool

### Requirement: Two-phase visibility in system prompt
At boot time, the names and descriptions of all non-hidden Agents MUST be
injected into the Agent's `stable_system` under a "可用 Agent" section
(format: `- <role>: <description>`). The body of each Agent is NOT
included in the boot prompt — bodies are only loaded into the active
sub-Agent when `task(type="definition", role=...)` is invoked.

This mirrors Skill v1.0 two-phase loading and keeps the boot prompt size
bounded.

#### Scenario: Boot prompt lists agent names but not bodies
- **WHEN** 5 Agents are registered (some hidden, some not)
- **THEN** the system prompt contains a "可用 Agent" section
- **AND** the section has one line per non-hidden Agent (name + description)
- **AND** no Agent body is present in the system prompt
- **AND** hidden Agents are absent from the section

### Requirement: Source attribution preserved
`AgentDef.source` MUST be one of `"builtin" | "user" | "project" | "plugin"`.
The `TUI` and `/status` command MAY display the source for diagnostic
purposes (e.g., "explorer [plugin: my-mcp-server]").

#### Scenario: Source is set correctly
- **WHEN** an Agent is loaded from each source
- **THEN** `agent.source` matches the source it was loaded from
- **AND** `list_visible()` includes the source string
