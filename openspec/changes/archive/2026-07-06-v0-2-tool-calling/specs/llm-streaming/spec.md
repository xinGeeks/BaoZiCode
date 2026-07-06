## MODIFIED Requirements

### Requirement: LLMClient abstract interface
The system MUST provide an abstract base class `LLMClient` defining a single async generator method `stream(messages, system, tools)` that yields `ContentDelta` objects, so that all backends present a uniform interface to the UI layer. The system MUST support four concrete backends: `AnthropicBackend` (Anthropic API), `OpenAIBackend` (OpenAI API), `MiniMaxBackend` (MiniMax API, OpenAI-compatible), and `DeepSeekBackend` (DeepSeek API, OpenAI-compatible). `OpenAIBackend`, `MiniMaxBackend`, and `DeepSeekBackend` MUST share a common `OpenAICompatibleBackend` base class. The `tools` parameter is a `list[ToolDefinition] | None`; when `None` or empty, the backend MUST NOT send a `tools` parameter to its underlying SDK (preserving v0.1 behavior).

#### Scenario: Backend instances satisfy the interface
- **WHEN** `AnthropicBackend`, `OpenAIBackend`, `MiniMaxBackend`, and `DeepSeekBackend` are instantiated
- **THEN** all four MUST be instances of `LLMClient` and expose a `stream(messages, system, tools) -> AsyncIterator[ContentDelta]` method

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

#### Scenario: Backends accept tools=None without sending tools to SDK
- **WHEN** `AnthropicBackend.stream` or any `OpenAICompatibleBackend.stream` is called with `tools=None`
- **THEN** the underlying SDK call MUST NOT include a `tools` parameter
- **AND** no `ContentDelta` of `type="tool_use"` is yielded

#### Scenario: Backends send tool definitions to SDK when tools is provided
- **WHEN** `AnthropicBackend.stream` is called with a non-empty `tools` list
- **THEN** the underlying Anthropic SDK call MUST include `tools=[{name, description, input_schema}, ...]` derived from the `ToolDefinition`s

#### Scenario: OpenAI-compatible backends convert ToolDefinitions to function-calling format
- **WHEN** any `OpenAICompatibleBackend.stream` is called with a non-empty `tools` list
- **THEN** the underlying OpenAI SDK call MUST include `tools=[{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}, ...]`

### Requirement: Messages are represented uniformly
The system MUST represent conversation messages as `Message` dataclasses with a `role` field (`"user"`, `"assistant"`, or `"tool"`) and a `content` field of type `str | list[ContentBlock]`. Plain-text messages (the common case) MUST continue to use `content: str`. Messages containing tool invocations or tool results MUST use `content: list[ContentBlock]` where each block has a discriminator `type` of `"text"`, `"tool_use"`, or `"tool_result"`. The `Message.to_dict()` method MUST serialize `content: str` as `{"role": ..., "content": ...}` and `content: list[ContentBlock]` as the backend-appropriate shape (Anthropic SDK-style blocks for `AnthropicBackend`, OpenAI-style separate messages for `OpenAICompatibleBackend`).

#### Scenario: A plain user message round-trips as str
- **WHEN** a user submits the text "hello" and no tool has been invoked in this conversation
- **THEN** the appended `Message` has `role="user"` and `content="hello"` (a `str`, not a list)

#### Scenario: An assistant text-only response round-trips as str
- **WHEN** the LLM finishes a turn producing only text (no tool_use)
- **THEN** a single `Message(role="assistant", content=<full response text>)` is appended, with `content` as `str`

#### Scenario: An assistant turn with tool_use round-trips as ContentBlock list
- **WHEN** the LLM emits a `Read` tool call followed by no further text
- **THEN** the appended `Message` has `role="assistant"` and `content=[<tool_use block with id, name, arguments>]`

#### Scenario: A tool result message round-trips as ContentBlock list
- **WHEN** a tool finishes and its result must be fed back to the model
- **THEN** a `Message(role="tool", content=[<tool_result block with tool_use_id, content, is_error>])` is appended

