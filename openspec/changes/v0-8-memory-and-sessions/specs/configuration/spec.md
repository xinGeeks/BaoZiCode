# configuration Specification (v0.8)

## Purpose
Configuration schema extensions for v0.8 memory, sessions, and time-gap reminder. The `MemoryConfig` and `SessionConfig` Pydantic models expose configurable thresholds, paths, and enable/disable flags. The deprecated `AppConfig.memory_path` field remains readable but emits a startup warning.

## ADDED Requirements

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