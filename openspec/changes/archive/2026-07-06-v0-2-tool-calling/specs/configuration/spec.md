## MODIFIED Requirements

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

#### Scenario: Omitted system_prompt uses default
- **WHEN** the YAML omits the `system_prompt` field
- **THEN** the loaded config has `system_prompt` equal to the default string

#### Scenario: Omitted backend base_url uses provider default
- **WHEN** a backend's `base_url` is omitted from the YAML
- **THEN** the loaded `BackendConfig.base_url` falls back to that backend's provider default
- **AND** if the YAML explicitly sets `base_url: null`, the same default is used

## ADDED Requirements

### Requirement: Permissions block configures tool-calling policy
The system MUST accept an optional `permissions:` block in the YAML config that governs the v0.2 tool-calling behavior. If the block is absent, all permissions default to: high-risk tools require confirmation, low-risk tools are auto-allowed, batch confirmation is disabled, Bash cwd follows the session with safety boundary enforcement.

#### Scenario: Default policy when permissions block is omitted
- **WHEN** the YAML has no `permissions:` block
- **THEN** the system uses defaults: every Write/Edit/Bash call requires per-call confirmation, Read/Grep/Glob/WebFetch auto-allowed, `batch_confirm=false`, `bash_locked_cwd=false`

#### Scenario: Custom deny list blocks specific tool patterns
- **WHEN** the YAML contains `permissions: deny: ["Bash(rm *)", "Bash(sudo *)"]`
- **AND** the LLM tries to invoke `Bash` with a command matching the pattern
- **THEN** the call is rejected before the confirmation modal even appears
- **AND** the conversation area shows a red ✗ card explaining which deny rule matched

#### Scenario: Custom auto_allow list permits additional tools
- **WHEN** the YAML contains `permissions: auto_allow: [Read, Grep, "Bash(npm test)"]`
- **THEN** `Read` and `Grep` remain auto-allowed
- **AND** `Bash` commands matching `npm test` are auto-allowed without confirmation
- **AND** other `Bash` commands still require confirmation

#### Scenario: batch_confirm toggle enables batch UI
- **WHEN** the YAML contains `permissions: batch_confirm: true`
- **THEN** the first of N consecutive same-name high-risk tool calls shows a modal with an "Allow all remaining" option

#### Scenario: bash_locked_cwd toggle locks Bash cwd to project root
- **WHEN** the YAML contains `permissions: bash_locked_cwd: true`
- **THEN** every `Bash` tool invocation runs with cwd fixed to the project root
- **AND** `cd <subdir>` commands have no effect on subsequent commands in the same tool invocation

#### Scenario: Unknown permission keys are ignored
- **WHEN** the YAML contains a `permissions:` key not in the known schema (e.g., `permissions: experimental: true`)
- **THEN** the config loads successfully and the unknown key is silently ignored (forward compatibility)

#### Scenario: /permissions displays the effective policy
- **WHEN** the user runs `/permissions` in the TUI
- **THEN** the conversation area shows the resolved settings (auto_allow list, deny list, batch_confirm, bash_locked_cwd) and which ones came from defaults vs explicit config