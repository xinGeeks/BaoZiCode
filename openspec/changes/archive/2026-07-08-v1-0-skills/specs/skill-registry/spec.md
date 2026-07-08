# skill-registry Specification (v1.0)

## Purpose
A single source of truth for Skill metadata. At boot time, scan three
directories (builtin, user-global, project-local) in priority order, parse
YAML frontmatter + Markdown body for each file, and merge into a single
`SkillRegistry` keyed by Skill name. Parsing failures on individual files
MUST be skipped (not block boot); the error MUST be logged to stderr as a
single WARN line. After boot, the Agent's system prompt sees only `name +
description` for each Skill (no body); the body is loaded on demand.

## Requirements

### Requirement: SkillDef data shape
A Skill MUST be represented as a `SkillDef` with these fields:

- `name: str` — primary identifier, lowercase a-z + `-` only, unique across all
  3 directories (after priority merge, project > user > builtin)
- `description: str` — one-line summary for boot-time prompt injection AND
  `/skill list` display
- `mode: Literal["shared", "independent"]` — execution mode (default `shared`)
- `allowed_tools: list[str] | None` — tool whitelist (default `None` = no
  restriction)
- `history_bubbles: int` — only used when `mode="independent"`, number of
  recent main-conversation bubbles to pass to the sub-Agent (default 0,
  range 0-50)
- `model: str | None` — optional model override, only used in
  `mode="independent"` (default `None` = inherit from main Agent)
- `body: str` — full Markdown body, with `{var}` placeholders preserved (not
  substituted at this stage)
- `source: Literal["builtin", "user", "project"]` — which directory the file
  came from (used for `/skill list` display + hot-reload decisions)
- `path: Path` — absolute path of the source file (used for hot-reload)

#### Scenario: Minimal valid Skill
- **WHEN** a Skill file has frontmatter with only `name` and `description`,
  no `mode`, no `allowed-tools`
- **THEN** the Skill is registered with `mode="shared"`, `allowed_tools=None`,
  `history_bubbles=0`, `model=None`

#### Scenario: Invalid frontmatter rejected
- **WHEN** a Skill file's YAML frontmatter is malformed OR a required field
  is missing OR `mode` is not in `{"shared", "independent"}`
- **THEN** that file is skipped, NOT added to the registry
- **AND** a WARN line is logged to stderr including the file path and a
  one-line reason

#### Scenario: Unknown tool in allowed-tools
- **WHEN** a Skill's `allowed-tools` lists a tool name that does NOT exist
  in the current `ToolRegistry` (built-in 7 + v0.6 MCP tools)
- **THEN** the Skill is rejected at boot with a HARD ERROR (not skipped)
- **AND** the error message names the unknown tool and the Skill file path
- **AND** the process exits with `SystemExit` (consistent with v0.9
  alias-collision boot panic policy)

#### Scenario: Invalid Skill name format
- **WHEN** a Skill's `name` field contains uppercase, digits, or symbols
- **THEN** the file is rejected with `ValueError`, skipped with WARN

### Requirement: Three-level priority merge
At boot, the registry MUST scan three directories in this fixed order:

1. **Builtin** — `baozicode/skills/builtin/<name>/SKILL.md` (package data)
2. **User-global** — `~/.config/baozicode/skills/<name>/SKILL.md`
3. **Project-local** — `<project_root>/.baozicode/skills/<name>/SKILL.md`

When two levels declare the same `name`, the higher-priority level (project
> user > builtin) **completely replaces** the lower-priority definition.
Frontmatter fields are NOT merged; the project-level file is taken as-is.

#### Scenario: Project overrides builtin
- **WHEN** builtin has `commit/SKILL.md` AND project has `.baozicode/skills/commit/SKILL.md`
- **THEN** only the project version is registered
- **AND** `skill.source = "project"`
- **AND** `skill.path` points to the project file

#### Scenario: User overrides builtin, no project version
- **WHEN** builtin has `review/SKILL.md` AND user has `~/.config/baozicode/skills/review/SKILL.md`
- **AND** no project-level file
- **THEN** the user version wins
- **AND** `skill.source = "user"`

#### Scenario: Three built-in samples always present
- **WHEN** the user has not created any custom Skills
- **THEN** `commit`, `review`, `test` are all registered (from builtin)
- **AND** they appear in `registry.list_visible()` in alphabetical order

### Requirement: Parse-failure isolation
A single file failing to parse MUST NOT prevent other files from being
loaded. The registry collects `(name, SkillDef | error_msg)` tuples during
the scan; only successfully parsed Skills are added to the lookup table.

#### Scenario: One file broken, three files valid
- **WHEN** four Skill files exist; one has a malformed frontmatter
- **THEN** three Skills are registered
- **AND** the broken one is logged as `WARN: skill 'foo' parse failed: ...`
- **AND** the App continues to boot

#### Scenario: All files broken
- **WHEN** every Skill file in all three directories fails to parse
- **THEN** the registry is empty
- **AND** one consolidated WARN line is printed (NOT one per file)
- **AND** the App boots normally with no Skills available

### Requirement: Boot-time visibility list
The `SkillRegistry` MUST expose a `list_visible() -> list[(name, description, source)]`
method for the boot-time system prompt section. The list MUST:
- Be sorted alphabetically by `name`
- Exclude any Skill with `hidden: true` frontmatter (escape hatch for
  internal-only Skills; not used in v1.0 samples but field MUST be supported)

#### Scenario: list_visible output
- **WHEN** the registry has 5 Skills (3 builtin + 1 user + 1 project)
- **THEN** `list_visible()` returns 5 tuples, sorted alphabetically
- **AND** each tuple has `(name, description, source)`

#### Scenario: Hidden Skill excluded from list_visible
- **WHEN** a Skill has `hidden: true` in its frontmatter
- **THEN** it is still loadable via `load_skill(name)` (if the user knows the name)
- **AND** it is NOT shown in `list_visible()` (no boot-time prompt injection)
- **AND** it is NOT shown in `/skill list`

### Requirement: Hot reload via /skill reload
The registry MUST support `reload(name: str) -> SkillDef` that re-reads the
file from `path` and re-parses it. On reload:
- The new definition replaces the old in the lookup table
- If parsing fails, the OLD definition is kept (no destructive reload)
- The reload is logged at INFO level

#### Scenario: Reload valid Skill
- **WHEN** user edits `~/.config/baozicode/skills/review/SKILL.md` and runs
  `/skill reload review`
- **THEN** the registry re-reads the file
- **AND** the new description is shown in `/skill list`
- **AND** if `review` was already activated, the active Skill body is
  replaced with the new body (no need to `/skill unload` + re-load)

#### Scenario: Reload broken Skill keeps old version
- **WHEN** user edits the Skill file with broken YAML
- **AND** runs `/skill reload review`
- **THEN** an error is shown: `WARN: skill 'review' reload failed: <reason>`
- **AND** the old version remains in the registry and active state

#### Scenario: Reload non-existent Skill
- **WHEN** `/skill reload nonexistent` is run
- **THEN** an error is shown: `WARN: no such skill: nonexistent`
- **AND** no state is changed
