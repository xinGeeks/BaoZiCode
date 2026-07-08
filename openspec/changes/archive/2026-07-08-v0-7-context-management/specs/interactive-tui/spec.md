## MODIFIED Requirements

### Requirement: Slash commands are routed correctly
The system MUST recognize the slash commands `/help`, `/clear`, `/exit`, `/model`, `/tools`, `/permissions`, `/permissions mode`, `/plan`, `/do`, `/auto`, `/stop`, `/status`, `/mcp`, `/mcp reconnect`, and `/compact` (v0.7) when they appear as the entire content of the input box, and route them to the corresponding handlers instead of sending them to the LLM. The `/permissions` command with no arguments MUST display the current policy; the `/permissions mode` command MUST open a three-option modal to switch permission mode. The `/compact` command MUST trigger context compaction: when no `Agent.run()` is in progress, it runs compaction immediately with `trigger="manual"` and a 3 000 token reserve; when an Agent loop is iterating, it sets the Agent's `_compact_requested` flag which is checked at the top of the next iteration, the current stream is cancelled, and the iteration restarts from the top with the compacted message list.

#### Scenario: /help shows available commands
- **WHEN** user types `/help` and presses Enter
- **THEN** the conversation area displays a help text listing all slash commands with one-line descriptions (including `/compact` with description "压缩对话历史(可降低 token 占用)" or equivalent)

#### Scenario: /clear resets the conversation
- **WHEN** user types `/clear` and presses Enter
- **THEN** the conversation area is cleared
- **AND** the in-memory message history (including any pending tool_use blocks) is emptied
- **AND** all files under `<project>/.baozicode/context/<this_session>/` are deleted
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
- **THEN** the conversation area displays the effective permission policy: current mode (strict / default / permissive), three-layer YAML sources (user-global / project / local), session rules count, and rule summary (top 10 rules grouped by tool)

#### Scenario: /permissions mode opens mode picker
- **WHEN** user types `/permissions mode` and presses Enter
- **THEN** a three-option modal appears: `strict`, `default`, `permissive`
- **AND** the current mode is visually marked
- **AND** selecting a mode closes the modal and updates the status bar
- **AND** pressing Escape cancels the selection

#### Scenario: Mode change does not hot-update the running Agent
- **WHEN** the user runs `/permissions mode permissive` while an Agent.run() is in progress
- **THEN** the running Agent MUST continue with its existing mode (which was captured at Agent construction time)
- **AND** the conversation area displays a notice: "模式已切换为 permissive，将在下一条消息生效"
- **AND** the next user message creates a new Agent instance with the new mode
- **AND** `/status` shows the new session-mode value even before the next message

#### Scenario: /compact during idle runs immediately
- **WHEN** user types `/compact` while no Agent.run() is in progress
- **THEN** the system runs compaction with `trigger="manual"` and a 3 000 token reserve
- **AND** the conversation area displays a line such as "已压缩: 45000 → 3200 tokens (5 条历史摘要,保留最近 7 条)" or equivalent

#### Scenario: /compact mid-iteration interrupts the current stream
- **WHEN** user types `/compact` while an Agent.run() is iterating (the LLM is streaming or tools are executing)
- **THEN** on the next `llm.stream()` entry point the flag is detected
- **AND** the current iteration's stream is cancelled before the next send
- **AND** compaction runs with `trigger="manual"` and a 3 000 token reserve
- **AND** the iteration restarts from the top with the compacted message list
- **AND** the LLM cannot observe any partial output from the cancelled stream (the partial text is dropped, not appended)

### Requirement: /status shows compaction stats when compactions have occurred
The system MUST extend the `/status` output to include a "compaction" subsection when one or more compactions have run in the current session. The subsection MUST show: total compaction count in this session, total tokens saved (sum of `tokens_before - tokens_after` across all compactions), and the timestamp of the most recent compaction. When no compaction has run, the compaction subsection MUST be omitted entirely.

#### Scenario: /status shows compaction stats after first compact
- **WHEN** user runs `/status` after the first Layer-2 summary has fired
- **THEN** the status output includes lines such as `compactions: 1`, `tokens_saved: 45000`, `last_compact: 2026-07-07T14:32:11Z`

#### Scenario: /status omits compaction stats when none happened
- **WHEN** user runs `/status` in a fresh session with no compactions
- **THEN** the status output omits the compaction subsection entirely
- **AND** no `compactions: 0` placeholder line is shown

#### Scenario: /status aggregates across multiple compactions
- **WHEN** three compactions have run in this session, each saving 20 K, 15 K, and 30 K tokens respectively
- **THEN** the status output shows `compactions: 3` and `tokens_saved: 65000`
