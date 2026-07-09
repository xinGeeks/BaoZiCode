# subagent-manager Specification (v1.2)

## Purpose
`SubAgentManager` is the central orchestrator for sub-Agent lifecycle. It
holds the registry of in-flight and completed tasks, dispatches new tasks
(via `SubAgentRuntime.spawn` + `agent.run`), tracks state, and routes
completion notifications back to the main Agent. It also owns the `task`
tool definition that is exposed to the main Agent's LLM.

The manager is a singleton on `BaoZiCodeApp` and is shared between the
`Agent` (for `task` tool execution and lifecycle event payloads) and the
`ChatScreen` (for TUI status bar / collapse cards / completion toasts).

## Requirements

### Requirement: TaskInfo data model
`TaskInfo` MUST be a frozen-ish dataclass (mutable in practice for state
transitions) with the following fields:

- `task_id: str` — unique, format `task-YYYYMMDD-HHMMSS-xxxx` (similar to
  session_id)
- `type: Literal["definition", "fork"]`
- `role: str | None` — definition mode role name, fork mode None
- `prompt: str` — the original task prompt
- `state: Literal["pending", "running", "done", "failed", "canceled", "timeout"]`
- `created_at: datetime`
- `started_at: datetime | None` — set when state transitions to `running`
- `finished_at: datetime | None` — set on terminal state
- `agent: Agent | None` — set when spawned (running state)
- `result: str | None` — final summary text (done state)
- `error: str | None` — error message (failed / canceled / timeout)
- `usage: UsageStats` — sub-Agent's own token counter
- `cancel_event: asyncio.Event` — set to cancel the sub-Agent's run loop
- `notification_pending: bool` — True if completion should be delivered to
  the main Agent on its next iteration top

#### Scenario: Initial state
- **WHEN** `TaskInfo(task_id=..., type="definition", role="explorer",
  prompt="...")` is constructed
- **THEN** `state == "pending"`, `started_at == None`, `finished_at == None`,
  `agent == None`, `result == None`, `error == None`, `notification_pending == False`

#### Scenario: State transitions
- **WHEN** `dispatch` is called
- **THEN** `state` transitions: `pending → running → {done | failed |
  canceled | timeout}` (linear, no backwards transitions)
- **AND** each transition updates `started_at` / `finished_at` accordingly

### Requirement: SubAgentManager state machine
The manager MUST maintain a `dict[str, TaskInfo]` of all tasks (terminal
state retained for at least `task_retention_minutes`, default 5). The
manager MUST NOT allow more than `max_concurrent` (default 5) tasks in
`running` state simultaneously. New `dispatch` calls exceeding the limit
MUST return a clear error to the caller (sync mode) or raise
`MaxConcurrentReachedError` (async mode).

#### Scenario: Concurrent limit
- **WHEN** 5 sub-Agents are in `running` state
- **AND** `dispatch(...)` is called for a 6th
- **THEN** the dispatch call returns / raises an error
- **AND** no new TaskInfo is added to `_tasks` (or it is added with state
  `pending` and immediately rejected)

#### Scenario: Task cleanup after retention period
- **WHEN** a task has been in `done` state for > `task_retention_minutes`
- **THEN** on the next `dispatch` or `list_tasks` call, the task is
  removed from `_tasks`
- **AND** the cleanup is lazy (no background sweeper)

### Requirement: dispatch — sync and async paths
`SubAgentManager.dispatch(...)` MUST support two return modes based on
`async_` parameter:

- `async_=True` (default): dispatch returns the `task_id` string
  immediately. The sub-Agent runs in the background as an `asyncio.Task`.
- `async_=False`: dispatch blocks until the sub-Agent reaches a terminal
  state, then returns the result summary string. If
  `timeout_seconds` is set and the sub-Agent has not finished, the
  manager MUST demote the task to background and return a
  "task demoted to background" message with the `task_id` (see D8 in
  design.md).

