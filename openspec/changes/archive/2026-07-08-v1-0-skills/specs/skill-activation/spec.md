# skill-activation Specification (v1.0)

## Purpose
Holds the runtime state of which Skills are currently active in the
session. Renders the active Skills' bodies into a "dynamic section" that
the Agent sees at every iteration (every LLM call). Multiple Skills can
be active simultaneously. The activation state is cleared on `/clear`
or session end.

## Requirements

### Requirement: SkillActivation state
A `SkillActivation` instance MUST be owned by `BaoZiCodeApp` (not by
`ChatScreen` or `ConversationManager`, since multiple Screens could share
it in the future) and MUST expose:

- `active: dict[str, ActiveSkill]` — current active Skills, keyed by name
- `activate(name, body, mode, allowed_tools, history_bubbles, model)` —
  add or update; idempotent for same body
- `deactivate(name)` — remove and unregister the slash command
- `clear()` — remove all (called on `/clear`)
- `render_active_section() -> str` — return the Markdown section to inject
  into the system-reminder
- `is_active(name) -> bool`

#### Scenario: Idempotent activation
- **WHEN** `activate("review", body="...", ...)` is called twice with the
  same body
- **THEN** the second call is a no-op (no error, no duplicate entry)

#### Scenario: Activation with updated body
- **WHEN** `activate("review", body="v1", ...)` then
  `activate("review", body="v2", ...)` (e.g., from /skill reload)
- **THEN** the entry is updated in place; `body` is now `"v2"`

#### Scenario: Deactivate
- **WHEN** `deactivate("review")` is called and `review` is active
- **THEN** the entry is removed
- **AND** the registered slash command `/review` is unregistered

#### Scenario: Deactivate inactive Skill
- **WHEN** `deactivate("review")` is called and `review` is NOT active
- **THEN** the call is a no-op (no error)

### Requirement: Dynamic section rendering
`render_active_section()` MUST return a Markdown string of the form:

```
<system-reminder type="active_skills" sticky="true" ttl="session">

## 当前激活的 Skill

### review
(模式: shared, 白名单: [Read, Grep, Glob])

请审查自 5 轮前 以来的所有改动,关注 权限层。

### test
(模式: independent, 白名单: [Bash])

跑测试套件,报告失败的 case。

</system-reminder>
```

When NO Skills are active, `render_active_section()` MUST return an empty
string (so the reminder is omitted from the LLM message list).

#### Scenario: Multiple active Skills rendered in order
- **WHEN** 3 Skills are active in activation order: `commit`, `review`, `test`
- **THEN** `render_active_section()` returns a section with 3 `###` subsections
  in the same order
- **AND** the outer `<system-reminder type="active_skills">` wraps all 3

#### Scenario: No active Skills returns empty
- **WHEN** `active` is empty
- **THEN** `render_active_section()` returns `""`
- **AND** the chat_screen `_inject_reminders` does NOT add a section

#### Scenario: Section includes metadata
- **WHEN** Skill `review` is active with `mode=shared, allowed_tools=[Read, Grep]`
- **THEN** the section shows `(模式: shared, 白名单: [Read, Grep])`
- **AND** if `allowed_tools=None`, shows `(模式: shared, 白名单: 无限制)`

### Requirement: Dynamic section injection per Agent iteration
The chat_screen MUST call `render_active_section()` BEFORE every
`Agent.run()` call. The returned string is wrapped in `<system-reminder>`
and spliced into `messages[-1]` (the user's last message), via the
existing `_inject_reminders(messages, iteration)` mechanism.

#### Scenario: First iteration after activation
- **WHEN** Skill `review` is loaded at iter 1 (mid-conversation)
- **THEN** at iter 2, `messages[-1]` is preceded by an
  `<system-reminder type="active_skills">` block
- **AND** the LLM response can refer to the active Skill body

#### Scenario: Skill deactivated mid-conversation
- **WHEN** `review` is deactivated at iter 5
- **THEN** at iter 6, NO `active_skills` block is added (assuming no other
  Skills are active)
- **AND** the LLM no longer sees the Skill body

#### Scenario: Sticky across compaction
- **WHEN** v0.7 Layer 2 compaction runs and the conversation is summarized
- **THEN** the active Skills' bodies are re-injected at the top of the
  post-compaction messages (sticky=true)
- **AND** the `post_compaction_reminder` (from v0.7) is preserved

### Requirement: Multiple Skills can be active simultaneously
The activation dict supports any number of Skills. There is no v1.0
collision/conflict detection between Skills (assumption: Skills are
designed to be non-overlapping). The `name` is the unique key.

#### Scenario: Three Skills active
- **WHEN** user loads `commit`, `review`, `test` in sequence
- **THEN** all 3 are in `active`
- **AND** the dynamic section shows all 3 bodies
- **AND** the tool whitelist is the INTERSECTION of all 3 allowed-tools
  lists (or all tools if any Skill has `allowed_tools=None`)

### Requirement: /clear clears activation
The `/clear` slash command MUST also call `SkillActivation.clear()`.
After `/clear`, no Skills are active, and the registered `/<name>` slash
commands for each formerly-active Skill are unregistered.

#### Scenario: /clear with 2 active Skills
- **WHEN** user has `commit` and `review` active
- **AND** user types `/clear`
- **THEN** `active` becomes `{}`
- **AND** `CommandRegistry.lookup("/commit")` returns None
- **AND** `CommandRegistry.lookup("/review")` returns None
- **AND** the conversation history is cleared (existing /clear behavior)

#### Scenario: /clear with no active Skills
- **WHEN** no Skills are active
- **AND** user types `/clear`
- **THEN** `clear()` is a no-op (no error)
- **AND** the conversation is cleared normally

### Requirement: Per-session scope
The active Skills are tied to the current session. They MUST NOT be
persisted in v0.8 JSONL session archive. On session restart (or new
session), the user MUST explicitly `/skill <name>` each Skill they want.

#### Scenario: Session restart loses active Skills
- **WHEN** user has `review` active and exits the App
- **THEN** the next launch starts with empty `active` (no auto-restore)
- **AND** `/skill list --active` shows "(no active skills)"

#### Scenario: --resume restores conversation, not Skills
- **WHEN** user resumes a previous session
- **THEN** the conversation history is loaded (v0.8 behavior)
- **AND** the active Skills are NOT restored (v1.0 scope)
