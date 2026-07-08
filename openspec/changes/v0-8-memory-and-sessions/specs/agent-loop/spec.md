# agent-loop Specification (v0.8 deltas)

## Purpose
Modifications to the v0.7 Agent loop to integrate session archiving (via `ConversationManager.archiver` callback) and async memory updates (triggered on `COMPLETED` or `MAX_ITERATIONS_REACHED`). All existing v0.7 semantics (tool calls, permissions, compaction) remain unchanged.

## ADDED Requirements

### Requirement: JSONL append via ConversationManager.archiver
The system MUST accept a `SessionArchiver` instance via `Agent.set_archiver(archiver)` setter (allowing late binding after Agent construction). When the archiver is set, every `ConversationManager.add_*` call writes one JSON line to the JSONL file via `archiver.append(msg)`. When the archiver is `None`, no file writes occur (back-compat with v0.7 behavior).

The system MUST NOT change the existing Agent.run() flow to explicitly call append — the append happens transparently inside `ConversationManager.add_*` based on the injected archiver.

#### Scenario: Archiver set, every add_* writes one line
- **WHEN** `agent.set_archiver(SessionArchiver(root, sid))` is called before `agent.run()`
- **AND** the run loop calls `conversation.add_user("hello")` then `conversation.add_assistant("hi")`
- **THEN** the JSONL file contains exactly 2 lines
- **AND** each line is independently parseable

#### Scenario: Archiver None, no writes
- **WHEN** `set_archiver(None)` is called explicitly OR never called (v0.7 default)
- **THEN** the `ConversationManager` instance operates identically to v0.7 (no append side-effect on `add_*`)
- **AND** no JSONL files are created by any of `add_user` / `add_assistant` / `add_message` / `add_tool_call` / `add_tool_result` / `add_turn` methods
- **AND** the Agent loop completes normally with in-memory-only conversation (matches v0.7 behavior)

#### Scenario: Archiver append failure does not break conversation
- **WHEN** `archiver.append()` raises an exception (disk full, permission denied)
- **THEN** the exception is caught and logged as a warning
- **AND** the conversation continues normally
- **AND** the user does NOT see an error in the TUI

### Requirement: Memory update trigger on natural stop
The system MUST accept a `MemoryUpdater` instance via `Agent.set_memory_updater(updater)` setter. When set, the Agent loop MUST trigger an async memory update at the `COMPLETED` or `MAX_ITERATIONS_REACHED` exit path.

The system MUST capture a frozen copy of `conversation.to_list()` at the trigger moment and pass it to `updater.update(snapshot)`. The system MUST NOT block on the update result — `asyncio.create_task` is used.

#### Scenario: COMPLETED triggers update
- **WHEN** the loop yields `done(reason=COMPLETED)`
- **THEN** `asyncio.create_task(self._memory_updater.update(snapshot))` is fired
- **AND** `snapshot` is `list(self._conversation.to_list())` (shallow copy)

#### Scenario: MAX_ITERATIONS_REACHED triggers update
- **WHEN** the loop exhausts `max_iterations` without natural completion
- **THEN** `done(reason=MAX_ITERATIONS_REACHED)` is yielded
- **AND** the memory update is fired (the conversation likely contains valuable content)

#### Scenario: USER_CANCELLED does NOT trigger update
- **WHEN** the user presses Esc / Ctrl+C
- **THEN** `done(reason=USER_CANCELLED)` is yielded
- **AND** no memory update is fired

#### Scenario: STREAM_ERROR does NOT trigger update
- **WHEN** the LLM stream raises an exception
- **THEN** `done(reason=STREAM_ERROR)` is yielded
- **AND** no memory update is fired

#### Scenario: COMPACTION_FAILED does NOT trigger update
- **WHEN** v0.7 compaction raises `CompactError` after 3 failures
- **THEN** `done(reason=COMPACTION_FAILED)` is yielded
- **AND** no memory update is fired (the conversation may be in an inconsistent state)

#### Scenario: Memory updater None, trigger is no-op
- **WHEN** `set_memory_updater` is never called (or called with None)
- **THEN** the COMPLETED / MAX_ITERATIONS_REACHED exit proceeds normally
- **AND** no task is created

### Requirement: Reminder injection supports time_gap and memory_refreshed
The system MUST extend `_inject_reminders` to support two new reminder types in addition to the v0.7 types (`env`, `plan_mode`, `denial_rate_limit`):
- `time_gap` (ttl="once") — body: "距上次会话已过 X 小时..." (constructed at resume time)
- `memory_refreshed` (ttl="sticky") — body: brief summary of last memory update ("已记录 N 条笔记 ...")

Both MUST be spliced into `messages[-2]` like existing reminders, preserving the user-message-last invariant.

#### Scenario: time_gap reminder appears in messages
- **WHEN** resume inserts a time_gap reminder into the conversation
- **AND** Agent.run() calls _inject_reminders
- **THEN** the time_gap reminder is present in the messages passed to `llm.stream()`
- **AND** it appears at `messages[-2]` (before the most recent user message)

#### Scenario: memory_refreshed reminder appended after update
- **WHEN** the memory updater completes a successful update
- **THEN** a memory_refreshed reminder is constructed
- **AND** injected into subsequent `llm.stream()` calls
- **AND** remains in the message list until the conversation is cleared

### Requirement: set_archiver and set_memory_updater are no-ops on running Agent
The system MUST allow `set_archiver` and `set_memory_updater` to be called BEFORE `agent.run()` starts, OR at safe points during the run. Calling them mid-iteration MUST NOT cause inconsistency — the Agent reads `self._archiver` and `self._memory_updater` at the top of each iteration, so a late setter call takes effect on the NEXT iteration.

#### Scenario: set_archiver before run
- **WHEN** `agent.set_archiver(archiver)` is called immediately after `Agent.__init__`
- **THEN** the archiver is active for all subsequent add_* calls in `agent.run()`

#### Scenario: set_memory_updater before run
- **WHEN** `agent.set_memory_updater(updater)` is called immediately after `Agent.__init__`
- **THEN** the updater is active for the COMPLETED / MAX_ITERATIONS_REACHED exit trigger