The dispatch parameters:

- `type: Literal["definition", "fork"]` — required
- `role: str | None` — required for `type="definition"`, ignored for
  `type="fork"`
- `prompt: str` — required, may contain `{var}` placeholders (resolved
  by caller before passing)
- `async_: bool = True`
- `timeout_seconds: int | None = None` — only effective for `async_=False`
- `parent_conversation: ConversationManager` — required for fork mode
  (used as snapshot source)
- `parent_denied_counts: dict[str, int]` — required for fork mode
- `main_agent_ref: Agent | None = None` — passed by Agent._v5_executor for
  reminder enqueue access

#### Scenario: Definition async dispatch
- **WHEN** `dispatch(type="definition", role="explorer", prompt="...",
  async_=True, ...)` is called
- **THEN** a new TaskInfo is created with state `pending`, then `running`
- **AND** the sub-Agent runs as a background `asyncio.Task`
- **AND** the function returns the `task_id` immediately

#### Scenario: Definition sync dispatch
- **WHEN** `dispatch(type="definition", role="explorer", prompt="...",
  async_=False, timeout_seconds=60, ...)` is called
- **AND** the sub-Agent completes in 30 seconds with summary "..."
- **THEN** the function returns "..." (the summary) after blocking

#### Scenario: Sync dispatch with timeout demotion
- **WHEN** `dispatch(type="definition", role="explorer", prompt="...",
  async_=False, timeout_seconds=60, ...)` is called
- **AND** the sub-Agent is still running at 60 seconds
- **THEN** the task state transitions to `timeout`
- **AND** `notification_pending` is set to True
- **AND** the function returns `"[sub-Agent 超时自动切后台,task_id=...]"`

#### Scenario: Fork dispatch forces async
- **WHEN** `dispatch(type="fork", async_=False, ...)` is called
- **THEN** the manager logs a warning ("fork mode forces async=true")
- **AND** the dispatch is treated as `async_=True`
- **AND** the function returns the `task_id`

#### Scenario: Tool filter empty set
- **WHEN** `dispatch` is called with a role whose `tools` intersect
  empty with `background_whitelist`
- **THEN** the dispatch raises `ToolFilterEmptyError` (or returns a
  `ToolResult` with `is_error=True` when called via the `task` tool)
- **AND** no TaskInfo is created (or it is created with state `failed`
  and the error is returned to the caller)

#### Scenario: Unknown role
- **WHEN** `dispatch(type="definition", role="nonexistent", ...)` is called
- **THEN** the dispatch returns a `ToolResult` with
  `is_error=True` and message "未知 Agent role: 'nonexistent'"
- **AND** no TaskInfo is created

### Requirement: The `task` tool definition
The `task` tool MUST be a `ToolDefinition` registered in the module-level
`ToolRegistry` singleton via `BaoZiCodeApp._register_task_tool` in
`on_mount`. Its schema:

- `name: "task"`
- `tool_type: "internal"` — bypasses Skill whitelist (consistent with
  `load_skill`)
- `description`: A ~150-token description explaining the two modes
  (definition / fork), async behavior, and example usage
- `parameters` (JSON Schema):
  - `type: string, enum: ["definition", "fork"], required`
  - `role: string, required for type=definition`
  - `prompt: string, required`
  - `async: boolean, default true`
  - `timeout_seconds: integer | null, default null`

The `task` tool's executor MUST be an `async` wrapper that:

1. Resolves the role via `app.subagents._runtime._registry.lookup(role)`
   (for definition mode)
2. Calls `app.subagents.dispatch(...)` with the appropriate parameters
3. For async mode: returns `ToolResult` with content `"[sub-Agent 已派发,
   task_id=..., 完成时主 Agent 顶部会有通知]"`
4. For sync mode: returns `ToolResult` with content = the sub-Agent's
   final summary (or the timeout demotion message)

