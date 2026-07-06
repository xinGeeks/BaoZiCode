# plan-mode Specification (NEW)

## Purpose
Define the two-phase plan/execute workflow: `/plan <task>` runs a single Agent iteration in plan mode (only `side_effect=False` tools available), letting the LLM explore and produce a plan; `/do` switches out of plan mode for full-tool execution. `/plan` is followed by a `plan_ready` idle state where the user can append clarifications before issuing `/do`.

## ADDED Requirements

### Requirement: Plan mode filters available tools by side_effect=False
When an `Agent` is constructed with `plan_mode=True`, the Agent MUST expose only tools whose `ToolDefinition.side_effect` is `False`. Specifically: `Read`, `Grep`, `Glob`, `WebFetch`. The Agent MUST NOT yield `tool_call` events for any `side_effect=True` tool while in plan mode — even if the LLM requests one, it MUST be treated as an unknown tool (feeding error result back, applying the `UNKNOWN_TOOL_HALLUCINATION` guard).

#### Scenario: Plan mode offers only read-only tools
- **WHEN** `Agent(plan_mode=True)` is constructed
- **THEN** `agent.available_tools` returns exactly `[Read, Grep, Glob, WebFetch]`

#### Scenario: Plan mode rejects Write/Edit/Bash requests
- **WHEN** the LLM, during a plan-mode run, requests `Write` (side_effect=True)
- **THEN** the Agent treats this as an unknown tool request
- **AND** applies the same retry/terminate logic as for genuinely unknown tool names

### Requirement: /plan slash command triggers a plan-mode run
The TUI MUST recognize `/plan <task>` as a slash command. The argument after `/plan` (the rest of the line) is the user's task. The TUI MUST construct an `Agent(plan_mode=True)` and call `agent.run(task)`, rendering all events normally.

#### Scenario: /plan runs one iteration in plan mode
- **WHEN** the user types `/plan refactor the auth module` and presses Enter
- **THEN** the TUI displays a user message card with the task text
- **AND** an `Agent(plan_mode=True)` is instantiated
- **AND** the LLM is called with only `[Read, Grep, Glob, WebFetch]` as available tools
- **AND** the resulting plan text is rendered as the assistant's response

#### Scenario: /plan without task is a usage error
- **WHEN** the user types `/plan` with no task text
- **THEN** the TUI displays an info message: "用法: /plan <task description>" (and does not start an Agent run)

### Requirement: Agent enters plan_ready idle state after /plan completes
After a plan-mode Agent run completes (any termination reason), the Agent MUST NOT be reused for further iterations. The TUI MUST transition to a `plan_ready` state where:
- The conversation history contains the plan as the most recent assistant message.
- The input box is re-enabled.
- The user can submit additional user messages, which are appended to the conversation history BUT do NOT trigger another Agent run.
- The status indicator shows that the system is waiting for `/do`.

#### Scenario: plan_ready allows user input without triggering Agent
- **WHEN** the user submits a clarification message after `/plan` completes
- **THEN** the message is added to the conversation history as a user message
- **AND** NO Agent run is started
- **AND** the input box remains enabled

#### Scenario: Multiple clarifications accumulate
- **WHEN** the user submits three clarification messages after `/plan`
- **THEN** all three appear in the conversation history in order
- **AND** none triggers an Agent run
- **AND** the LLM does not see them until `/do` is issued

### Requirement: /do slash command transitions out of plan mode and executes
The TUI MUST recognize `/do` as a slash command. When issued while in `plan_ready` state, the TUI MUST:
- Clear the `plan_ready` flag.
- Construct a new `Agent(plan_mode=False)` (full toolset).
- Run the Agent with the user's task derived from the conversation history (specifically: the original `/plan <task>` plus any clarifications).
- Render all events normally.

#### Scenario: /do executes with full toolset
- **WHEN** the user types `/do` after `/plan refactor the auth module` plus clarifications
- **THEN** a new `Agent(plan_mode=False)` is instantiated
- **AND** the Agent has access to all 7 tools (Read, Write, Edit, Bash, Grep, Glob, WebFetch)
- **AND** the Agent runs with the full conversation history (plan + clarifications) as context

#### Scenario: /do while not in plan_ready is a no-op
- **WHEN** the user types `/do` without having previously run `/plan`
- **THEN** the TUI displays an info message: "当前不在 plan 模式,请先用 /plan <task> 制定计划"
- **AND** no Agent run is started

### Requirement: /auto slash command clears plan_ready without executing
The TUI MUST recognize `/auto` as a slash command. When issued while in `plan_ready` state, the TUI MUST clear the flag WITHOUT starting an Agent run, returning to a neutral idle state. The conversation history is preserved.

#### Scenario: /auto exits plan mode without running
- **WHEN** the user types `/auto` while in plan_ready
- **THEN** the plan_ready flag is cleared
- **AND** no Agent run is started
- **AND** the user can issue a fresh `/plan` or send a regular message

#### Scenario: /auto when not in plan_ready is a no-op
- **WHEN** the user types `/auto` outside plan_ready
- **THEN** the TUI displays an info message: "当前不在 plan 模式"

### Requirement: Plan mode does NOT affect the normal non-plan workflow
When the TUI is NOT in `plan_ready` state (the default after app start, after `/clear`, or after `/do`/`/auto`), sending a regular user message MUST trigger a normal `Agent(plan_mode=False)` run with all 7 tools — identical to v0.2 behavior (modulo the new event-driven Agent).

#### Scenario: Normal message uses full toolset
- **WHEN** the user sends "add a hello world endpoint" without prior `/plan`
- **THEN** `Agent(plan_mode=False)` runs with all 7 tools available

#### Scenario: /clear exits plan_ready
- **WHEN** the user issues `/clear` while in plan_ready
- **THEN** the conversation history is cleared AND the plan_ready flag is cleared
- **AND** the next user message triggers a normal (non-plan) Agent run
