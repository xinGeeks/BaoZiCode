# agent-loop Specification (NEW)

## Purpose
Define the Agent class, its `AgentEvent` streaming contract, the five (plus one stream error) termination conditions, the iteration upper bound, and the user-cancellation signal. Agent is the new core abstraction in v0.3 that decouples reasoning logic from the TUI layer.

## ADDED Requirements

### Requirement: Agent is an async generator that yields AgentEvents
The system MUST provide an `Agent` class whose `run(user_message: str)` method is an `async generator` yielding `AgentEvent` instances. The TUI (and any other consumer) MUST consume events by iterating the generator — Agent MUST NOT push events via callbacks, queues, or shared mutable state.

#### Scenario: Agent.run yields events in stream order
- **WHEN** the TUI calls `async for event in agent.run(user_message)`
- **THEN** events arrive in the order they are produced by the Agent's internal loop (text → tool_call → tool_result → usage → ... → done)

#### Scenario: Agent.run returns when done
- **WHEN** the Agent finishes (any termination reason)
- **THEN** `Agent.run` exits cleanly after yielding exactly one `done` event
- **AND** no further events are yielded

#### Scenario: Same Agent instance supports multiple sequential runs
- **WHEN** the TUI calls `agent.run(message_a)` and then `agent.run(message_b)` sequentially
- **THEN** both runs produce independent event streams with isolated state (no deny counts or failure windows leak between runs)

### Requirement: AgentEvent has a discriminated type field
The system MUST represent every event the Agent emits as an `AgentEvent` dataclass (or frozen dataclass) with a `type` field of one of: `"text"`, `"tool_call"`, `"tool_result"`, `"usage"`, `"progress"`, `"done"`, `"error"`. The payload for each event type MUST be type-safe and unambiguous.

#### Scenario: text event carries a string chunk
- **WHEN** the LLM streams a text delta
- **THEN** the Agent yields `AgentEvent(type="text", payload=<string chunk>)`

#### Scenario: tool_call event carries a ToolCall
- **WHEN** the LLM finishes emitting a complete tool invocation
- **THEN** the Agent yields `AgentEvent(type="tool_call", payload=<ToolCall>)` after permission check has resolved (allowed or denied)

#### Scenario: tool_result event carries a ToolResult
- **WHEN** a tool finishes executing (or is denied)
- **THEN** the Agent yields `AgentEvent(type="tool_result", payload=<ToolResult>)` BEFORE feeding it back into the conversation history

#### Scenario: usage event carries per-turn and session totals
- **WHEN** the LLM stream for a single turn completes
- **THEN** the Agent yields `AgentEvent(type="usage", payload={"this_turn": UsageStats, "session_total": UsageStats})`

#### Scenario: progress event carries minimal set
- **WHEN** the Agent transitions between phases within a single iteration
- **THEN** the Agent yields `AgentEvent(type="progress", payload={"iteration": int, "max": int, "phase": "streaming"|"tool_exec"|"checking"})` with `phase` reflecting the current Agent state

#### Scenario: done event carries termination reason
- **WHEN** the Agent finishes (any reason)
- **THEN** the Agent yields exactly one `AgentEvent(type="done", payload={"reason": StopReason})` before the generator returns

#### Scenario: error event carries a human-readable message
- **WHEN** an unrecoverable exception occurs (not a stop condition, not a stream error caught as a stop)
- **THEN** the Agent yields `AgentEvent(type="error", payload=<error message string>)` followed by a `done` event with `reason=STREAM_ERROR`

### Requirement: Agent supports six termination reasons
The Agent MUST terminate and emit `done` with one of these `StopReason` values:
- `COMPLETED`: LLM produced no tool_use in this iteration; task is done.
- `MAX_ITERATIONS_REACHED`: current iteration >= `agent.max_iterations` (default 20).
- `USER_CANCELLED`: `_cancel_event` was set (Esc or Ctrl+C during a run).
- `UNKNOWN_TOOL_HALLUCINATION`: same unknown tool name appeared twice consecutively.
- `DENIALS_EXCEEDED`: same tool name was denied (by user or `permissions.deny`) 3 times cumulatively in this run.
- `FAILED_TOOL_LOOP`: same `(tool_name, sha256(error_msg)[:16])` appeared 3 times consecutively in this run.
- `STREAM_ERROR`: LLM SDK raised an exception that the Agent caught as a terminal condition.

#### Scenario: COMPLETED after text-only turn
- **WHEN** the LLM stream completes with text deltas only (no `tool_use`)
- **THEN** the Agent yields `done` with `reason=COMPLETED`

