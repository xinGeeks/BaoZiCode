# slash-dispatcher Specification (v0.9)

## Purpose
The single entry point that decides whether a user's keystroke sequence ends up
as a slash command or a regular Agent message. Also provides real-time Tab
completion that runs on every keystroke (not just on Enter).

## Requirements

### Requirement: Entry split on leading slash
When the user presses Enter in the input box, the dispatcher MUST:

- Take the raw input string
- If input is empty or whitespace-only → ignore (no-op)
- If input starts with `/` → parse as a command (see parse_command spec)
- Otherwise → forward to the Agent loop as a normal user message

#### Scenario: Empty input
- **WHEN** user presses Enter with empty input
- **THEN** nothing happens (no Agent call, no error)

#### Scenario: Whitespace-only input
- **WHEN** user presses Enter with `"   \n"`
- **THEN** nothing happens

#### Scenario: Plain message
- **WHEN** user types `请总结上次对话` (no leading slash)
- **THEN** the dispatcher forwards the text to the Agent loop
- **AND** the slash command path is not entered

#### Scenario: Slash prefix
- **WHEN** user types `/help`
- **THEN** the dispatcher invokes the help command handler
- **AND** the Agent loop does NOT receive `/help` as a user message

### Requirement: parse_command splits on first whitespace
The dispatcher MUST split command input on the FIRST whitespace only:

- Everything before the first space (or end of string) is the command name
- Everything after is `args` (may be empty string)
- Leading and trailing whitespace on `args` is stripped

#### Scenario: Command with no args
- **WHEN** input is `/status`
- **THEN** `name="status"`, `args=""`

#### Scenario: Command with args
- **WHEN** input is `/permission  strict  ` (trailing spaces)
- **THEN** `name="permission"`, `args="strict"`

#### Scenario: Command with multi-word args
- **WHEN** input is `/review 5 轮前`
- **THEN** `name="review"`, `args="5 轮前"`

### Requirement: Unknown command path
If `registry.lookup(name)` returns `None` after lowercasing, the dispatcher
MUST emit `ctx.show_error("未知命令: /<name>. 输入 /help 查看可用命令")`.
The Agent loop MUST NOT receive the input.

#### Scenario: Unknown command
- **WHEN** user types `/foobar`
- **THEN** the chat log shows an error message mentioning `/foobar`
- **AND** the message guides the user to `/help`

### Requirement: Tab completion on input changes
On every keystroke (textual `Input.on_input_changed` event), the dispatcher
MUST compute completion candidates for the current input:

- Candidates are commands (and aliases) that start with the current input
  prefix (case-insensitive)
- Hidden commands are excluded from the candidates
- Return value is `list[str]`: the matched primary command names (NOT aliases)

#### Scenario: Single match auto-complete
- **WHEN** user has typed `/rev` and presses Tab
- **AND** the registry has exactly one command/alias starting with `rev`
- **THEN** the input text is replaced with the full primary name
- **AND** the completion menu is not shown

#### Scenario: Multiple matches show menu
- **WHEN** user has typed `/p` and presses Tab
- **AND** the registry has `/plan` and `/permission` as candidates
- **THEN** the input text is NOT replaced
- **AND** a completion menu listing both `/plan` and `/permission` is displayed

#### Scenario: Hidden command not in completion list
- **WHEN** user has typed `/d` and a hidden command `/debug-trace` exists
- **THEN** the completion menu lists all non-hidden commands starting with `/d`
- **AND** `/debug-trace` does NOT appear

#### Scenario: No matches, no menu
- **WHEN** user has typed `/zzzz` and no command starts with `zzzz`
- **THEN** no menu appears
- **AND** a hint message is shown: `无匹配命令. 输入 /help 查看全部`

### Requirement: Tab completion at the empty prefix
When the input box is empty and the user presses Tab, the dispatcher MUST:
- Show ALL non-hidden command names in the completion menu
- NOT auto-complete to any single name (multiple matches)

#### Scenario: Empty input + Tab
- **WHEN** the input is empty (only `/` or nothing) and user presses Tab
- **THEN** the completion menu lists all 10 visible built-in commands
- **AND** no auto-complete fires

### Requirement: Completion respects arg state
Completion operates on the COMMAND part only. Once the user types a space
(meaning they're past the command name into args), Tab MUST:
- No-op (do not try to "complete" args)
- Optionally insert a literal Tab character depending on terminal conventions,
  but in v0.9 the simpler "ignore completion past the first space" rule applies.

#### Scenario: Input has a space after command
- **WHEN** user has typed `/review ` (trailing space, possibly more text)
- **THEN** pressing Tab does NOT change the input
- **AND** pressing Tab does NOT show a completion menu
