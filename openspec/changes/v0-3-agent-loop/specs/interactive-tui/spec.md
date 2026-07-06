## MODIFIED Requirements

### Requirement: Slash commands are routed correctly
The system MUST recognize the slash commands `/help`, `/clear`, `/exit`, `/model`, `/tools`, `/permissions`, `/plan`, `/do`, `/auto`, `/stop`, `/status` when they appear as the entire content of the input box, and route them to the corresponding handlers instead of sending them to the LLM.

#### Scenario: /help shows available commands
- **WHEN** user types `/help` and presses Enter
- **THEN** the conversation area displays a help text listing all slash commands with one-line descriptions (v0.2's `/tools` and `/permissions`, plus v0.3's `/plan`, `/do`, `/auto`, `/stop`, `/status`)

#### Scenario: /clear resets the conversation and exits plan mode
- **WHEN** user types `/clear` and presses Enter
- **THEN** the conversation area is cleared
- **AND** the in-memory message history (including any pending tool_use blocks) is emptied
- **AND** if the TUI was in `plan_ready` state, the flag is cleared
- **AND** the input box is refocused

#### Scenario: /exit quits the application
- **WHEN** user types `/exit` and presses Enter (or presses Ctrl+C while no Agent is running)
- **THEN** the TUI exits cleanly with exit code 0

#### Scenario: /model shows four-backend picker
- **WHEN** user types `/model` and presses Enter
- **THEN** a selection prompt appears listing all four backends (`anthropic`, `openai`, `minimax`, `deepseek`) with their configured model names
- **AND** the currently-active backend is visually marked (e.g., "(当前)" suffix or distinct style)
- **AND** selecting a different backend MUST switch the active backend without restarting the TUI
- **AND** a confirmation line (e.g., "已切换到 minimax · MiniMax-M3") appears in the conversation area
- **AND** selecting the already-active backend (or pressing Escape) MUST close the picker without making changes

#### Scenario: /tools lists available tools with risk levels
- **WHEN** user types `/tools` and presses Enter
- **THEN** the conversation area displays all seven tools grouped by risk: a "Low risk (auto)" group listing `Read`, `Grep`, `Glob`, `WebFetch` and a "High risk (confirmation required)" group listing `Write`, `Edit`, `Bash`

#### Scenario: /permissions shows the current policy
- **WHEN** user types `/permissions` and presses Enter
- **THEN** the conversation area displays the effective permission policy: which tools are auto-allowed, whether batch confirmation is on, and whether Bash cwd is locked

## ADDED Requirements

### Requirement: TUI subscribes to Agent events instead of driving the loop
The TUI MUST NOT implement the agent loop itself (no nested `while True`, no direct LLM stream iteration in `ChatScreen`). The TUI MUST instantiate an `Agent`, call `agent.run(user_message)`, and consume `AgentEvent`s to drive rendering.

#### Scenario: ChatScreen delegates to Agent
- **WHEN** the user submits a non-slash-command message
- **THEN** the TUI creates or reuses an `Agent` instance
- **AND** iterates `agent.run(message)` to receive events
- **AND** renders each event (text → Markdown stream; tool_call → ToolCallCard; tool_result → ToolResultCard; usage → log; progress → status bar; done → finalize; error → error card)

#### Scenario: Agent event stream replaces inline loop
- **WHEN** the Agent yields multiple `text` events
- **THEN** the TUI writes each to the active Markdown widget in arrival order
- **AND** does NOT maintain its own `full_text` accumulator (the StreamCollector owns this state)

### Requirement: Esc and Ctrl+C cancel the running Agent
When an Agent is running, pressing Esc or Ctrl+C MUST call `agent.cancel()` and NOT exit the application. When no Agent is running, pressing Ctrl+C MUST exit the application (v0.2 behavior preserved). The TUI MUST re-enable the input box after the `done(USER_CANCELLED)` event arrives.

#### Scenario: Esc during Agent run cancels
- **WHEN** the user presses Esc while the Agent is streaming or executing tools
- **THEN** `agent.cancel()` is called
- **AND** at the next safe checkpoint the Agent yields `done(USER_CANCELLED)`
- **AND** the TUI displays "cancelled by user" in the conversation area
- **AND** the input box is re-enabled
- **AND** the application does NOT exit

#### Scenario: Ctrl+C during Agent run cancels
- **WHEN** the user presses Ctrl+C while the Agent is running
- **THEN** the same behavior as Esc above (cancel Agent, keep app)

#### Scenario: Ctrl+C when idle exits the app
- **WHEN** the user presses Ctrl+C while no Agent is running and no modal is open
- **THEN** the application exits cleanly (v0.2 behavior preserved)

#### Scenario: Ctrl+C inside a Modal still closes the Modal first
- **WHEN** a Modal is open (e.g., PermissionModal) and the user presses Ctrl+C
- **THEN** the Modal is dismissed (its handler runs) and the application exits

### Requirement: TUI displays iteration progress
The TUI MUST maintain a status indicator (typically a bottom bar or a fixed-position label) that reflects the latest `progress` event from the Agent. The indicator MUST show `iteration/max · phase` (e.g., `● 3/20 · tool_exec`).

#### Scenario: Progress bar updates during a run
- **WHEN** the Agent yields `AgentEvent(type="progress", payload={"iteration": 3, "max": 20, "phase": "tool_exec"})`
- **THEN** the status indicator updates to `3/20 · tool_exec`

#### Scenario: Status cleared after done
- **WHEN** the Agent yields `done`
- **THEN** the status indicator is hidden or replaced with an idle label

### Requirement: TUI exposes session token usage via /status
The TUI MUST recognize `/status` as a slash command. When issued, the TUI displays the current session total token usage (accumulated from `usage` events since app start or last `/clear`).

#### Scenario: /status shows token totals
- **WHEN** user types `/status` and presses Enter
- **THEN** the conversation area shows a line with the accumulated input/output/cache tokens for the current session

#### Scenario: /status with no usage yet
- **WHEN** user types `/status` and no Agent run has produced usage events
- **THEN** the conversation area shows "(no token usage recorded yet)"
