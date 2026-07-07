# agent-loop Specification

## Purpose
TBD - created by archiving change v0-4-prompt. Update Purpose after archive.

> 注：v0-3-agent-loop 已归档但 spec sync 阶段被打断（`openspec/specs/agent-loop/` 目录为空）。v0.4 一次性补建完整 spec，覆盖 v0.3 Agent 主体行为 + v0.4 PromptBuilder 集成。

## ADDED Requirements

### Requirement: Agent.run is an async generator yielding AgentEvent
The system MUST provide an `Agent` class whose `run(user_message: str)` method is an `async generator` yielding `AgentEvent` instances. The TUI (and any other consumer) MUST consume events by iterating the generator — Agent MUST NOT push events via callbacks, queues, or shared mutable state.

#### Scenario: TUI iterates async generator
- **WHEN** the TUI calls `async for event in agent.run(user_message)`
- **THEN** events arrive in the order they are produced by the Agent's internal loop (text → tool_call → tool_result → usage → ... → done)

#### Scenario: Agent.run exits cleanly after done
- **WHEN** the Agent finishes (any termination reason)
- **THEN** `Agent.run` exits cleanly after yielding exactly one `done` event
- **AND** no further events are yielded

#### Scenario: Sequential runs have isolated state
- **WHEN** the TUI calls `agent.run(message_a)` and then `agent.run(message_b)` sequentially
- **THEN** both runs produce independent event streams with isolated state (no deny counts or failure windows leak between runs)

### Requirement: AgentEvent has 7 type variants
The system MUST represent every event the Agent emits as an `AgentEvent` dataclass (or frozen dataclass) with a `type` field of one of: `"text"`, `"tool_call"`, `"tool_result"`, `"usage"`, `"progress"`, `"done"`, `"error"`. The payload for each event type MUST be type-safe and unambiguous.

#### Scenario: text event has string payload
- **WHEN** the LLM streams a text delta
- **THEN** the Agent yields `AgentEvent(type="text", payload=<string chunk>)`

#### Scenario: tool_call event after permission check
- **WHEN** the LLM finishes emitting a complete tool invocation
- **THEN** the Agent yields `AgentEvent(type="tool_call", payload=<ToolCall>)` after permission check has resolved (allowed or denied)

#### Scenario: tool_result event before conversation append
- **WHEN** a tool finishes executing (or is denied)
- **THEN** the Agent yields `AgentEvent(type="tool_result", payload=<ToolResult>)` BEFORE feeding it back into the conversation history

#### Scenario: usage event has this_turn and session_total
- **WHEN** the LLM stream for a single turn completes
- **THEN** the Agent yields `AgentEvent(type="usage", payload={"this_turn": UsageStats, "session_total": UsageStats})`

#### Scenario: progress event reflects current phase
- **WHEN** the Agent transitions between phases within a single iteration
- **THEN** the Agent yields `AgentEvent(type="progress", payload={"iteration": int, "max": int, "phase": "streaming"|"tool_exec"|"checking"})` with `phase` reflecting the current Agent state

#### Scenario: exactly one done event
- **WHEN** the Agent finishes (any reason)
- **THEN** the Agent yields exactly one `AgentEvent(type="done", payload={"reason": StopReason})` before the generator returns

#### Scenario: error event on unrecoverable exception
- **WHEN** an unrecoverable exception occurs (not a stop condition, not a stream error caught as a stop)
- **THEN** the Agent yields `AgentEvent(type="error", payload=<error message string>)` followed by a `done` event with `reason=STREAM_ERROR`

### Requirement: Agent terminates with one of 7 StopReason values
The Agent MUST terminate and emit `done` with one of these `StopReason` values: `COMPLETED`, `MAX_ITERATIONS_REACHED`, `USER_CANCELLED`, `UNKNOWN_TOOL_HALLUCINATION`, `DENIALS_EXCEEDED`, `FAILED_TOOL_LOOP`, `STREAM_ERROR`.

#### Scenario: Text-only turn completes
- **WHEN** the LLM stream completes with text deltas only (no `tool_use`)
- **THEN** the Agent yields `done` with `reason=COMPLETED`

#### Scenario: Max iterations reached
- **WHEN** the iteration counter reaches `agent.max_iterations` BEFORE any other stop condition
- **THEN** the Agent yields `done` with `reason=MAX_ITERATIONS_REACHED`
- **AND** the conversation history is preserved (not rolled back)

#### Scenario: User cancellation at safe checkpoint
- **WHEN** `agent.cancel()` is called while `agent.run` is awaiting
- **THEN** the Agent yields `done` with `reason=USER_CANCELLED` at the next safe checkpoint
- **AND** any in-progress tool execution is NOT interrupted mid-flight

