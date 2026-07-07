# interactive-tui Specification (delta for v0-4-prompt)

## Purpose
Delta to `openspec/specs/interactive-tui/spec.md` adding v0.4 `/status` command cache display.

## ADDED Requirements

### Requirement: /status command shows session token usage and cache statistics
The system MUST provide a `/status` slash command that, when invoked, displays the current session's accumulated token usage and cache statistics in the conversation area. The display MUST include, in this order:
- `input: <N>` — total input tokens consumed
- `output: <N>` — total output tokens generated
- `cache_read: <N>` — total tokens served from cache (v0.4 always 0; v0.5+ may be > 0)
- `cache_write: <N>` — total tokens written to cache (v0.4 always 0)
- `hit_rate: <PCT>%` — computed as `round(cache_read / (cache_read + input) * 100, 1)`; when `cache_read + input == 0`, the value is `0.0`

The values reflect the entire session, not just the current turn.

#### Scenario: /status before any LLM call
- **WHEN** the user runs `/status` with no prior LLM calls in the session
- **THEN** the conversation area shows `input: 0`, `output: 0`, `cache_read: 0`, `cache_write: 0`, `hit_rate: 0.0%`

#### Scenario: /status after one LLM call
- **WHEN** the user runs `/status` after at least one LLM call has completed
- **THEN** the conversation area shows the accumulated `input` and `output` totals from `UsageStats`
- **AND** the `hit_rate` percentage reflects the cache_read vs input ratio
- **AND** the input box is re-enabled and refocused

#### Scenario: /status is routable as a slash command
- **WHEN** the user types `/status` and presses Enter
- **THEN** the command is recognized as a slash command (NOT sent to the LLM)
- **AND** the status display renders in the conversation area
