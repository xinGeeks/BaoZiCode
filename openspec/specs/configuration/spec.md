# configuration Specification

## Purpose
TBD - created by archiving change v0-1-tui-multiturn-streaming. Update Purpose after archive.
## Requirements
### Requirement: YAML config file is loaded at startup
The system MUST load a YAML configuration file at startup and validate it against the `AppConfig` Pydantic schema before any LLM client is constructed.

#### Scenario: Valid config loads successfully
- **WHEN** the config file exists, is well-formed YAML, and matches the `AppConfig` schema
- **THEN** the system starts up with the configured backend and model

#### Scenario: Invalid config surfaces a clear error
- **WHEN** the config file has a schema violation (e.g., missing required field, unknown `backend` value)
- **THEN** the system prints a human-readable error to stderr indicating which field is invalid
- **AND** exits with a non-zero exit code without starting the TUI

#### Scenario: Missing config file produces actionable guidance
- **WHEN** no config file is found at any of the search locations
- **THEN** the system prints a message that includes the path where `config.example.yaml` should be copied to
- **AND** exits with a non-zero exit code

### Requirement: Config file search order
The system MUST search for the config file in the following order and use the first one found:
1. The path provided via `--config <path>` CLI argument
2. `./config.yaml` in the current working directory
3. `~/.config/baozicode/config.yaml` in the user's home directory

#### Scenario: --config flag wins over default locations
- **WHEN** the user runs `baozicode --config /tmp/my.yaml`
- **THEN** the system loads `/tmp/my.yaml` and ignores any `./config.yaml` or `~/.config/baozicode/config.yaml` that may also exist

#### Scenario: Project-local config wins over user-global config
- **WHEN** both `./config.yaml` and `~/.config/baozicode/config.yaml` exist
- **AND** `--config` is not provided
- **THEN** the system loads `./config.yaml`

### Requirement: API keys are read from .env file
The system MUST read API keys from a `.env` file (loaded by `python-dotenv`), NOT from the YAML file, to avoid accidentally committing secrets to version control.

#### Scenario: .env file populates env vars
- **WHEN** a `.env` file exists in the current directory and contains `ANTHROPIC_API_KEY=sk-ant-xxx`
- **THEN** the environment variable `ANTHROPIC_API_KEY` is set to `sk-ant-xxx` before the LLM client is constructed

#### Scenario: Missing .env key is reported
- **WHEN** the YAML references `${ANTHROPIC_API_KEY}` but the `.env` file does not define it (and the env var is not set elsewhere)
- **THEN** the system prints an error naming the missing variable
- **AND** exits with a non-zero exit code

### Requirement: ${ENV_VAR} placeholders in YAML are substituted
The system MUST support `${VAR_NAME}` placeholder syntax in the YAML config for string fields, and MUST replace these placeholders with the corresponding environment variable values loaded from `.env` (or the process environment).

#### Scenario: Placeholder is replaced
- **WHEN** the YAML contains `api_key: ${ANTHROPIC_API_KEY}` and `ANTHROPIC_API_KEY=sk-ant-xxx` is loaded
- **THEN** the validated `AppConfig.anthropic.api_key` field equals `sk-ant-xxx`

#### Scenario: Unresolved placeholder is reported
- **WHEN** the YAML contains `${MISSING_VAR}` and no such env var is defined
- **THEN** the config loading fails with a clear error message

### Requirement: Config schema distinguishes all four backends
The system MUST validate that the YAML contains configuration blocks for ALL FOUR backends — `anthropic`, `openai`, `minimax`, and `deepseek` — even when only one is active, so the user can switch backends by editing one field.

#### Scenario: All four backend blocks required
- **WHEN** the YAML is loaded
- **THEN** the schema MUST require `anthropic:`, `openai:`, `minimax:`, and `deepseek:` sections, each with `api_key` and `model` fields

#### Scenario: Switching backend is a single-field edit
- **WHEN** the user edits only the `backend:` field in `config.yaml` to any of `anthropic`, `openai`, `minimax`, or `deepseek` and restarts
- **THEN** the system starts up using the new backend without code changes