#### Scenario: Task tool description mentions both modes
- **WHEN** the LLM reads the tool description
- **THEN** it sees clear guidance on when to use `definition` (clean
  context + role persona) vs `fork` (inherit context + cache hit)
- **AND** it sees the `async` default and how to override

#### Scenario: Task tool in get_all_tools
- **WHEN** `ToolRegistry.get_all_tools()` is called after `on_mount`
- **THEN** the list includes the `task` tool
- **AND** `task.tool_type == "internal"`

#### Scenario: Task tool idempotent registration
- **WHEN** `_register_task_tool` is called twice
- **THEN** the second call catches the `ValueError` from `register_tool`
- **AND** does not re-register (idempotent)

### Requirement: Result flow — main Agent idle vs running
When a sub-Agent reaches a terminal state, the result MUST be routed
back to the main Agent's conversation via one of two paths:

- **Main Agent idle**: A new `Message(role="user", content="[<role> 子对话结果]\n<summary>")`
  is added to the main `ConversationManager` immediately. The main
  Agent's next `Agent.run` (or next iteration within an existing run)
  will see this as the latest user message.

- **Main Agent running**: The result is queued in the manager's pending
  notifications list. On the main Agent's next iteration top
  (in `_inject_reminders`), the result is delivered as a
  `<system-reminder type="subagent_result" ttl="once">` block. After
  delivery, the task's `notification_pending` is reset to False.

The "main Agent idle" state is defined as: no `Agent.run` is currently
in flight. Implementation: the main Agent sets a `_is_running` flag at
the start of `run()` and clears it in the `finally` block.

#### Scenario: Async sub-Agent completes while main idle
- **WHEN** main Agent is not running
- **AND** a background sub-Agent completes with summary "found 3 bugs"
- **THEN** `main_conversation.add_user("[explorer 子对话结果]\nfound 3 bugs")`
  is called
- **AND** the main conversation now has one additional user message
- **AND** the main Agent (if started after this point) will process
  this message in its first iteration

#### Scenario: Async sub-Agent completes while main running
- **WHEN** main Agent is currently in `Agent.run`'s iteration loop
- **AND** a background sub-Agent completes with summary "..."
- **THEN** the manager enqueues a reminder via
  `main_agent.enqueue_reminder("subagent_result", "<formatted reminder>")`
- **AND** the task's `notification_pending` is set to True
- **AND** on the main Agent's next `_inject_reminders` call, the
  reminder appears as `<system-reminder type="subagent_result" ttl="once">`

#### Scenario: Failed sub-Agent result delivery
- **WHEN** a sub-Agent terminates with `state="failed"` and
  `error="tool execution timeout"`
- **THEN** the result is delivered with summary including the error
  trace
- **AND** the reminder body / user message starts with
  `[<role> 子对话失败]`

#### Scenario: Canceled sub-Agent result delivery
- **WHEN** a sub-Agent is canceled (via `cancel_all` from main cancel)
- **THEN** the result is delivered with summary "子对话已被取消"
- **AND** the reminder body / user message starts with
  `[<role> 子对话已取消]`

### Requirement: Three ways to enter background mode
The manager MUST support three triggers for transitioning a sub-Agent to
background execution:

1. **Explicit async=true (default)**: The `task` tool's `async` parameter
   defaults to `True`. Most sub-Agents start in the background.

2. **Sync dispatch with timeout demotion**: When `async_=False` is
   passed with `timeout_seconds`, and the sub-Agent exceeds the timeout,
   it is demoted to background (state `timeout`, `notification_pending=True`).
   The dispatch call returns a "task demoted" message with `task_id`.

3. **Manual switch (TUI)**: TUI status bar provides a key binding
   (Ctrl+Shift+B by default) that calls `subagent_manager.demote_to_background(task_id)`
   on the currently-running sync task. This is a UI affordance, not a
   programmatic API; spec requires the binding to exist but the
   implementation is in the TUI module.

