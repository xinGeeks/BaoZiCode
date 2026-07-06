## MODIFIED Requirements

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
The system MUST recognize the slash commands `/help`, `/clear`, `/exit`, `/model`, `/tools`, `/permissions` when they appear as the entire content of the input box, and route them to the corresponding handlers instead of sending them to the LLM.

#### Scenario: /help shows available commands
- **WHEN** user types `/help` and presses Enter
- **THEN** the conversation area displays a help text listing all slash commands with one-line descriptions (including v0.2 additions `/tools` and `/permissions`)

#### Scenario: /clear resets the conversation
- **WHEN** user types `/clear` and presses Enter
- **THEN** the conversation area is cleared
- **AND** the in-memory message history (including any pending tool_use blocks) is emptied
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

#### Scenario: /tools lists available tools with risk levels
- **WHEN** user types `/tools` and presses Enter
- **THEN** the conversation area displays all seven tools grouped by risk: a "Low risk (auto)" group listing `Read`, `Grep`, `Glob`, `WebFetch` and a "High risk (confirmation required)" group listing `Write`, `Edit`, `Bash`
- **AND** each entry shows the tool's one-line description

#### Scenario: /permissions shows the current policy
- **WHEN** user types `/permissions` and presses Enter
- **THEN** the conversation area displays the effective permission policy: which tools are auto-allowed, whether batch confirmation is on, and whether Bash cwd is locked
- **AND** the user can see the source of each setting (config file vs default)

### Requirement: Input is locked during streaming and tool execution
The system MUST disable the input box while the LLM is generating a response OR while a tool is being executed (including awaiting user confirmation), and MUST re-enable it (and refocus it) once the response completes, fails, or the tool chain ends.

#### Scenario: Input disabled while LLM streams
- **WHEN** the user has submitted a message and the LLM is streaming the response
- **THEN** the input box is visibly disabled (grayed out or non-interactive)
- **AND** typing characters has no effect

#### Scenario: Input disabled while a tool confirmation modal is open
- **WHEN** a high-risk tool is being requested by the LLM and a confirmation modal is on screen
- **THEN** the input box remains disabled
- **AND** only the modal's Y/N keys are interactive

#### Scenario: Input re-enabled after completion
- **WHEN** the LLM finishes streaming (success or error) and any tool chain has resolved
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

## ADDED Requirements

### Requirement: Tool invocations are rendered as visible cards in the conversation
The system MUST render every tool invocation as a visible "card" widget in the conversation area: a 🔧 card showing the tool name and arguments before execution, and a 📄 card showing the result content (truncated if very long) after execution. Cards MUST appear in the conversation area in the order they occur during the stream.

#### Scenario: Low-risk tool shows invocation and result cards
- **WHEN** the LLM invokes `Read` with `{"file_path": "README.md"}`
- **THEN** a 🔧 card appears in the conversation showing "Read README.md" and the parameter list
- **AND** after execution, a 📄 card appears showing the truncated file content

#### Scenario: High-risk tool shows confirmation modal before execution
- **WHEN** the LLM invokes `Bash` with `{"command": "rm old.txt"}`
- **THEN** the text stream pauses
- **AND** a confirmation modal appears (centered, blocking) showing the exact command and arguments
- **AND** the modal accepts Y (allow), N (deny), or Escape (cancel)
- **AND** if the user denies, a red ✗ card replaces the would-be 📄 card with the text "Denied by user"

### Requirement: Stream pauses for tool execution and resumes after tool_result is fed back
The system MUST pause the LLM text stream when a `ContentDelta(type="tool_use")` is yielded, execute the requested tool (after any required confirmation), append a `Message(role="tool", content=[<tool_result block>])` to the conversation history, and then resume the stream by calling the backend again with the updated messages list. The user MUST see tool cards in the conversation area as visual anchors for each pause/resume cycle.

#### Scenario: Single tool pause-and-resume
- **WHEN** the LLM emits text, then a tool_use, then more text
- **THEN** text is streamed up to the tool_use
- **AND** the tool executes (with confirmation if high-risk)
- **AND** the tool_result is appended to conversation history
- **AND** the LLM is called again with the augmented history
- **AND** the resumed text stream renders into a NEW Markdown widget (continuing the assistant turn visually)

#### Scenario: Multiple sequential tool calls in one turn
- **WHEN** the LLM emits Read, then Grep, then text in one turn
- **THEN** each tool is executed in sequence with its own card
- **AND** the final text stream resumes once all tools have returned

### Requirement: Permission modal follows high-risk-tool confirmation flow
The system MUST display a modal confirmation dialog before executing any tool whose `ToolDefinition.risk == "high"`. The modal MUST show the tool name, the full arguments, and three actions: allow (Y), deny (N), cancel (Escape). The system MUST NOT execute the tool until the user explicitly allows it.

#### Scenario: Default per-call confirmation
- **WHEN** `permissions.batch_confirm` is `false` (default) or unset
- **THEN** every high-risk tool call shows an individual confirmation modal

#### Scenario: Batch confirmation available for sequential same-type tools
- **WHEN** `permissions.batch_confirm` is `true`
- **AND** two or more consecutive tool calls have the same `name` (e.g., three `Bash` calls in a row)
- **THEN** the first call shows a modal with an additional "Allow all remaining" option
- **AND** choosing it allows all subsequent calls of the same name in this turn without further modals

### Requirement: Tool cards remain in the conversation history visually
The system MUST NOT remove tool cards from the conversation area when `/clear` is invoked mid-stream or after a tool error — the cards from the current turn remain visible until the user explicitly clears the screen. (The conversation history list, separate from the visual cards, IS cleared by `/clear`.)

#### Scenario: Cards persist across tool failures
- **WHEN** a tool execution raises an exception (e.g., file not found)
- **THEN** the 🔧 card remains visible
- **AND** the 📄 card is replaced with a red ✗ card showing the error message