### Requirement: Default values are sensible
The system MUST provide these defaults when fields are omitted from the YAML:
- `system_prompt`: "You are BaoZiCode, a helpful AI coding assistant."
- `base_url` for Anthropic: `https://api.anthropic.com`
- `base_url` for OpenAI: `https://api.openai.com/v1`
- `base_url` for MiniMax: `https://api.minimaxi.com/v1` (placeholder, override recommended)
- `base_url` for DeepSeek: `https://api.deepseek.com/v1`
- `agent.context_window_tokens` (v0.7): `128_000` (default context window used when no per-backend override is set; must be a positive integer)
- `agent.compaction.per_block_threshold` (v0.7): `8192` (Layer-1 single-block offload threshold in bytes)
- `agent.compaction.per_message_threshold` (v0.7): `20480` (Layer-1 per-message aggregate offload threshold in bytes)
- `agent.compaction.recent_window_min_messages` (v0.7): `5` (Layer-2 tail-window minimum message count)
- `agent.compaction.recent_window_tokens` (v0.7): `10000` (Layer-2 tail-window minimum token count)
- `agent.compaction.reserve_tokens_auto` (v0.7): `13000` (auto-trigger safety margin)
- `agent.compaction.reserve_tokens_manual` (v0.7): `3000` (manual-trigger tighter margin)
- `agent.compaction.max_summary_tokens` (v0.7): `2000` (cap on summary LLM output)
- `agent.compaction.max_consecutive_failures` (v0.7): `3` (circuit-breaker threshold for CompactError)
- `backend.<name>.context_window_tokens` (v0.7): `None` (per-backend override; `None` means "fall back to `agent.context_window_tokens`")

#### Scenario: Omitted system_prompt uses default
- **WHEN** the YAML omits the `system_prompt` field
- **THEN** the loaded config has `system_prompt` equal to the default string

#### Scenario: Omitted backend base_url uses provider default
- **WHEN** a backend's `base_url` is omitted from the YAML
- **THEN** the loaded `BackendConfig.base_url` falls back to that backend's provider default
- **AND** if the YAML explicitly sets `base_url: null`, the same default is used

#### Scenario: Omitted agent.context_window_tokens uses 128K
- **WHEN** the YAML omits the `agent.context_window_tokens` field
- **THEN** the loaded `AgentConfig.context_window_tokens` equals `128_000`

#### Scenario: Omitted agent.compaction uses sensible defaults
- **WHEN** the YAML omits the `agent.compaction` block entirely
- **THEN** the loaded `CompactionConfig` uses the documented defaults for every field (8K/20K thresholds, 5 msg / 10K token tail, 13K/3K reserves, 2K max summary, 3-strike breaker)

#### Scenario: Per-backend context_window_tokens override
- **WHEN** `agent.context_window_tokens: 200_000` and `anthropic.context_window_tokens: 128_000`
- **THEN** the effective context window for Anthropic is 128 K
- **AND** for OpenAI (no override) it is 200 K
- **AND** for MiniMax and DeepSeek (no overrides) it is 200 K

#### Scenario: Schema validation rejects negative context window
- **WHEN** user sets `agent.context_window_tokens: -1` in YAML
- **THEN** Pydantic validation fails with a clear error message naming the field and the bad value
- **AND** the system does not start

#### Scenario: Schema validation rejects zero per-block threshold
- **WHEN** user sets `agent.compaction.per_block_threshold: 0` in YAML
- **THEN** Pydantic validation fails (positive integer required)
- **AND** the system does not start

### Requirement: Permissions block configures tool-calling policy
The system MUST accept permission configuration via two complementary mechanisms: (1) an optional `permissions:` block in the main `config.yaml` (legacy v0.2 fields); (2) up to three `permissions*.yaml` files for the new five-layer defense. When no `permissions*.yaml` file exists in any layer, the system MUST fall back to the v0.2 `Permissions.auto_allow / deny / batch_confirm / bash_locked_cwd` fields, preserving existing behavior. When any `permissions*.yaml` exists, the new structure takes precedence and a deprecation warning is logged about any legacy `permissions:` fields being ignored.

#### Scenario: Default policy when no permission config exists
- **WHEN** neither `config.yaml:permissions:` nor any `permissions*.yaml` exists
- **THEN** the system uses v0.2 defaults: every Write/Edit/Bash call requires per-call confirmation, Read/Grep/Glob/WebFetch auto-allowed, `batch_confirm=false`, `bash_locked_cwd=false`

#### Scenario: New YAML takes precedence over legacy fields
- **WHEN** `config.yaml` contains `permissions: {auto_allow: [Read]}` AND `<project>/.baozicode/permissions.yaml` exists with rules
- **THEN** the new YAML rules are used
- **AND** a deprecation warning is logged: "permissions.auto_allow in config.yaml is ignored when permissions YAML files exist"

