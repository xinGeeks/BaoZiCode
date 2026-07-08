# command-context Specification (v0.9)

## Purpose
A narrow runtime interface passed to every `CommandHandler`. The interface
isolates command implementations from Textual specifics so that:

- Commands can be unit-tested with a stub `CommandContext` (no Textual app)
- A future second front-end (CLI, Web) can implement the same interface
- `baozicode/commands/` does NOT depend on `textual` (preserve the
  established dependency direction: `tui → commands → ...`)

## Requirements

### Requirement: Context surface area
`CommandContext` MUST expose exactly these seven operations plus two property
accessors:

```
show_info(text: str) -> None
show_error(text: str) -> None
send_to_agent(text: str) -> None
switch_mode(new_mode: PermissionMode | None) -> None
get_token_usage() -> UsageStats
refresh_status() -> None
push_modal(screen: TextualScreen) -> None       # the one textual-shaped escape hatch

app: BaoZiCodeApp | None                        # property accessor (escape hatch)
config: AppConfig                               # property accessor
```

All methods are synchronous (no async required on the interface itself).
Handlers that need IO are still async; the interface just doesn't surface
async ops.

#### Scenario: Handler uses show_info to log
- **WHEN** a `LOCAL` handler calls `ctx.show_info("status: 3 sessions")`
- **THEN** the chat log emits a single info line with that text

#### Scenario: Handler uses show_error for visible errors
- **WHEN** a handler raises during execution
- **THEN** the dispatcher catches and emits `ctx.show_error("<exception type>: <msg>")`
- **AND** the user sees this message in the chat log

#### Scenario: Handler calls send_to_agent
- **WHEN** a `PROMPT` handler calls `ctx.send_to_agent(prompt_text)` (or returns PromptResult)
- **THEN** the prompt text is fed into the Agent loop as the next user message
- **AND** the chat log shows it as a user-role entry

### Requirement: send_to_agent is observable
`ctx.send_to_agent(text)` MUST:
- Append a user-role message to `self.app.conversation` (mirroring how a typed
  message would have arrived)
- Trigger `Agent.run(text)` exactly once (or schedule it in the active worker)
- Show the text in the chat UI as a user-role entry

If `self.app._current_agent` is already running (busy), the call MUST
queue the message via the same path the input box uses — not bypass queuing.

#### Scenario: Idle state, send_to_agent
- **WHEN** the Agent is idle
- **AND** a `PROMPT` handler returns `PromptResult(text="review this")`
- **THEN** an `Agent.run("review this")` worker is started
- **AND** the chat log shows `> review this` as the next user turn

#### Scenario: Busy state, send_to_agent
- **WHEN** the Agent is currently processing a previous turn
- **AND** `/review` is invoked
- **THEN** the prompt text is queued behind the in-flight turn
- **AND** no second `Agent.run` worker is started (serialized)

### Requirement: switch_mode is permissive only
`ctx.switch_mode(new_mode)` MUST update `self.app.session_mode` so the NEXT
Agent run picks it up. It MUST NOT retroactively change the in-flight Agent
(consistent with v0.5 contract: "mode captured at Agent.__init__ time").

#### Scenario: switch_mode(None) clears override
- **WHEN** user types `/permission default` and `ctx.switch_mode("default")` is called
- **THEN** `app.session_mode = "default"`
- **AND** the NEXT `Agent` constructed will use that mode
- **AND** the running Agent (if any) is unaffected

#### Scenario: switch_mode(None) clears session override
- **WHEN** `ctx.switch_mode(None)` is called
- **THEN** `app.session_mode = None`
- **AND** the NEXT Agent falls back to `app.permissions_v5.mode` (the YAML-defined mode)

### Requirement: push_modal is the one Textual-shaped escape hatch
`ctx.push_modal(screen)` exists so commands like `/session` (which need a
real modal) can still delegate to TUI. The receiver (`commands/`) does NOT
type-annotate the parameter as a specific Textual screen class — just
`Screen` from `textual.screen`. This keeps the textual dependency localized
to the call site.

#### Scenario: /session pushes the existing StartupSessionScreen
- **WHEN** `/session` runs
- **THEN** it constructs `StartupSessionScreen(...)` and calls `ctx.push_modal(screen)`
- **AND** `BaoZiCodeApp`'s wrapper awaits the dismiss result and routes to
  `resume_session(...)` / `start_new_session()` based on the chosen value

#### Scenario: Modal pushed without blocking Agent
- **WHEN** a modal is pushed via `ctx.push_modal(...)`
- **THEN** the Agent loop is NOT cancelled
- **AND** the user can dismiss the modal to continue

### Requirement: Context does NOT import business logic
The module `baozicode/commands/context.py` MUST:
- Import from `baozicode/llm/base.py` ONLY for type annotation of
  `UsageStats` and `ContentDelta`
- Import from `baozicode/permissions/types.py` ONLY for `PermissionMode`
- Import `from textual.screen import Screen` for the `push_modal` type hint
- NOT import from `baozicode/agent/`, `baozicode/tui/`, `baozicode/prompt/`,
  or `baozicode/sessions/`

A `BaoZiCodeApp` reference MAY be exposed via the `app` property, but the
context itself does not import from `baozicode/app.py` to avoid a cycle.
The `app` reference is provided by the runtime wrapper in `tui/chat_screen.py`
when constructing the context.

#### Scenario: Static import audit
- **WHEN** `python -c "import baozicode.commands.context"` runs
- **THEN** the import set MUST NOT include `baozicode.agent`, `baozicode.tui`,
  `baozicode.prompt`, or `baozicode.sessions`