For fork mode, `async_=False` is silently overridden to `True` (D8 in
design.md).

#### Scenario: Explicit async dispatch
- **WHEN** `task` tool is called with `async=true` (default)
- **THEN** dispatch returns `task_id` immediately
- **AND** sub-Agent runs in background

#### Scenario: Sync timeout demotion
- **WHEN** `task` tool is called with `async=false, timeout_seconds=30`
- **AND** sub-Agent is still running at 30 seconds
- **THEN** task state becomes `timeout`
- **AND** dispatch returns `"[sub-Agent 超时自动切后台,task_id=...]"`
- **AND** the result notification is queued for main Agent

#### Scenario: Manual demote from TUI
- **WHEN** user presses Ctrl+Shift+B while a sync sub-Agent is running
- **THEN** the manager calls `demote_to_background(task_id)`
- **AND** the task's state transitions to `running` (unchanged) but
  the dispatch promise (caller waiting) is cancelled with a
  "task demoted" message
- **AND** the result is delivered via the normal notification path
  when the sub-Agent eventually completes

### Requirement: Cascade cancellation
`SubAgentManager.cancel_all()` MUST cancel all `running` tasks by setting
each task's `cancel_event`. The Agent's main loop checks
`self._cancel_event` at the same safe points as the main Agent's
`_cancel_event` (per-iteration top, after each tool_result).

Sub-Agent failure or cancellation MUST NOT propagate to the main Agent
(no `agent.cancel()` call on the main). The main Agent continues
running its own loop. The result is delivered as a "subagent failed" or
"subagent canceled" reminder / user message (per Result flow spec).

#### Scenario: Main Agent cancel cascades to sub-Agents
- **WHEN** main Agent's `cancel()` is called
- **AND** 2 sub-Agents are running
- **THEN** `subagent_manager.cancel_all()` is called
- **AND** both sub-Agents' `cancel_event`s are set
- **AND** both sub-Agents terminate with `USER_CANCELLED` at their
  next safe point
- **AND** the main Agent's `cancel_event` is also set (it cancels
  itself)

#### Scenario: Sub-Agent failure does not cancel main
- **WHEN** a sub-Agent terminates with `STREAM_ERROR`
- **THEN** the main Agent's `cancel_event` is NOT set
- **AND** the main Agent continues running its own loop
- **AND** the failure is delivered to the main Agent as a
  `<system-reminder type="subagent_result" ttl="once">` (or user
  message if main is idle)

#### Scenario: Sub-Agent cancel does not cancel main
- **WHEN** a sub-Agent is canceled (by user via TUI)
- **THEN** the main Agent's `cancel_event` is NOT set
- **AND** the cancel is delivered to the main Agent via the standard
  notification path

### Requirement: TUI status bar reporting
`BaoZiCodeApp` (or `ChatScreen`) MUST poll `subagent_manager.list_tasks()`
on a regular interval (e.g., every 0.5s) and update the status bar with
the format `[agents: {running}/{done}]` where:

- `running` = count of tasks in `running` state
- `done` = count of tasks in `done` state (or any terminal state)

The status bar MUST update atomically (Textual `refresh`).

#### Scenario: Status bar reflects 0 running, 2 done
- **WHEN** 2 sub-Agents have completed and none are running
- **THEN** status bar shows `[agents: 0/2]`

#### Scenario: Status bar reflects 3 running, 1 done
- **WHEN** 3 sub-Agents are running and 1 has completed
- **THEN** status bar shows `[agents: 3/1]`

### Requirement: TUI sub-Agent collapse cards
`ChatScreen` MUST display a collapse card per sub-Agent task. The card
shows:

- Folded: `task_id` (short form) + role + state badge + first line of
  result (if done)
- Expanded: full sub-Agent streaming text + tool result cards (reusing
  the existing `ToolResultCard` widget) + final result text