#### Scenario: Unknown tool first occurrence
- **WHEN** the LLM calls a tool name not in the registry for the first time in this run
- **THEN** the Agent returns an error `ToolResult` to the LLM (no `done` event)
- **AND** the run continues to the next iteration (LLM gets a chance to correct)

#### Scenario: Unknown tool hallucination
- **WHEN** the LLM calls a tool name that is not in the registry
- **AND** the LLM previously called that same unknown name in the immediately preceding iteration
- **THEN** the Agent yields `done` with `reason=UNKNOWN_TOOL_HALLUCINATION`

#### Scenario: Denials threshold reached
- **WHEN** the same tool name has been denied 3 times cumulatively in this run
- **THEN** the Agent yields `done` with `reason=DENIALS_EXCEEDED`

#### Scenario: Failed tool loop detected
- **WHEN** the same `(tool_name, sha256(error_msg)[:16])` appears as a tool_result 3 times consecutively
- **THEN** the Agent yields `done` with `reason=FAILED_TOOL_LOOP`
- **AND** a tool call with a different error message (different hash) does NOT count toward this threshold

#### Scenario: Stream error caught
- **WHEN** the LLM SDK raises an exception during streaming
- **THEN** the Agent catches it, yields `error` event with the message, then yields `done` with `reason=STREAM_ERROR`

### Requirement: Agent reads max_iterations from AppConfig
The Agent MUST read `agent.max_iterations` from `AppConfig`. If absent, the default MUST be 20. When the iteration counter reaches this value, the Agent yields `done` with `reason=MAX_ITERATIONS_REACHED`.

#### Scenario: Default 20 iterations
- **WHEN** the config file omits `agent.max_iterations`
- **THEN** the Agent allows up to 20 iterations before yielding `MAX_ITERATIONS_REACHED`

#### Scenario: Custom max_iterations respected
- **WHEN** the config sets `agent.max_iterations: 5`
- **THEN** the Agent allows up to 5 iterations

### Requirement: Agent.cancel() sets an asyncio.Event
The Agent MUST expose a `cancel()` method that sets an internal `asyncio.Event`. The main loop MUST check this event at safe checkpoints: after each LLM stream completes, after each tool finishes executing, and after each `tool_result` is fed back to the conversation. `cancel()` MUST NOT interrupt a tool execution already in progress.

#### Scenario: Cancel during LLM stream
- **WHEN** cancel() is called while the Agent is awaiting an LLM stream
- **THEN** the Agent yields `done` with `reason=USER_CANCELLED` at the end of the current stream

#### Scenario: Cancel during tool execution
- **WHEN** cancel() is called while a tool is executing
- **THEN** the tool execution completes (cancel does NOT interrupt mid-flight)
- **AND** the Agent yields `done` with `reason=USER_CANCELLED` at the next safe checkpoint

### Requirement: Agent integrates PromptBuilder (v0.4)
The Agent's constructor MUST accept `config: AppConfig` (replacing the v0.3 `system_prompt: str` parameter) and use it to build a `BuiltPrompt` via `PromptBuilder.build(config, plan_mode=self._plan_mode, tools=tools)`. The resulting `BuiltPrompt` is stored as `self._prompt` and reused for every `llm.stream` call within that Agent instance.

#### Scenario: Agent.__init__ calls PromptBuilder.build once
- **WHEN** Agent is constructed with a valid AppConfig
- **THEN** `PromptBuilder.build()` is called exactly once during `__init__`
- **AND** the returned BuiltPrompt is stored as `self._prompt`

#### Scenario: Agent uses BuiltPrompt.stable_system as system argument
- **WHEN** Agent.run calls `llm.stream(messages, system=..., tools=...)`
- **THEN** the `system` argument is `self._prompt.stable_system`

#### Scenario: Agent uses BuiltPrompt.augmented_tools as tools argument
- **WHEN** Agent.run calls `llm.stream(...)`
- **THEN** the `tools` argument is `self._prompt.augmented_tools` (rules already injected into descriptions)

#### Scenario: Plan mode filters tools to side_effect=False
- **WHEN** Agent is constructed with `plan_mode=True`
- **THEN** `self._prompt.augmented_tools` contains only the 4 read-only tools (Read / Grep / Glob / WebFetch)
- **AND** Write / Edit / Bash are NOT present

### Requirement: Agent._inject_reminders splices <system-reminder> messages
The Agent MUST implement `_inject_reminders(messages, iteration)` that, on every LLM call, splices any pending `<system-reminder>` user-role messages (env / plan_mode) before `messages[-1]`. See `system-reminders` spec for details.

#### Scenario: Reminder appears in second-to-last position
- **WHEN** Agent.run invokes llm.stream
- **THEN** the messages argument contains a user-role message with `<system-reminder` substring
- **AND** that message is at index `len(messages) - 2` (immediately before the user message)
