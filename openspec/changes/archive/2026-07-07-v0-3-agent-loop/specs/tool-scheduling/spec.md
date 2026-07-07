# tool-scheduling Specification (NEW)

## Purpose
Define how multiple tool calls produced by a single LLM response are grouped and executed: read-only (`side_effect=False`) calls run in parallel within a batch; side-effecting (`side_effect=True`) calls run sequentially in LLM order. The scheduler exposes a single entry point so future DAG-based scheduling (方案 C) can replace it without changing the Agent or TUI.

## ADDED Requirements

### Requirement: ToolDefinition declares its side_effect flag
The system MUST extend `ToolDefinition` with a `side_effect: bool` field defaulting to `False`. Each tool module (`baozicode/tools/<name>.py`) MUST declare this field explicitly. The flag MUST be `False` for `Read`, `Grep`, `Glob`, `WebFetch` and `True` for `Write`, `Edit`, `Bash`.

#### Scenario: Read tool declared as side-effect-free
- **WHEN** `baozicode.tools.read.TOOL` is inspected
- **THEN** `TOOL.side_effect` is `False`

#### Scenario: Write tool declared as side-effecting
- **WHEN** `baozicode.tools.write.TOOL` is inspected
- **THEN** `TOOL.side_effect` is `True`

#### Scenario: Bash tool declared as side-effecting
- **WHEN** `baozicode.tools.bash.TOOL` is inspected
- **THEN** `TOOL.side_effect` is `True`

### Requirement: Scheduler splits calls into parallel and sequential batches
The scheduler MUST accept a list of `ToolCall` instances (in LLM return order) and partition them into batches:
- A batch of consecutive `side_effect=False` calls is marked `parallel`.
- A batch of one or more `side_effect=True` calls is marked `sequential`.
- The order of batches follows the original LLM order.

#### Scenario: All-read batch is parallel
- **WHEN** the scheduler receives `[Read A, Read B, Read C]`
- **THEN** it produces one batch `{parallel: True, calls: [Read A, Read B, Read C]}`

#### Scenario: All-write batch is sequential
- **WHEN** the scheduler receives `[Write A, Write B]`
- **THEN** it produces one batch `{parallel: False, calls: [Write A, Write B]}`

#### Scenario: Mixed batch splits at boundary
- **WHEN** the scheduler receives `[Read A, Read B, Write C, Bash D, Read E]`
- **THEN** it produces three batches in order:
  - `{parallel: True, calls: [Read A, Read B]}`
  - `{parallel: False, calls: [Write C]}`
  - `{parallel: False, calls: [Bash D]}`
  - `{parallel: True, calls: [Read E]}`

### Requirement: Parallel batches execute concurrently via asyncio.gather
A parallel batch MUST execute all its tool calls concurrently using `asyncio.gather`. The results MUST be ordered to match the input call order (NOT completion order) before being yielded to the Agent.

#### Scenario: Parallel results in LLM order
- **WHEN** the scheduler runs a parallel batch `[Read A, Read B, Read C]`
- **AND** Read B finishes before Read A
- **THEN** the scheduler yields results in order `[A_result, B_result, C_result]`

#### Scenario: Parallel execution is actually concurrent
- **WHEN** a parallel batch contains 4 Read calls each taking 100ms
- **THEN** the total wall-clock time is approximately 100ms (NOT 400ms)

#### Scenario: Exception in one parallel call does not crash others
- **WHEN** one Read in a parallel batch raises an exception
- **THEN** other Reads complete and yield their results
- **AND** the failed Read yields a synthetic `ToolResult(is_error=True, content=<exception message>)`

### Requirement: Sequential batches execute in LLM order
A sequential batch MUST execute its tool calls one at a time, awaiting each before starting the next. Each result is yielded to the Agent as soon as it completes (so the TUI can render the result card immediately).

#### Scenario: Sequential order preserved
- **WHEN** the scheduler runs a sequential batch `[Write A, Write B]`
- **THEN** Write A completes first, its result is yielded, then Write B starts

#### Scenario: Sequential batch results yield one at a time
- **WHEN** the scheduler runs `[Write A, Write B]`
- **THEN** the Agent receives two `tool_result` events, the second emitted only after the first tool's `execute()` returns

### Requirement: Scheduler results feed conversation in LLM order
The scheduler MUST ensure that the order in which `ToolResult`s are appended to the conversation history matches the order of the corresponding `ToolCall`s in the LLM response (not the completion order).

#### Scenario: Tool result order matches tool call order
- **WHEN** a parallel batch `[Read A, Read B]` produces results `[A_result, B_result]` (already in LLM order from the scheduler)
- **THEN** `conversation.add_tool_result(A_result)` is called before `conversation.add_tool_result(B_result)`

### Requirement: Scheduler exposes a single function for future DAG replacement
The scheduler's batch-partitioning logic MUST be encapsulated in a single function (`_split_batches(calls: list[ToolCall]) -> list[Batch]`). The Agent MUST call only this function for partitioning decisions. A future DAG-based scheduler (方案 C) MUST be implementable by replacing this function alone, without changing the Agent's event yield loop or the TUI's event consumption.

#### Scenario: Single function dependency
- **WHEN** the Agent needs to schedule a list of tool calls
- **THEN** it calls exactly one function from `baozicode.agent.scheduler`
- **AND** the function returns the batch plan
- **AND** the Agent iterates the plan to execute batches

#### Scenario: Function signature is stable
- **WHEN** `_split_batches` is called with any list of `ToolCall` instances
- **THEN** it returns a list of `Batch` dataclasses, each with `parallel: bool` and `calls: list[ToolCall]`
- **AND** concatenating all batches' `calls` lists equals the input list (preserving order, no loss, no duplication)
