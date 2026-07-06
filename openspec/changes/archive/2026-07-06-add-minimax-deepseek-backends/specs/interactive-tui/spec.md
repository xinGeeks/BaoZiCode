## MODIFIED Requirements

### Requirement: Slash commands are routed correctly
The system MUST recognize the four slash commands `/help`, `/clear`, `/exit`, `/model` when they appear as the entire content of the input box, and route them to the corresponding handlers instead of sending them to the LLM.

#### Scenario: /help shows available commands
- **WHEN** user types `/help` and presses Enter
- **THEN** the conversation area displays a help text listing all four slash commands with one-line descriptions

#### Scenario: /clear resets the conversation
- **WHEN** user types `/clear` and presses Enter
- **THEN** the conversation area is cleared
- **AND** the in-memory message history is emptied
- **AND** the input box is refocused

#### Scenario: /exit quits the application
- **WHEN** user types `/exit` and presses Enter (or presses Ctrl+C)
- **THEN** the TUI exits cleanly with exit code 0

#### Scenario: /model shows four-backend picker
- **WHEN** user types `/model` and presses Enter
- **THEN** a selection prompt appears listing all four backends (`anthropic`, `openai`, `minimax`, `deepseek`) with their configured model names
- **AND** the currently-active backend is visually marked (e.g., "(当前)" suffix or distinct style)
- **AND** selecting a different backend MUST switch the active backend without restarting the TUI
- **AND** a confirmation line (e.g., "已切换到 minimax · MiniMax-M3") appears in the conversation area
- **AND** selecting the already-active backend (or pressing Escape) MUST close the picker without making changes
