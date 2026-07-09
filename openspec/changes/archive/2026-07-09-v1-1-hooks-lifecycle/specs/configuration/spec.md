# configuration Specification (v1.1 deltas)

## Purpose
Adds v1.1 `hooks:` block to `AppConfig` and `config.yaml`. The hooks block follows the
same embedded-block pattern as `instructions` / `memory` / `sessions` / `commands` /
`skills`. The bootstrap order is updated so hooks are loaded right after permissions.

## ADDED Requirements

### Requirement: AppConfig.hooks field
The system MUST extend `baozicode/config/schema.py` `AppConfig` with an optional
`hooks: list[HookDefYaml] | None = None` field. When `None` or omitted from YAML,
the v1.1 hook system is disabled and BaoZiCode behaves identically to v1.0.

`HookDefYaml` MUST be a Pydantic model with fields:
- `id: str` (required, non-empty, unique across the file)
- `event: Literal["session.start", "session.end", "turn.start", "turn.end",
  "message.received", "message.sent", "tool.pre", "tool.post",
  "system.error", "system.compaction", "system.cancel"]`
- `if_: ConditionYaml | None = Field(None, alias="if")` — the `if` YAML key
  conflicts with Python reserved word, hence the alias
- `actions: list[ActionYaml]` (required, ≥ 1)
- `async_: bool = Field(False, alias="async")` — async allowed only for `tool.post`
- `timeout_seconds: int = 30` (≥ 1, ≤ 300)
- `run_once: bool = False`

The `id` uniqueness and the `async: true` + `event ∈ {tool.pre, session.*, turn.*,
message.*, system.*}` constraint MUST be enforced during `HookRegistry.freeze()`,
not at AppConfig load time (other rules may load even if some hooks fail validation,
see "Configuration validation surfaces hook errors clearly").

#### Scenario: Default AppConfig has no hooks
- **WHEN** a config.yaml omits the `hooks:` block
- **THEN** `AppConfig.hooks is None`
- **AND** the v1.1 hook system is fully disabled
- **AND** BaoZiCode behaves identically to v1.0

#### Scenario: Hook block with one rule parses successfully
- **WHEN** config.yaml has:
  ```yaml
  hooks:
    - id: audit-bash-pre
      event: tool.pre
      actions:
        - action: shell
          command: "echo audit"
  ```
- **THEN** `AppConfig.hooks` is a list of 1 `HookDefYaml` with `id="audit-bash-pre"`,
  `event="tool.pre"`, `actions=[ActionYaml(action="shell", command="echo audit")]`

#### Scenario: Empty actions list rejected at freeze time
- **WHEN** a hook has `actions: []`
- **THEN** `HookRegistry.freeze()` raises `HookValidationError` naming the hook id
- **AND** the system exits with non-zero status

#### Scenario: Duplicate hook id rejected at freeze time
- **WHEN** two hooks in `AppConfig.hooks` share the same `id`
- **THEN** `HookRegistry.freeze()` raises `HookValidationError` listing both occurrences
- **AND** the system exits

### Requirement: HookDefYaml conditions and actions sub-schemas
`ConditionYaml` MUST support `all: list[MatcherYaml]` or `any: list[MatcherYaml]`
(both optional, exactly one MAY be present; both present is a validation error at
freeze time). `MatcherYaml` is a Pydantic model accepting one of:
- `tool: str` — exact tool name match
- `arg.<name>: MatchValue` — value matcher (see below) on a tool argument named `<name>`

`MatchValue` is a tagged union with discriminator being the matcher kind:
- `kind="exact"` + `value: str`
- `kind="glob"` + `value: str` (fnmatch)
- `kind="regex"` + `value: str`
- `kind="not_exact"` + `value: str`
- `kind="not_glob"` + `value: str`
- `kind="not_regex"` + `value: str`

When `ConditionYaml` is omitted or empty, the hook fires unconditionally
(equivalent to `if: {}`).

`ActionYaml` is a tagged union with discriminator `action`:
- `kind="shell"` + `command: str`, optional `timeout_seconds: int = 30`,
  optional `enqueue: bool = False`
- `kind="http"` + `url: str`, optional `method: Literal["GET","POST"] = "GET"`,
  optional `body: dict | None`, optional `parse_expr: str` (required for deny capability),
  optional `deny_reason: str | None`
- `kind="prompt"` + `content: str`, optional `slot: Literal["sticky_reminder",
  "stable_system", "temp"] = "sticky_reminder"`
- `kind="sub-agent"` + `goal: str`, optional `parse_expr: str` (required for deny
  capability), optional `deny_reason: str | None`

#### Scenario: Condition with all + exact matcher parses
- **WHEN** YAML has:
  ```yaml
  if:
    all:
      - tool: "Bash"
      - arg.command: {exact: "ls"}
  ```
