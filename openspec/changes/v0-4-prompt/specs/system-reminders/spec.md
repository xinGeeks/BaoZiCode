# system-reminders Specification

## Purpose
TBD - created by archiving change v0-4-prompt. Update Purpose after archive.

## ADDED Requirements

### Requirement: SystemReminder dataclass models runtime injection
The system MUST provide a `SystemReminder` dataclass with three fields: `kind: Literal["env", "plan_mode", "task_complete", "cancel"]`, `content: str`, and `ttl: Literal["once", "static", "session"]` (default `"static"`).

#### Scenario: Construct a plan_mode reminder
- **WHEN** `SystemReminder(kind="plan_mode", content="...")` is constructed
- **THEN** `reminder.kind == "plan_mode"`, `reminder.content == "..."`, and `reminder.ttl == "static"` (default)

### Requirement: Reminders are serialized as user-role messages with <system-reminder> tags
The system MUST serialize each `SystemReminder` to a `Message(role="user", content=str)` where the content is the literal string `<system-reminder type="{kind}" ttl="{ttl}">\n{content}\n</system-reminder>`.

#### Scenario: Env reminder serialization
- **WHEN** `SystemReminder(kind="env", content="cwd: /x")` is serialized
- **THEN** the resulting message has `role="user"` and `content` equal to `<system-reminder type="env" ttl="static">\ncwd: /x\n</system-reminder>`

### Requirement: Reminders are spliced before messages[-1], not at messages[0]
The system MUST inject reminder messages between `messages[:-1]` and `messages[-1]` so the final user message remains at the end of the list. The agent's `_inject_reminders()` method MUST be the single injection point used in `Agent.run` for every LLM call.

#### Scenario: Env reminder position
- **WHEN** the conversation has a single user message "hi" and an env reminder is generated
- **THEN** the messages list passed to `llm.stream` has length 2
- **AND** `messages[-1]` is the user message "hi" (unchanged)
- **AND** `messages[-2]` is the env reminder with `<system-reminder type="env"`

#### Scenario: Reminder is not at messages[0]
- **WHEN** the conversation has historical messages followed by a new user message
- **AND** a reminder is injected
- **THEN** `messages[0]` is unchanged historical content (NOT a reminder)
- **AND** the reminder is the second-to-last entry, before the user message

### Requirement: PlanModeReminder emits at iteration 1 and every N iterations
The system MUST provide a `PlanModeReminder` class with a `should_emit(iteration: int) -> bool` method that returns `True` exactly when: (1) `plan_mode=True`, AND (2) `iteration == 1` OR `(iteration - 1) % interval == 0`. The default `interval` is 5.

#### Scenario: First iteration emits in plan mode
- **WHEN** PlanModeReminder(plan_mode=True, interval=5) is constructed
- **THEN** `should_emit(1) == True`

#### Scenario: Iteration 5, 10, 15 emit; 6, 7, 9, 11 do not
- **WHEN** PlanModeReminder(plan_mode=True, interval=5) is constructed
- **THEN** `should_emit(5) == True`, `should_emit(10) == True`, `should_emit(15) == True`
- **AND** `should_emit(6) == False`, `should_emit(7) == False`, `should_emit(9) == False`, `should_emit(11) == False`

#### Scenario: Plan mode disabled never emits
- **WHEN** PlanModeReminder(plan_mode=False, interval=5) is constructed
- **THEN** `should_emit(i) == False` for every `i` in 1..20

### Requirement: Agent._inject_reminders combines env, plan_mode, and one-shot reminders
The system MUST implement `Agent._inject_reminders(messages, iteration)` that, when `enable_system_reminders=True`, builds the message list in this order: (1) the `dynamic_messages` from `BuiltPrompt` (containing the env reminder), (2) the plan_mode reminder IF `PlanModeReminder.should_emit(iteration)`, and splices all of them before `messages[-1]`. When `enable_system_reminders=False`, the method MUST return `messages` unchanged.

#### Scenario: Plan mode reminder at iteration 1
- **WHEN** Agent is in plan mode, iteration 1, enable_system_reminders=True
- **THEN** the messages passed to llm.stream contain a `<system-reminder type="plan_mode">` block

#### Scenario: Plan mode reminder at iteration 6
- **WHEN** Agent is in plan mode, iteration 6, interval=5
- **THEN** the messages passed to llm.stream contain a `<system-reminder type="plan_mode">` block

#### Scenario: Plan mode reminder skipped at iteration 7
- **WHEN** Agent is in plan mode, iteration 7, interval=5
- **THEN** the messages passed to llm.stream do NOT contain a `<system-reminder type="plan_mode">` block

#### Scenario: All reminders skipped when enable_system_reminders=False
- **WHEN** Agent is constructed with config.agent.enable_system_reminders=False
- **THEN** the messages passed to llm.stream contain NO `<system-reminder` substring in any user message
