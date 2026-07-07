## MODIFIED Requirements

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