Cards are placed in a separate scrollable container below the main
conversation. Click toggles fold/expand.

#### Scenario: New sub-Agent card appears
- **WHEN** a sub-Agent is dispatched (async)
- **THEN** a new card is added to the container with state badge "running"
- **AND** the card shows the role name and task_id

#### Scenario: Sub-Agent card updates on completion
- **WHEN** the sub-Agent transitions to `done`
- **THEN** the card's state badge changes to "done" (with green color)
- **AND** the result text is displayed

#### Scenario: Failed sub-Agent card
- **WHEN** the sub-Agent transitions to `failed`
- **THEN** the card's state badge is "failed" (red)
- **AND** the error message is displayed

### Requirement: Completion toast
When a sub-Agent transitions to a terminal state while the user is
typing in the main input box, a Textual `App.notify` toast appears for
0.5 seconds with the format:

```
explorer 完成: found 3 bugs in foo.py
```

The toast does NOT block the input box. It is purely informational.

#### Scenario: Completion toast appears
- **WHEN** user is typing
- **AND** a sub-Agent completes
- **THEN** a toast appears at the bottom of the screen for 0.5s
- **AND** the input box remains interactive

### Requirement: Per-session task scope
Tasks are scoped to the current session. When `BaoZiCodeApp.start_new_session()`
is called, the manager MUST clear its `_tasks` dict (after closing any
running tasks gracefully). The `task_id` format includes a timestamp to
avoid collisions across sessions.

#### Scenario: New session clears tasks
- **WHEN** `start_new_session()` is called
- **AND** 2 tasks are in `_tasks` (one running, one done)
- **THEN** the running task is canceled
- **AND** `_tasks` is empty after the call
- **AND** new dispatches use new task_ids with the new session's timestamp

### Requirement: Session archive excludes sub-Agent messages
Sub-Agent conversations MUST NOT be written to the main session's
JSONL archive. The sub-Agent's `ConversationManager` is constructed
with `archiver=None` (in `SubAgentRuntime.spawn`). Only the main
Agent's `ConversationManager` writes to JSONL.

#### Scenario: Sub-Agent messages not in JSONL
- **WHEN** a sub-Agent adds 5 messages to its conversation
- **AND** the main session has `sessions_enabled=True`
- **THEN** the main session's JSONL file does NOT contain those 5 messages
- **AND** only the main Agent's messages are in the JSONL
- **AND** the result notification (user message or reminder) IS in the
  JSONL (because it's added to the main conversation)

### Requirement: Hooks compatibility — subagent metadata
All lifecycle events fired by a sub-Agent's `Agent._fire_lifecycle_safe`
MUST include a `subagent` field in the payload when the payload is a
dict. Main Agent events have `subagent=None` (or the field is absent
for main Agent, depending on implementation choice).

The `subagent` dict has shape:
```python
{
    "task_id": str,
    "role": str | None,
    "type": Literal["definition", "fork"],
    "depth": int,  # always 1 in v1.2
}
```

Old hooks that do not read this field are not affected.

#### Scenario: Sub-Agent tool.pre has subagent field
- **WHEN** a sub-Agent runs and calls the `Read` tool
- **THEN** the `tool.pre` event payload includes
  `payload["subagent"] = {"task_id": ..., "role": "explorer",
  "type": "definition", "depth": 1}`

#### Scenario: Main Agent tool.pre has no subagent field
- **WHEN** the main Agent runs and calls the `Read` tool
- **THEN** the `tool.pre` event payload does NOT have a `subagent` key
  (or has it as `None` — implementation choice, both acceptable)

#### Scenario: Old hook ignores subagent field
- **WHEN** a hook handler reads `payload` and accesses
  `payload["tool_name"]` (old format)
- **AND** the payload is from a sub-Agent and has an extra `subagent` key
- **THEN** the hook handler still works (it ignores the extra key)