- **THEN** `ConditionYaml` has `all=[Matcher(tool="Bash"), Matcher(arg="command", exact="ls")]`

#### Scenario: Condition with glob matcher parses
- **WHEN** YAML has `arg.path: {glob: "src/**/*.py"}`
- **THEN** `MatcherYaml` has `kind="glob"`, `value="src/**/*.py"`

#### Scenario: Action.shell has command field
- **WHEN** YAML has `actions: [{action: shell, command: "echo hi"}]`
- **THEN** `ActionYaml.kind == "shell"` and `command == "echo hi"`

#### Scenario: Action.http with parse_expr parses
- **WHEN** YAML has:
  ```yaml
  actions:
    - action: http
      url: "http://risk.example/check"
      parse_expr: "res.deny = body.risk > 0.8; res.deny_reason = body.label"
  ```
- **THEN** `ActionYaml.kind == "http"` with `parse_expr` and `deny_reason` populated
- **AND** validation accepts it (parse_expr is present, no `deny` field — http uses
  parse_expr to set `res.deny`)

### Requirement: Hooks embedded in config.yaml under top-level `hooks:` key
The system MUST support a top-level `hooks:` block in `config.yaml`, parsed into
`AppConfig.hooks`. The block is a YAML list (not a dict), so the YAML preserves
declaration order — hook execution order in the v1.1 dispatcher follows YAML
declaration order within the same event.

The system MUST NOT introduce a separate `hooks.yaml` file in v1.1 — hooks are part of
the main config block to keep loader responsibilities aligned with `instructions` /
`memory` / `sessions` / `commands` / `skills`. (Future v1.1.1 may split this if
real-world hook counts exceed practical YAML size.)

#### Scenario: Hooks at top level of config.yaml
- **WHEN** config.yaml has:
  ```yaml
  backend: anthropic
  hooks:
    - id: rule-1
      event: tool.pre
      actions: [...]
    - id: rule-2
      event: turn.start
      actions: [...]
  ```
- **THEN** `AppConfig.hooks` is the list `[rule-1, rule-2]` in declaration order

#### Scenario: No separate hooks.yaml file scanned
- **WHEN** the loader runs, it MUST NOT scan for `<project>/.baozicode/hooks.yaml` or
  `~/.config/baozicode/hooks.yaml`
- **AND** any such file in the project root is ignored silently (no warning, no error)

### Requirement: Bootstrap order is permissions → hooks → instructions → memory → sessions → commands → skills
The system MUST extend the bootstrap sequence in `BaoZiCodeApp.startup()` (or
equivalent) so that hooks are loaded right after permissions and before instructions:

```
bootstrap order:
  1. config (AppConfig.load)         # parses YAML, including hooks: block
  2. permissions                     # v0.5 five-layer defense
  3. hooks (NEW in v1.1)             # HookRegistry.freeze() + create_dispatcher()
  4. instructions                    # v0.8
  5. memory                          # v0.8
  6. sessions                        # v0.8
  7. commands                        # v0.9
  8. skills                          # v1.0
  9. Agent construction (passes hook_registry=registry.create_dispatcher())
```

`HookRegistry.freeze()` MUST run during step 3; any validation error (duplicate id,
invalid event, async on tool.pre, etc.) MUST cause `SystemExit` with a clear error
message naming the offending hook id and field.

#### Scenario: Bootstrap order preserves v1.0 when hooks absent
- **WHEN** `AppConfig.hooks is None`
- **THEN** step 3 is a no-op (no registry created, no dispatcher instantiated)
- **AND** steps 4-9 proceed as in v1.0
- **AND** `Agent` is constructed with `hook_registry=None`

#### Scenario: Bootstrap fails clearly on bad hook config
- **WHEN** `HookRegistry.freeze()` finds a hook with `id="bad"`, `event="tool.pre"`,
  `async=True`
- **THEN** startup prints: `ERROR: hooks[bad]: async not allowed for tool.pre events`
- **AND** `SystemExit(1)` is raised
- **AND** no Agent is constructed

### Requirement: Configuration validation surfaces hook errors clearly
The system MUST collect all hook validation errors before failing (do not bail on
the first error). The startup error message MUST list every offending hook id and
the specific field that failed. Format:

```
ERROR: hooks validation failed (3 errors):
  - hooks[audit-bash-pre]: empty actions list
  - hooks[bad-async]: async not allowed for tool.pre events
  - hooks[no-id]: missing required field 'id'
```

#### Scenario: Multiple errors collected
- **WHEN** 3 hooks in config.yaml each have a different validation problem
- **THEN** startup collects all 3 errors
- **AND** exits once with the consolidated message above
- **AND** does not abort at the first error

#### Scenario: Single error shown clearly
- **WHEN** exactly 1 hook has a validation problem
- **THEN** startup prints:
  ```
  ERROR: hooks validation failed (1 error):
    - hooks[bad]: <reason>
  ```