### Requirement: Streaming yields text and tool_use incrementally
The system MUST yield each `ContentDelta` as soon as a new event is received from the underlying LLM SDK, with `type` reflecting the block kind: `"text"` for natural-language tokens and `"tool_use"` for tool invocations. The system MUST buffer partial tool_use events within a backend and yield exactly one `ContentDelta(type="tool_use", text=<ToolCall>)` per completed tool invocation, so the UI receives complete `ToolCall` objects. This applies to all four backends.

#### Scenario: Anthropic backend yields one tool_use delta per completed block
- **WHEN** `AnthropicBackend.stream` receives a `content_block_start{type:tool_use}` followed by zero or more `input_json_delta` events and a `content_block_stop`
- **THEN** the backend MUST yield exactly one `ContentDelta(type="tool_use", text=ToolCall(...))` with all `input_json_delta` fragments concatenated and JSON-parsed into `arguments`
- **AND** no `ContentDelta` is yielded before the `content_block_stop`

#### Scenario: OpenAI-compatible backends yield tool_use deltas
- **WHEN** any `OpenAICompatibleBackend.stream` receives a `tool_calls` array in the streamed completion delta
- **THEN** the backend MUST yield one `ContentDelta(type="tool_use", text=ToolCall(...))` for each complete tool invocation in the array

#### Scenario: Anthropic backend yields text deltas between tool_use blocks
- **WHEN** `AnthropicBackend.stream` receives text tokens, then a `tool_use` block, then more text tokens
- **THEN** text deltas are yielded in order, then exactly one `tool_use` delta, then more text deltas

### Requirement: System prompt is passed correctly to each backend
The system MUST deliver the configured system prompt to each backend using the protocol that backend expects: as a separate `system` parameter for Anthropic, and as the first message with `role="system"` in the messages list for the three OpenAI-compatible backends (OpenAI, MiniMax, DeepSeek).

#### Scenario: Anthropic receives system as separate parameter
- **WHEN** `AnthropicBackend.stream` is called with `system="You are helpful"`
- **THEN** the underlying Anthropic SDK call includes `system="You are helpful"` as a top-level argument

#### Scenario: OpenAI-compatible backends receive system as first message
- **WHEN** `OpenAIBackend.stream`, `MiniMaxBackend.stream`, or `DeepSeekBackend.stream` is called with `system="You are helpful"` and a messages list
- **THEN** the messages list sent to the underlying SDK has a leading `{"role": "system", "content": "You are helpful"}` element

### Requirement: Multi-turn conversation history is preserved
The system MUST send the full conversation history (all previous user, assistant, and tool messages) to the LLM on every turn, so the model can reference prior context and prior tool results.

#### Scenario: Second turn includes first turn
- **WHEN** the user has submitted one message and received one assistant response
- **AND** the user submits a second message
- **THEN** the messages list passed to `LLMClient.stream` MUST contain both prior messages in order, followed by the new user message

#### Scenario: /clear empties history
- **WHEN** the user runs `/clear`
- **THEN** the next call to `LLMClient.stream` MUST receive a messages list containing only the new user message

#### Scenario: History preserves tool_use and tool_result blocks
- **WHEN** a prior turn contained a tool invocation and its result
- **THEN** those messages MUST appear in subsequent `stream` calls in the order they were appended, with their `ContentBlock` lists intact

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

## ADDED Requirements

### Requirement: Backends handle malformed tool_use gracefully
The system MUST catch JSON parse errors that occur while accumulating an Anthropic `input_json_delta` stream, and yield a single `ContentDelta(type="tool_use", text=ToolCall(id=..., name=..., arguments={}, error=<exception message>))` instead of letting the exception propagate. This prevents a malformed tool invocation from crashing the entire conversation turn.

#### Scenario: Anthropic partial_json parse error yields error-marked ToolCall
- **WHEN** `AnthropicBackend.stream` accumulates an `input_json_delta` sequence that does not form valid JSON by `content_block_stop`
- **THEN** the yielded `ToolCall.arguments` is an empty dict
- **AND** the `ToolCall` carries an `error` attribute (or accessible diagnostic) describing the parse failure
- **AND** the UI surfaces this as a failed tool call (e.g., a red ✗ card) rather than a hard crash