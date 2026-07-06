# llm-streaming Specification

## Purpose
TBD - created by archiving change v0-1-tui-multiturn-streaming. Update Purpose after archive.
## Requirements
### Requirement: LLMClient abstract interface
The system MUST provide an abstract base class `LLMClient` defining a single async generator method `stream(messages, system)` that yields `ContentDelta` objects, so that all backends present a uniform interface to the UI layer. The system MUST support four concrete backends: `AnthropicBackend` (Anthropic API), `OpenAIBackend` (OpenAI API), `MiniMaxBackend` (MiniMax API, OpenAI-compatible), and `DeepSeekBackend` (DeepSeek API, OpenAI-compatible). `OpenAIBackend`, `MiniMaxBackend`, and `DeepSeekBackend` MUST share a common `OpenAICompatibleBackend` base class.

#### Scenario: Backend instances satisfy the interface
- **WHEN** `AnthropicBackend`, `OpenAIBackend`, `MiniMaxBackend`, and `DeepSeekBackend` are instantiated
- **THEN** all four MUST be instances of `LLMClient` and expose a `stream(messages, system) -> AsyncIterator[ContentDelta]` method

#### Scenario: Factory returns the correct backend for each of the four options
- **WHEN** the factory is called with a config whose `backend` field is `"anthropic"`
- **THEN** it returns an `AnthropicBackend` instance
- **AND** when called with `backend="openai"` it returns an `OpenAIBackend` instance
- **AND** when called with `backend="minimax"` it returns an `MiniMaxBackend` instance
- **AND** when called with `backend="deepseek"` it returns an `DeepSeekBackend` instance

#### Scenario: OpenAI-compatible backends share a common base class
- **WHEN** `OpenAIBackend`, `MiniMaxBackend`, and `DeepSeekBackend` are inspected via `__mro__`
- **THEN** all three MUST have `OpenAICompatibleBackend` as a base class
- **AND** each MUST differ from the others only by their `DEFAULT_BASE_URL` and `DEFAULT_MODEL` class attributes

### Requirement: Messages are represented uniformly
The system MUST represent conversation messages as `Message` dataclasses with a `role` field (`"user"` or `"assistant"`) and a `content` field holding plain text in v0.1.

#### Scenario: A user message round-trips
- **WHEN** a user submits the text "hello"
- **THEN** a `Message(role="user", content="hello")` is appended to the conversation history

#### Scenario: An assistant response round-trips
- **WHEN** the LLM finishes streaming a response
- **THEN** a single `Message(role="assistant", content=<full response text>)` is appended to the conversation history

### Requirement: Streaming yields text incrementally
The system MUST yield each `ContentDelta` with `type="text"` as soon as a new token is received from the underlying LLM SDK, so the UI can render character-by-character. This applies to all four backends.

#### Scenario: Anthropic backend yields deltas
- **WHEN** `AnthropicBackend.stream` is called with a valid messages list
- **THEN** it yields one or more `ContentDelta(type="text", text=...)` objects during the stream
- **AND** concatenating the `text` of all yielded deltas equals the full assistant response

#### Scenario: OpenAI backend yields deltas
- **WHEN** `OpenAIBackend.stream` is called with a valid messages list
- **THEN** it yields one or more `ContentDelta(type="text", text=...)` objects during the stream
- **AND** concatenating the `text` of all yielded deltas equals the full assistant response

#### Scenario: MiniMax backend yields deltas
- **WHEN** `MiniMaxBackend.stream` is called with a valid messages list
- **THEN** it yields one or more `ContentDelta(type="text", text=...)` objects during the stream
- **AND** concatenating the `text` of all yielded deltas equals the full assistant response

#### Scenario: DeepSeek backend yields deltas
- **WHEN** `DeepSeekBackend.stream` is called with a valid messages list
- **THEN** it yields one or more `ContentDelta(type="text", text=...)` objects during the stream
- **AND** concatenating the `text` of all yielded deltas equals the full assistant response

### Requirement: System prompt is passed correctly to each backend
The system MUST deliver the configured system prompt to each backend using the protocol that backend expects: as a separate `system` parameter for Anthropic, and as the first message with `role="system"` in the messages list for the three OpenAI-compatible backends (OpenAI, MiniMax, DeepSeek).

#### Scenario: Anthropic receives system as separate parameter
- **WHEN** `AnthropicBackend.stream` is called with `system="You are helpful"`
- **THEN** the underlying Anthropic SDK call includes `system="You are helpful"` as a top-level argument

#### Scenario: OpenAI-compatible backends receive system as first message
- **WHEN** `OpenAIBackend.stream`, `MiniMaxBackend.stream`, or `DeepSeekBackend.stream` is called with `system="You are helpful"` and a messages list
- **THEN** the messages list sent to the underlying SDK has a leading `{"role": "system", "content": "You are helpful"}` element

### Requirement: Multi-turn conversation history is preserved
The system MUST send the full conversation history (all previous user and assistant messages) to the LLM on every turn, so the model can reference prior context.

#### Scenario: Second turn includes first turn
- **WHEN** the user has submitted one message and received one assistant response
- **AND** the user submits a second message
- **THEN** the messages list passed to `LLMClient.stream` MUST contain both prior messages in order, followed by the new user message

#### Scenario: /clear empties history
- **WHEN** the user runs `/clear`
- **THEN** the next call to `LLMClient.stream` MUST receive a messages list containing only the new user message

### Requirement: Streaming exceptions propagate to the UI
The system MUST allow exceptions thrown by any of the four LLM SDKs to bubble up out of the `stream` generator, so the UI layer can catch them, render an error, and unlock the input.

#### Scenario: Anthropic SDK exception bubbles up
- **WHEN** the Anthropic SDK raises an exception (e.g., `anthropic.APIConnectionError`) during streaming
- **THEN** the exception propagates out of `AnthropicBackend.stream`

#### Scenario: OpenAI SDK exception bubbles up
- **WHEN** the OpenAI SDK raises an exception (e.g., `openai.APIError`) during streaming
- **THEN** the exception propagates out of `OpenAIBackend.stream`

#### Scenario: MiniMax SDK exception bubbles up
- **WHEN** the underlying SDK raises a connection or auth exception during streaming
- **THEN** the exception propagates out of `MiniMaxBackend.stream`

#### Scenario: DeepSeek SDK exception bubbles up
- **WHEN** the underlying SDK raises a connection or auth exception during streaming
- **THEN** the exception propagates out of `DeepSeekBackend.stream`