### Requirement: Three permission YAML files load in priority order
The system MUST search for permission YAML files in three locations and merge their contents in the order: (1) `~/.config/baozicode/permissions.yaml` (user global — lowest priority), (2) `<project_root>/.baozicode/permissions.yaml` (project — middle priority), (3) `<project_root>/.baozicode/permissions.local.yaml` (local — highest priority). A missing file MUST be silently skipped. A malformed file MUST log a warning and be skipped (other files still load).

#### Scenario: Only user-global file exists
- **WHEN** only `~/.config/baozicode/permissions.yaml` exists
- **THEN** the loaded permissions contain rules from that file, tagged `source="user_global"`
- **AND** project and local layers are empty

#### Scenario: All three files present
- **WHEN** all three `permissions*.yaml` files exist
- **THEN** the loaded permissions contain rules from all three, each tagged with their respective `source`
- **AND** evaluation uses the priority order: local > project > user-global

#### Scenario: Project file is malformed
- **WHEN** `<project>/.baozicode/permissions.yaml` exists but contains invalid YAML syntax
- **THEN** the system logs a warning naming the file and the YAML error
- **AND** the file is skipped
- **AND** user-global and local files (if present) still load

### Requirement: Permissions YAML schema is validated
The system MUST validate each `permissions*.yaml` file against a Pydantic schema before use. The schema MUST require `rules: list[PermissionRule]` (may be empty) and accept optional `mode: Literal["strict","default","permissive"]` (default `default`). Each `PermissionRule` MUST have `tool: str` (non-empty), `pattern: str` (non-empty, fnmatch glob syntax), `decision: Literal["allow","deny"]`. Unknown top-level keys MUST be silently ignored (`extra="ignore"`).

#### Scenario: Valid YAML loads
- **WHEN** `<project>/.baozicode/permissions.yaml` contains valid `rules` and `mode` fields
- **THEN** the file is parsed and rules are added to the merged rule list

#### Scenario: Invalid rule is rejected
- **WHEN** a rule has empty `tool` or empty `pattern`
- **THEN** the system MUST log a validation error naming the offending rule
- **AND** skip that rule (other rules in the same file still load)

#### Scenario: Missing rules key uses empty list
- **WHEN** a `permissions*.yaml` contains only `mode: default` with no `rules:` key
- **THEN** the file contributes no rules but its mode may still apply if it is the highest-priority file declaring a mode

#### Scenario: Unknown decision value rejected
- **WHEN** a rule has `decision: maybe` (not in the Literal)
- **THEN** the file fails validation with a schema error

### Requirement: Permission mode field controls fallback behavior
The system MUST support a top-level `mode` field in any `permissions*.yaml` file with values `strict`, `default`, or `permissive`. When multiple files declare `mode`, the value from the highest-priority file (local > project > user-global) wins. The default when no file declares `mode` is `default`.

#### Scenario: Project mode wins over user-global mode
- **WHEN** user-global YAML declares `mode: strict` and project YAML declares `mode: permissive`
- **THEN** the active mode is `permissive` (project overrides user-global)

#### Scenario: Local mode wins over project mode
- **WHEN** project YAML declares `mode: default` and local YAML declares `mode: strict`
- **THEN** the active mode is `strict` (local overrides project)

#### Scenario: Session mode via /permissions mode overrides YAML
- **WHEN** the user runs `/permissions mode permissive` in the TUI
- **THEN** the session-effective mode becomes `permissive` for the current Agent session
- **AND** the YAML-declared mode is preserved on disk but bypassed for the session
- **AND** restarting BaoZiCode reverts to the YAML-declared mode

### Requirement: README documents permissions YAML paths
The project README MUST document the three `permissions*.yaml` file paths, the merge priority, the rule schema, and recommend adding `.baozicode/permissions.local.yaml` to `.gitignore`.

#### Scenario: README has permissions section
- **WHEN** a user reads the project README
- **THEN** there is a "Permissions" section listing the three YAML file paths and an example YAML file with mode + rules

#### Scenario: .gitignore example provided
- **WHEN** a user copies the example `.gitignore` snippets from the README
- **THEN** `.baozicode/permissions.local.yaml` is included in the recommended ignore pattern

### Requirement: MemoryConfig schema
The system MUST expose a `MemoryConfig` Pydantic model in `baozicode/config/schema.py` with the following fields and defaults:

