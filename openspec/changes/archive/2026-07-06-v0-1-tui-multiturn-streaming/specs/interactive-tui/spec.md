## ADDED Requirements

### Requirement: TUI launches with banner and welcome
The system MUST display an ASCII 包子 (baozi) banner and a short welcome message in the conversation area when the user starts `baozicode`.

#### Scenario: Cold start shows banner
- **WHEN** user runs `baozicode` in a terminal
- **THEN** the TUI appears within 2 seconds with the ASCII baozi banner rendered at the top of the conversation area
- **AND** a welcome line below the banner shows the current backend and model name (e.g., "Anthropic · claude-sonnet-4-6")

#### Scenario: Missing terminal capabilities degrade gracefully
- **WHEN** the user's terminal does not support Unicode
- **THEN** the system MUST still launch the TUI and the banner falls back to ASCII-only characters

### Requirement: User can submit messages via input box
The system MUST provide an input box at the bottom of the TUI where the user can type a message and submit it by pressing Enter.

#### Scenario: Submitting a message sends it to the LLM
- **WHEN** user types a non-empty message and presses Enter
- **THEN** the message is appended to the conversation area (right-aligned or styled as "user")
- **AND** the system begins streaming the LLM response in the same conversation area

#### Scenario: Empty input is ignored
- **WHEN** user presses Enter with an empty or whitespace-only input
- **THEN** the system MUST NOT send a request to the LLM and MUST NOT modify the conversation area

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

#### Scenario: /model shows current model
- **WHEN** user types `/model` and presses Enter
- **THEN** a selection prompt appears with the currently-active model and the alternative backend's model
- **AND** selecting the alternative MUST switch the active backend without restarting the TUI
- **AND** a confirmation line (e.g., "Switched to OpenAI · gpt-5") appears in the conversation area

### Requirement: Input is locked during streaming
The system MUST disable the input box while the LLM is generating a response, and MUST re-enable it (and refocus it) once the response completes or fails.

#### Scenario: Input disabled while LLM streams
- **WHEN** the user has submitted a message and the LLM is streaming the response
- **THEN** the input box is visibly disabled (grayed out or non-interactive)
- **AND** typing characters has no effect

#### Scenario: Input re-enabled after completion
- **WHEN** the LLM finishes streaming (success or error)
- **THEN** the input box becomes interactive again
- **AND** the input box is refocused so the user can immediately type the next message

### Requirement: Streaming errors are surfaced in the conversation
The system MUST catch any exception thrown by the LLM client during streaming, render a human-readable error message in the conversation area, and re-enable the input box.

#### Scenario: Network failure mid-stream
- **WHEN** the streaming connection is interrupted (e.g., timeout, DNS failure)
- **THEN** the conversation area shows an error line styled distinctly (e.g., red text: "✗ Error: connection failed")
- **AND** the input box is re-enabled and refocused
- **AND** any partial text already received from the LLM is preserved in the conversation area

#### Scenario: API authentication failure
- **WHEN** the API returns 401/403 (e.g., missing or invalid API key)
- **THEN** the conversation area shows an authentication-specific error message
- **AND** the input box is re-enabled
