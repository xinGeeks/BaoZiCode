## MODIFIED Requirements

### Requirement: Slash commands are routed correctly
The system MUST recognize the slash commands `/help`, `/clear`, `/exit`, `/model`, `/tools`, `/permissions`, `/permissions mode`, `/plan`, `/do`, `/auto`, `/stop`, `/status` when they appear as the entire content of the input box, and route them to the corresponding handlers instead of sending them to the LLM. The `/permissions` command with no arguments MUST display the current policy; the `/permissions mode` command MUST open a three-option modal to switch permission mode.

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

## MODIFIED Requirements

### Requirement: Permission modal follows five-layer permission flow
The system MUST display a modal confirmation dialog before executing any tool call whose `permissions.check(call)` returns `fallthrough` at layers L1-L3 (i.e., no rule matched and mode is `default` or permissive with explicit user preference). The modal MUST show the tool name, the full arguments, the matched layer so far (e.g., "No rule matched — L5 fallback"), and four actions: allow once (Y), allow for session (A), allow permanently (P), deny (N, default Escape). The system MUST NOT execute the tool until the user explicitly allows it with one of the three allow actions.

#### Scenario: Default per-call confirmation with three allow options
- **WHEN** `permissions.mode` is `default` (or unset)
- **AND** L1/L2/L3 all return fallthrough
- **THEN** a confirmation modal appears with four buttons: Y (allow once), A (allow for session), P (allow permanently), N (deny)
- **AND** the modal displays the tool name, formatted arguments, and a hint about which mode is active

#### Scenario: Allow once permits the current call only
- **WHEN** the user presses Y (or clicks "Allow once")
- **THEN** the current ToolCall is allowed and proceeds to execution
- **AND** no rule is added to `_session_rules`
- **AND** no file is written

#### Scenario: Allow for session adds to in-memory rules
- **WHEN** the user presses A (or clicks "Allow for session")
- **THEN** a rule with a **glob pattern** (NOT the exact full command string) is appended to `_session_rules`
- **AND** the glob is derived by: taking the first whitespace-separated token of the primary argument (e.g., the command after the binary for Bash), replacing the rest with ` *` suffix
- **AND** subsequent matching calls in the same Agent session bypass the modal
- **AND** the rule is discarded when the Agent process exits
- **AND** the rule's `matched_pattern` is shown in the conversation area so the user can verify what was granted

#### Scenario: Allow permanently appends to local YAML
- **WHEN** the user presses P (or clicks "Allow permanently")
- **THEN** a rule with the same **glob pattern** (not exact command) used for the session rule is appended to `<project_root>/.baozicode/permissions.local.yaml`
- **AND** the rule is de-duplicated against existing local rules by `(tool, pattern, decision)` key
- **AND** subsequent matching calls in this and future sessions bypass the modal

#### Scenario: Deny produces is_error and continues loop
- **WHEN** the user presses N (or Escape)
- **THEN** the modal returns deny
- **AND** the ToolResult has `is_error=True` with content explaining the denial
- **AND** the result is fed back to the LLM in the next conversation turn
- **AND** the Agent loop MUST continue (not terminate)

#### Scenario: L1/L2 deny short-circuits modal
- **WHEN** L1 or L2 returns DENY
- **THEN** the modal MUST NOT be displayed (because the pipeline short-circuits before L5)
- **AND** the ToolResult is synthesized directly with the deny reason

#### Scenario: Strict mode skips modal
- **WHEN** `permissions.mode` is `strict`
- **AND** L1/L2/L3 all return fallthrough
- **THEN** L4 returns DENY
- **AND** the modal MUST NOT be displayed

## ADDED Requirements

### Requirement: Status bar shows current permission mode
The system MUST display the current permission mode (`strict` / `default` / `permissive`) in the status bar alongside the existing `mode · backend/model · auto · phase` display.

#### Scenario: Status bar reflects current mode
- **WHEN** the user switches mode via `/permissions mode`
- **THEN** the status bar updates within 1 second to show the new mode (e.g., `● full · anthropic/claude-sonnet-4-6 · ask · default · idle`)

#### Scenario: /status output includes mode and threshold
- **WHEN** the user runs `/status`
- **THEN** the conversation area displays the current mode, the `denial_warn_threshold` value, the count of consecutive denials in the current session, and the count of session rules added