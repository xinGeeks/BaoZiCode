## MODIFIED Requirements

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
