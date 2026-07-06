## MODIFIED Requirements

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