| Field | Type | Default | Validation |
|---|---|---|---|
| `enabled` | `bool` | `True` | none |
| `user_dir` | `Path` | `~/.baozicode/memory` | expanduser applied at load |
| `project_dir` | `Path` | `.baozicode/memory` | resolved against project_root at load |
| `index_max_lines` | `int` | `200` | ≥ 50 |
| `index_max_bytes` | `int` | `25600` | ≥ 1024 |
| `warning_lines` | `int` | `180` | ≥ 25, must be < `index_max_lines` |
| `warning_bytes` | `int` | `22528` | must be < `index_max_bytes` |
| `recent_turns_for_update` | `int` | `5` | ≥ 1 |
| `auto_compress_per_session` | `int` | `1` | ≥ 0 |

The system MUST attach `MemoryConfig` to `AppConfig.memory` with default `MemoryConfig()`.

#### Scenario: Defaults load successfully
- **WHEN** a config.yaml omits the `memory:` block
- **THEN** `AppConfig.memory` is a `MemoryConfig` with all default values

#### Scenario: Custom thresholds accepted
- **WHEN** `memory.index_max_lines: 300, memory.warning_lines: 250`
- **THEN** the config loads successfully

#### Scenario: Logical invariant rejected
- **WHEN** `memory.warning_lines: 200` and `memory.index_max_lines: 200`
- **THEN** Pydantic validation fails with `warning_lines must be < index_max_lines`

#### Scenario: Below minimum rejected
- **WHEN** `memory.index_max_lines: 10`
- **THEN** Pydantic validation fails with `index_max_lines must be ≥ 50`

### Requirement: SessionConfig schema
The system MUST expose a `SessionConfig` Pydantic model with:

| Field | Type | Default | Validation |
|---|---|---|---|
| `enabled` | `bool` | `True` | none |
| `dir` | `Path` | `.baozicode/sessions` | resolved against project_root at load |
| `retention_days` | `int` | `30` | ≥ 1 |

The system MUST attach `SessionConfig` to `AppConfig.sessions` with default `SessionConfig()`.

#### Scenario: Defaults load successfully
- **WHEN** a config.yaml omits the `sessions:` block
- **THEN** `AppConfig.sessions` is a `SessionConfig` with default values

#### Scenario: Retention of 0 rejected
- **WHEN** `sessions.retention_days: 0`
- **THEN** Pydantic validation fails with `retention_days must be ≥ 1`

### Requirement: AgentConfig.time_gap_threshold_hours
The system MUST expose `time_gap_threshold_hours: int = 8` on `AgentConfig` with `gt=0` validation. The system MUST pass this value to the resume flow for time-gap reminder insertion.

#### Scenario: Default 8 hours
- **WHEN** `agent.time_gap_threshold_hours` is not specified
- **THEN** it defaults to 8

#### Scenario: Custom threshold accepted
- **WHEN** `agent.time_gap_threshold_hours: 4`
- **THEN** the threshold is 4 hours; gaps > 4 hours trigger the reminder

#### Scenario: Zero rejected
- **WHEN** `agent.time_gap_threshold_hours: 0`
- **THEN** Pydantic validation fails with `time_gap_threshold_hours must be > 0`

### Requirement: Deprecated memory_path with fallback
The system MUST keep `AppConfig.memory_path: Path = Path("~/.config/baozicode/memory.md")` readable. When this field is set to a non-default value, the loader MUST emit a stderr warning:
```
WARN: memory_path is deprecated, move to <user_dir>/MEMORY.md + <project_dir>/MEMORY.md (will be removed in v0.9)
```

The system MUST keep the field functional as a fallback: when both new memory directories are empty AND `memory_path` points to an existing file, the system MUST read that file's content as the user-global memory index.

#### Scenario: Deprecated field triggers warning
- **WHEN** `memory_path: ~/my-notes.md` is set in YAML
- **THEN** startup emits the deprecation warning to stderr
- **AND** `app.startup` proceeds normally

#### Scenario: Fallback used when new dirs empty
- **WHEN** both `~/.baozicode/memory/` and `<project>/.baozicode/memory/` are empty
- **AND** `memory_path: ~/my-notes.md` points to an existing file
- **THEN** the system reads `~/my-notes.md` content as the user-global memory index
- **AND** the system prompt includes that content under `## 长期记忆 (用户级)`

#### Scenario: New dirs take priority over deprecated path
- **WHEN** `~/.baozicode/memory/MEMORY.md` exists with content
- **AND** `memory_path` is also set
- **THEN** the new directory's content is used
- **AND** `memory_path` is ignored (deprecation warning still printed)

