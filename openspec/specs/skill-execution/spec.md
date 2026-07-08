# skill-execution Specification (v1.0)

## Purpose
Implements the two execution modes declared in the Skill's frontmatter:
`shared` (append the Skill body to the current conversation) and
`independent` (spawn a sub-Agent in a fresh ConversationManager, then
return a summary). The mode is fixed at Skill load time and cannot be
changed without `/skill reload`.

## Requirements

### Requirement: shared mode — single conversation
In `mode=shared`, when the user invokes `/<name>` (or the LLM invokes
`run_skill(name)`), the placeholder-substituted Skill body is appended
as a user-role message to the main `ConversationManager` via
`ctx.send_to_agent`. The Agent's next iteration processes the Skill body
as if the user had typed it.

#### Scenario: shared mode appends to main conversation
- **WHEN** Skill `commit` is active with `mode=shared`
- **AND** user types `/commit`
- **THEN** the body (post-placeholder) is sent via `ctx.send_to_agent(body)`
- **AND** the body becomes a user-role message in the main conversation
- **AND** the Agent's response is appended normally
- **AND** the conversation now has +1 user turn + 1 assistant turn

#### Scenario: shared mode with args
- **WHEN** Skill `review` is active with `mode=shared` and body contains
  `{since}` and `{focus_area}`
- **AND** user types `/review --since="5 轮前" --focus_area="权限层"`
- **THEN** the body is rendered with `{since}=5 轮前` and `{focus_area}=权限层`
- **AND** the rendered body is sent to the Agent

### Requirement: independent mode — sub-Agent isolation
In `mode=independent`, when the user invokes `/<name>`, the loader:

1. **Snapshot**: Take the last `history_bubbles` user/assistant turns from
   the main `ConversationManager`. Each turn is a `Message` (user-role
   or assistant-role).
2. **Sub-conversation**: Create a fresh `ConversationManager`.
3. **Sub-Agent**: Create a new `Agent` instance with the same backend,
   same `SkillRegistry`, same `SkillActivation`, same `config`. The
   sub-Agent's `available_tools` is the intersection of all active Skills'
   `allowed_tools` (or full set if any Skill has `allowed_tools=None`).
4. **Inject history**: The snapshot turns are inserted as user-role
   messages at the start of the sub-conversation (before the Skill body).
   The Skill body is the first user-role message of the sub-conversation.
5. **Run sub-Agent**: The sub-Agent runs to completion
   (`COMPLETED` or `MAX_ITERATIONS_REACHED`).
6. **Summary**: Generate a 3-section summary via LLM call
   (default model: `config.skills.summary_model`, fallback to sonnet):
   - `## 任务执行`: what the sub-Agent did (which tools, what files, etc.)
   - `## 关键发现`: notable results (failures, surprises, blockers)
   - `## 后续建议`: suggested next steps for the main Agent / user
7. **Return summary**: The summary is appended to the main conversation
   as a user-role message (`ctx.send_to_agent(summary)`).
8. **Cleanup**: The sub-ConversationManager and sub-Agent are discarded.

#### Scenario: independent mode, no history
- **WHEN** Skill `test` is active with `mode=independent`, `history_bubbles=0`
- **AND** user types `/test`
- **THEN** the sub-conversation is empty initially
- **AND** the Skill body is the first user message
- **AND** the sub-Agent runs to completion
- **AND** the summary is returned to the main conversation

#### Scenario: independent mode with 5 history bubbles
- **WHEN** Skill `test` is active with `mode=independent`, `history_bubbles=5`
- **AND** the main conversation has 12 turns
- **THEN** the last 5 turns (turns 8-12) are copied to the sub-conversation
- **AND** the Skill body is added as the next user message
- **AND** the sub-Agent sees the recent context

#### Scenario: Sub-Agent summary via dedicated LLM call
- **WHEN** the sub-Agent completes (e.g., 4 tool calls, 1 final text)
- **THEN** a separate LLM call is made with the sub-conversation as input
  and the prompt: `请用三段(## 任务执行 / ## 关键发现 / ## 后续建议)总结上述执行结果`
- **AND** the LLM's response is the summary
- **AND** the summary is short (≤ 500 tokens)

#### Scenario: Sub-Agent aborts with MAX_ITERATIONS
- **WHEN** the sub-Agent stops with `StopReason.MAX_ITERATIONS_REACHED`
- **THEN** the summary's `## 关键发现` includes a note:
  "sub-Agent did not complete (max iterations reached)"
- **AND** the main conversation receives a warning, not an error

#### Scenario: Sub-Agent aborts with USER_CANCELLED
- **WHEN** the user hits `Ctrl+C` while a sub-Agent is running
- **THEN** the sub-Agent stops with `StopReason.USER_CANCELLED`
- **AND** the main conversation receives a "cancelled" notice
- **AND** no summary is generated (since there's no final result)

### Requirement: Sub-Agent uses different model
When the Skill's frontmatter specifies `model: claude-haiku-4-5` (or any
model override), the sub-Agent MUST be created with that model instead
of the main Agent's model. This is useful for cheap Skills like `commit`
(summary) or `test` (full test run with haiku).

#### Scenario: Sub-Agent uses Skill's model override
- **WHEN** Skill `test` has `model=claude-haiku-4-5`
- **AND** main Agent uses `claude-sonnet-4-6`
- **AND** user types `/test`
- **THEN** the sub-Agent is created with `model=claude-haiku-4-5`
- **AND** the sub-Agent's LLM calls use haiku (cheaper, faster)

#### Scenario: No model override
- **WHEN** Skill has no `model` field
- **THEN** the sub-Agent inherits the main Agent's model

### Requirement: history-bubbles cap
`history_bubbles` MUST be in range `[0, 50]`. A value > 50 is clamped to
50. A negative value is treated as 0. This cap exists to prevent the
sub-conversation from blowing up.

#### Scenario: history_bubbles=100 clamped
- **WHEN** Skill frontmatter has `history-bubbles: 100`
- **THEN** the activation stores 50 (clamped)
- **AND** `/status` shows `history_bubbles: 50 (clamped from 100)`

### Requirement: Sub-Agent cannot load other Skills
For v1.0, sub-Agents MUST NOT call `load_skill` tool (it would be
recursive and confusing). The `load_skill` tool description in the
sub-Agent's `augmented_tools` MUST be omitted, or the tool description
MUST say "not available in sub-Agent context". The sub-Agent's
`ToolRegistry` is a frozen snapshot of the main Agent's tools minus
`load_skill`.

#### Scenario: Sub-Agent has no load_skill tool
- **WHEN** a sub-Agent is created (for any Skill)
- **THEN** `load_skill` is NOT in the sub-Agent's `augmented_tools`
- **AND** the sub-Agent's prompt does not mention `load_skill`

#### Scenario: Sub-Agent tries to call load_skill
- **WHEN** the sub-Agent (somehow) attempts `load_skill("foo")`
- **THEN** the tool call is treated as `unknown_tool`
- **AND** v0.5's `unknown_tool` guard returns `is_error=True`
