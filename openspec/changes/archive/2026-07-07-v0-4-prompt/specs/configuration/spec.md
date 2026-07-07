# configuration Specification (delta for v0-4-prompt)

## Purpose
Delta to `openspec/specs/configuration/spec.md` adding v0.4 prompt-related fields.

## MODIFIED Requirements

### Requirement: Default values are sensible
The system MUST provide these defaults when fields are omitted from the YAML:
- `system_prompt`: "You are BaoZiCode, a helpful AI coding assistant."
- `custom_instructions`: "" (empty string, no user-provided instructions)
- `skills_dir`: `~/.config/baozicode/skills` (directory for skill files, scanned but may not exist)
- `memory_path`: `~/.config/baozicode/memory.md` (file for long-term memory, missing file is silently skipped)
- `base_url` for Anthropic: `https://api.anthropic.com`
- `base_url` for OpenAI: `https://api.openai.com/v1`
- `base_url` for MiniMax: `https://api.minimaxi.com/v1` (placeholder, override recommended)
- `base_url` for DeepSeek: `https://api.deepseek.com/v1`

#### Scenario: Omitted system_prompt uses default
- **WHEN** the YAML omits the `system_prompt` field
- **THEN** the loaded config has `system_prompt` equal to the default string

#### Scenario: Omitted v0.4 prompt fields use defaults
- **WHEN** the YAML omits `custom_instructions`, `skills_dir`, and `memory_path`
- **THEN** the loaded config has `custom_instructions == ""`, `skills_dir == Path("~/.config/baozicode/skills")`, and `memory_path == Path("~/.config/baozicode/memory.md")`

#### Scenario: Omitted backend base_url uses provider default
- **WHEN** a backend's `base_url` is omitted from the YAML
- **THEN** the loaded `BackendConfig.base_url` falls back to that backend's provider default
- **AND** if the YAML explicitly sets `base_url: null`, the same default is used

## ADDED Requirements

### Requirement: AgentConfig supports prompt reminder cadence and rules
The system MUST extend `AgentConfig` with three new fields beyond the v0.3 `max_iterations: int = 20`:
- `enable_system_reminders: bool = True` — when False, Agent._inject_reminders returns messages unchanged
- `plan_reminder_interval: int = 5` — interval (in iterations) for re-emitting plan_mode reminders
- `rules: RulesConfig` — controls which key rules are active (see `RulesConfig` requirement below)

#### Scenario: Defaults work without YAML
- **WHEN** the YAML omits the `agent:` block entirely
- **THEN** the active `AgentConfig` has `enable_system_reminders=True`, `plan_reminder_interval=5`, and `rules=RulesConfig()` (all rules enabled)

#### Scenario: Disabling reminders skips injection
- **WHEN** the YAML sets `agent: enable_system_reminders: false`
- **THEN** the Agent constructed with this config never injects `<system-reminder` messages into the messages list

#### Scenario: Custom reminder interval changes cadence
- **WHEN** the YAML sets `agent: plan_reminder_interval: 3`
- **THEN** the plan_mode reminder is emitted at iterations 1, 4, 7, 10, ... (instead of 1, 6, 11, ...)

### Requirement: RulesConfig controls individual key rules
The system MUST provide a `RulesConfig` Pydantic model with 7 boolean fields, all defaulting to `True`:
- `edit_requires_read`, `prefer_specialized_tools`, `bash_timeout`, `parallel_limit`, `error_then_decide`, `absolute_paths`, `webfetch_to_file`

When a rule's field is `False`, that rule MUST NOT appear in the system prompt's `## 工具使用关键规则` section AND MUST NOT inject any prefix into any tool's `description`.

#### Scenario: All rules enabled by default
- **WHEN** the YAML omits `agent.rules`
- **THEN** `rules.edit_requires_read == True` (and 6 others)
- **AND** the rendered system prompt contains all 7 numbered rules

#### Scenario: Disabling one rule removes it from prompt
- **WHEN** the YAML sets `agent: rules: { edit_requires_read: false }`
- **THEN** the rule numbered "1." (edit_requires_read) does NOT appear in the system prompt
- **AND** the Edit tool's description does NOT contain the `【必读】` prefix

#### Scenario: Unknown rule keys are ignored
- **WHEN** the YAML contains `agent: rules: { some_future_rule: true }`
- **THEN** the config loads successfully (Pydantic `extra="ignore"`)
- **AND** the unknown key is silently dropped
