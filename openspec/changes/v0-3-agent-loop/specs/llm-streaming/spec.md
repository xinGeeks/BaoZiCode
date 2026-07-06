## MODIFIED Requirements

### Requirement: Streaming exceptions propagate to the UI
The system MUST allow exceptions thrown by any of the four LLM SDKs to bubble up out of the `stream` generator. The v0.3 Agent layer catches these exceptions and translates them into an `error` event followed by `done(reason=STREAM_ERROR)`. The TUI renders the error message and unlocks the input. SDK exceptions MUST NOT crash the entire application.

#### Scenario: Anthropic SDK exception is caught by Agent
- **WHEN** the Anthropic SDK raises an exception (e.g., `anthropic.APIConnectionError`) during streaming
- **THEN** the exception propagates out of `AnthropicBackend.stream`
- **AND** the Agent catches it, yields `AgentEvent(type="error", payload=<message>)`
- **AND** the Agent yields `AgentEvent(type="done", payload={"reason": STREAM_ERROR})`
- **AND** the TUI displays the error message and re-enables the input box

#### Scenario: OpenAI SDK exception is caught by Agent
- **WHEN** the OpenAI SDK raises an exception (e.g., `openai.APIError`) during streaming
- **THEN** the exception propagates out of `OpenAIBackend.stream`
- **AND** the Agent catches it as in the Anthropic scenario above

#### Scenario: MiniMax SDK exception is caught by Agent
- **WHEN** the underlying SDK raises a connection or auth exception during streaming
- **THEN** the exception propagates out of `MiniMaxBackend.stream`
- **AND** the Agent catches it as in the Anthropic scenario above

#### Scenario: DeepSeek SDK exception is caught by Agent
- **WHEN** the underlying SDK raises a connection or auth exception during streaming
- **THEN** the exception propagates out of `DeepSeekBackend.stream`
- **AND** the Agent catches it as in the Anthropic scenario above

## ADDED Requirements

### Requirement: Backends expose token usage information
Each `LLMClient.stream` implementation MUST yield a usage payload at the end of each complete turn, in addition to the `text` and `tool_use` content deltas. The usage payload MUST be a `UsageStats` dataclass with `input_tokens: int`, `output_tokens: int`, `cache_read_tokens: int = 0`, and `cache_write_tokens: int = 0`. If the underlying SDK does not expose usage for a given call, the backend MUST yield `UsageStats(0, 0, 0, 0)` (not raise).

#### Scenario: Anthropic backend yields usage from message_delta
- **WHEN** `AnthropicBackend.stream` finishes a turn
- **THEN** it yields a final `ContentDelta(type="usage", payload=<UsageStats>)` whose values match the `message_delta.usage` field of the SDK response

#### Scenario: OpenAI backend yields usage when stream_options include_usage
- **WHEN** `OpenAIBackend.stream` is called with `stream_options={"include_usage": true}` (or backend default)
- **THEN** it yields a `ContentDelta(type="usage", payload=<UsageStats>)` with the token counts from the final streamed chunk

#### Scenario: Backends yield zero usage when SDK doesn't expose it
- **WHEN** a backend's underlying SDK does not return usage data (e.g., older API version, streaming disabled)
- **THEN** the backend yields `ContentDelta(type="usage", payload=UsageStats(0, 0, 0, 0))`
- **AND** does NOT raise an exception

#### Scenario: Agent aggregates per-turn and session-total usage
- **WHEN** the Agent receives a `usage` ContentDelta from a backend
- **THEN** it adds the per-turn values to a running session-total accumulator
- **AND** yields `AgentEvent(type="usage", payload={"this_turn": <UsageStats>, "session_total": <accumulated UsageStats>})`