#### Scenario: MAX_ITERATIONS_REACHED is the safety net
- **WHEN** the iteration counter reaches `agent.max_iterations` BEFORE any other stop condition
- **THEN** the Agent yields `done` with `reason=MAX_ITERATIONS_REACHED`
- **AND** the conversation history is preserved (not rolled back)

#### Scenario: USER_CANCELLED via cancel event
- **WHEN** `agent.cancel()` is called while `agent.run` is awaiting
- **THEN** the Agent yields `done` with `reason=USER_CANCELLED` at the next safe checkpoint
- **AND** any in-progress tool execution is NOT interrupted mid-flight (it completes, but its result is NOT fed back to the LLM)

#### Scenario: UNKNOWN_TOOL_HALLUCINATION after retry
- **WHEN** the LLM calls a tool name that is not in the registry
- **AND** the LLM previously called that same unknown name in the immediately preceding iteration
- **THEN** the Agent yields `done` with `reason=UNKNOWN_TOOL_HALLUCINATION`

#### Scenario: First unknown tool name is allowed
- **WHEN** the LLM calls a tool name not in the registry for the first time in this run
- **THEN** the Agent returns an error `ToolResult` to the LLM (no `done` event)
- **AND** the Agent yields a `progress` event with `phase=checking`
- **AND** the run continues to the next iteration (LLM gets a chance to correct)

#### Scenario: DENIALS_EXCEEDED after threshold
- **WHEN** the same tool name has been denied 3 times cumulatively in this run (by user pressing N OR by `permissions.deny` matching)
- **THEN** the Agent yields `done` with `reason=DENIALS_EXCEEDED`

#### Scenario: FAILED_TOOL_LOOP uses precise hash
- **WHEN** the same `(tool_name, sha256(error_msg)[:16])` appears as a tool_result 3 times consecutively
- **THEN** the Agent yields `done` with `reason=FAILED_TOOL_LOOP`
- **AND** a tool call with a different error message (different hash) does NOT count toward this threshold

#### Scenario: STREAM_ERROR catches SDK exceptions
- **WHEN** the LLM SDK raises an exception during streaming
- **THEN** the Agent catches it, yields `error` event with the message, then yields `done` with `reason=STREAM_ERROR`

### Requirement: Iteration upper bound is configurable, default 20
The Agent MUST read `agent.max_iterations` from `AppConfig`. If absent, the default MUST be 20. When the iteration counter reaches this value, the Agent yields `done` with `reason=MAX_ITERATIONS_REACHED`.

#### Scenario: Default 20 iterations
- **WHEN** the config file omits `agent.max_iterations`
- **THEN** the Agent allows up to 20 iterations before yielding `MAX_ITERATIONS_REACHED`

#### Scenario: Custom max iterations respected
- **WHEN** the config sets `agent.max_iterations: 5`
- **THEN** the Agent allows up to 5 iterations

### Requirement: User cancellation via asyncio.Event
The Agent MUST expose a `cancel()` method that sets an internal `asyncio.Event`. The main loop MUST check this event at safe checkpoints: after each LLM stream completes, after each tool finishes executing, and after each `tool_result` is fed back to the conversation. `cancel()` MUST NOT interrupt a tool execution already in progress.

#### Scenario: Cancel during LLM stream wait
- **WHEN** the Agent is awaiting `async for delta in llm.stream(...)`
- **AND** `cancel()` is called
- **THEN** the Agent yields `done(USER_CANCELLED)` at the next iteration boundary (after the current stream iteration completes)

#### Scenario: Cancel after tool execution
- **WHEN** `cancel()` is called after a tool finishes but before the next iteration's stream starts
- **THEN** the Agent yields `done(USER_CANCELLED)` before initiating the next LLM call

#### Scenario: Cancel is per-run, not persistent
- **WHEN** `cancel()` is called after a run has completed
- **THEN** the next call to `agent.run(...)` is unaffected (the cancel event was for the previous run)

#### Scenario: Cancel does not interrupt in-progress tool
- **WHEN** `cancel()` is called while `await execute_tool(call)` is running
- **THEN** the tool completes normally
- **AND** its result is yielded as `tool_result` event
- **AND** the Agent immediately yields `done(USER_CANCELLED)` afterwards (no further LLM call)

### Requirement: Agent maintains per-run state isolation
The Agent MUST initialize fresh state at the start of each `run()` call: empty deny counters, empty failure window, cleared cancel event. State MUST NOT leak across consecutive runs on the same Agent instance.

#### Scenario: State reset between runs
- **WHEN** `run(message_a)` completes with 2 denials counted
- **AND** `run(message_b)` is called next
- **THEN** `message_b` starts with deny counter = 0

#### Scenario: Cancel event cleared at run start
- **WHEN** `cancel()` was called for a previous run that already finished
- **AND** a new `run()` is called
- **THEN** the new run starts with cancel event unset