### Requirement: Example config.yaml snippet
The system MUST update `config.example.yaml` to include the following comment block as documentation:

```yaml
# v0.8 memory + sessions configuration
memory:
  enabled: true                    # set false to disable all memory features
  user_dir: ~/.baozicode/memory    # cross-project user-global notes
  project_dir: .baozicode/memory   # project-local notes (committed if desired)
  index_max_lines: 200             # hard limit on MEMORY.md
  index_max_bytes: 25600           # 25 KB hard limit
  warning_lines: 180               # soft warning threshold
  warning_bytes: 22528             # ~22 KB soft warning
  recent_turns_for_update: 5       # turns fed to memory-update LLM
  auto_compress_per_session: 1     # max automatic LLM compressions per session

sessions:
  enabled: true                    # set false to disable JSONL session archive
  dir: .baozicode/sessions         # per-project sessions directory
  retention_days: 30               # JSONLs older than this are cleaned at startup

agent:
  time_gap_threshold_hours: 8      # gap after which /resume inserts reminder
```

#### Scenario: config.example.yaml contains the v0.8 block
- **WHEN** a user reads `config.example.yaml`
- **THEN** it includes commented examples for the `memory:`, `sessions:`,
  and `agent.time_gap_threshold_hours` fields as documentation

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

### Requirement: WorktreeConfig schema (NEW)

`SubAgentsConfig` MUST expose an optional nested field
`worktree: WorktreeConfig | None` in v1.3. The system MUST use the
following Pydantic model definition for `WorktreeConfig`:

```python
class WorktreeConfig(BaseModel):
    enabled: bool = True  # 总开关(冗余,real decision 来自 frontmatter isolation)
    link_paths: list[str] = [".venv", "node_modules", ".cargo"]
    copy_paths: list[str] = [
        ".baozicode/BaoZiCode.md",
        ".env",
        "config.yaml",
        ".claude/",
    ]
    retention_minutes: int = 60
    daemon_interval_seconds: int = 60
    max_concurrent_worktrees: int = 5
```

#### Scenario: SubAgentsConfig with worktree block
- **WHEN** YAML 含 `subagents.worktree.link_paths: [.venv,
  custom_lib]`(覆盖默认)
- **THEN** `AppConfig.subagents.worktree.link_paths == [".venv",
  "custom_lib"]`(覆盖 Pydantic 默认 list)

#### Scenario: SubAgentsConfig without worktree block
- **WHEN** YAML 没 `subagents.worktree:` 子键
- **THEN** `AppConfig.subagents.worktree is None`;bootstrap 路
  径用 `WorktreeConfig()` 全默认对象兜底

#### Scenario: Pydantic validates bad types
- **WHEN** YAML 含 `subagents.worktree.retention_minutes: -5`
- **THEN** `AppConfig` 加载报错 Pydantic `greater_than_equal
  错误(retention_minutes 必须 ≥ 0)`

#### Scenario: Max concurrent cap enforced
- **WHEN** YAML 含 `subagents.worktree.max_concurrent_worktrees:
  100`
- **THEN** `WorktreeManager` 创建第 `max_concurrent_worktrees +
  1` 个 worktree 时,**默认**仍允许(只 warn + 继续);worker
  count 可通过 `/status` 看到

### Requirement: config.example.yaml update (NEW)

`config.example.yaml` MUST contain an inline, documented example block
for `subagents.worktree` in v1.3:

```yaml
subagents:
  enabled: true
  max_concurrent: 5
  default_timeout_seconds: 300
  task_retention_minutes: 5
  plugins_enabled: true
  background_whitelist: [Read, Grep, Glob, WebFetch, notify_complete]

  # v1.3 嵌套块 — Worktree Isolation 配置
  worktree:
    enabled: true
    link_paths:
      - .venv
      - node_modules
      - .cargo
    copy_paths:
      - .baozicode/BaoZiCode.md
      - .env
      - config.yaml
      - .claude/
    retention_minutes: 60
    daemon_interval_seconds: 60
    max_concurrent_worktrees: 5
```

#### Scenario: User copies example to config.yaml
- **WHEN** 用户从 `config.example.yaml` 复制 `subagents.worktree`
  段到自己 `config.yaml`
- **THEN** `WorktreeConfig` 加载该段成功 + `WorktreeInitializer`
  按 `link_paths` / `copy_paths` 初始化 worktree

