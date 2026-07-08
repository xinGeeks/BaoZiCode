# command-registry Specification (v0.9)

## Purpose
A single source of truth for slash-command metadata (name, aliases, description,
usage, type, params hint, hidden flag, handler), with boot-time alias collision
detection that panics on conflict. New commands are registered by adding one
entry to a list, not by editing dispatcher code paths.

## Requirements

### Requirement: Command metadata shape
The registry MUST store every command as a `CommandDef` with these fields:

- `name: str` — primary command name, lowercase a-z + `-` only
- `aliases: list[str]` — additional names, same char set
- `description: str` — short one-line summary for `/help` and Tab completions
- `usage: str` — usage example, e.g. `"/plan"` or `"/review <since>"`
- `type: CommandType` — one of `LOCAL` / `UI_STATE` / `PROMPT`
- `params_hint: str | None` — optional placeholder, e.g. `"<mode>"`
- `hidden: bool` — if True, command is executable but excluded from
  `/help` listings and Tab completion
- `handler: CommandHandler` — async `(args: str, ctx: CommandContext) -> CommandResult`

#### Scenario: Minimal command definition
- **WHEN** a command is registered with only `name`/`description`/`usage`/`type`/`handler`
- **THEN** `aliases=[]`, `params_hint=None`, `hidden=False`
- **AND** the registry accepts the entry without error

#### Scenario: Command name validation
- **WHEN** a command is registered with a name containing uppercase letters, digits, or symbols
- **THEN** the registry rejects the entry with `ValueError`
- **AND** no entry is added to the registry

### Requirement: Boot-time alias collision detection
At boot time (call to `registry.freeze()`), the registry MUST scan every
`(name + all aliases)` across all commands and panic if any string appears twice.

#### Scenario: Two commands claim the same alias
- **WHEN** command A has `aliases=["p"]` and command B has `aliases=["p"]`
- **AND** `registry.freeze()` is called
- **THEN** the process exits with `SystemExit("alias collision: p -> .../... ")`
- **AND** the error message names BOTH commands and the exact alias string

#### Scenario: Command primary name matches another's alias
- **WHEN** command A has `name="review"` and command B has `aliases=["review"]`
- **AND** `registry.freeze()` is called
- **THEN** the process exits with `SystemExit` naming both entries

#### Scenario: All aliases unique
- **WHEN** every name + every alias across all commands is unique
- **THEN** `registry.freeze()` returns without error

### Requirement: Case-insensitive lookup
The registry MUST resolve command names by lowercasing input before lookup.
- All names stored in the registry are lowercase.
- A user typing `/PLAN` resolves to the same command as `/plan`.

#### Scenario: User types uppercase command
- **WHEN** the user types `/PLAN` (uppercase)
- **THEN** `registry.lookup("PLAN")` returns the command registered as `name="plan"`

#### Scenario: User types mixed case
- **WHEN** the user types `/Memory`
- **THEN** the lookup resolves to the command registered as `name="memory"`

### Requirement: Hidden commands
Commands with `hidden=True` MUST:
- Resolve normally when typed by name (e.g., `/debug-trace`)
- Be excluded from `/help` listing
- Be excluded from Tab completion candidate lists
- Not appear in `registry.all_visible()` output

#### Scenario: Hidden command resolves by exact name
- **WHEN** user types `/debug-trace`
- **THEN** the hidden command's handler runs
- **THEN** the registry does NOT show this command in `/help`

#### Scenario: Hidden command excluded from Tab completion
- **WHEN** the Tab key is pressed with input `/d`
- **THEN** the completion menu lists `/debug-trace` only if the command is NOT hidden
- **AND** hidden commands never appear in any completion list

### Requirement: Handler signature
Every `CommandHandler` MUST be an async callable with this signature:

```
async def handler(args: str, ctx: CommandContext) -> CommandResult
```

- `args` — everything after the first space in the user's input (may be `""`)
- `ctx` — the runtime handle (see `command-context` spec)
- Return value — a `CommandResult` subclass (see below)

Sync handlers MUST be wrapped as `async def` (the registry does NOT auto-wrap).

#### Scenario: Async handler runs to completion
- **WHEN** the dispatcher invokes an async handler
- **THEN** the dispatcher awaits the coroutine
- **AND** the dispatched user input does NOT proceed to the Agent path

#### Scenario: Handler raises an exception
- **WHEN** any handler raises an exception during execution
- **THEN** the dispatcher catches it
- **AND** the user sees a visible error in the chat log via `ctx.show_error(...)`
- **AND** the Agent path does NOT receive a corrupted message

### Requirement: CommandResult union type
The registry MUST define `CommandResult` as a tagged union of three concrete types:

- `LocalResult` — tag `"local"`, no payload (info commands like `/status`)
- `UiStateResult` — tag `"ui_state"`, no payload (state changes like `/clear`)
- `PromptResult` — tag `"prompt"`, `text: str` payload (text injection like `/review`)

The dispatcher MUST use the tag to decide what UI side-effect to apply.

#### Scenario: LocalResult dispatch
- **WHEN** a handler returns `LocalResult()`
- **THEN** the dispatcher prints nothing in the chat log (no echo)
- **AND** the dispatcher still respects any `ctx.show_info(...)` calls the handler made

#### Scenario: PromptResult dispatch
- **WHEN** a handler returns `PromptResult(text="...")`
- **THEN** the dispatcher calls `ctx.send_to_agent(text)`
- **AND** the Agent loop processes the injected text as if the user had typed it

#### Scenario: Result tag matching handler type
- **WHEN** a `LOCAL` command returns `PromptResult(...)`
- **THEN** the dispatcher logs a registry-config error
- **AND** still routes the prompt text to the Agent (defensive: tag wins over declared type)

### Requirement: Ten built-in commands registered at boot
`builtins.register_all(registry)` MUST register exactly these 10 commands:

| name        | aliases              | type        | params_hint     | hidden |
|-------------|----------------------|-------------|-----------------|--------|
| help        | (none)               | LOCAL       | (none)          | False  |
| compact     | (none)               | UI_STATE    | (none)          | False  |
| clear       | (none)               | UI_STATE    | (none)          | False  |
| plan        | (none)               | UI_STATE    | (none)          | False  |
| do          | (none)               | UI_STATE    | (none)          | False  |
| session     | (none)               | UI_STATE    | (none)          | False  |
| memory      | (none)               | LOCAL       | (none)          | False  |
| permission  | permissions          | UI_STATE    | `[<mode>]`      | False  |
| status      | (none)               | LOCAL       | (none)          | False  |
| review      | (none)               | PROMPT      | `[<since>]`     | False  |

The `permission` command has a secondary alias `permissions` matching the
v0.5–v0.6 spelling. No command in v0.9 is `hidden=True`.

#### Scenario: All ten commands resolve
- **WHEN** `builtins.register_all(registry); registry.freeze()` completes
- **THEN** each of the 10 names from the table above resolves via `registry.lookup`

#### Scenario: /permissions resolves
- **WHEN** the user types `/permissions` (legacy v0.5 spelling)
- **THEN** `registry.lookup("permissions")` returns the command registered as `name="permission"`

### Requirement: /plan and /do are strict verbs
The `/plan` and `/do` commands MUST be strict mode toggles. They MUST:
- Accept any non-empty `args` string and silently ignore it
- Switch `self.plan_mode` to `True` (`/plan`) or `False` (`/do`)
- Re-display the status bar with the new mode marker

#### Scenario: /plan with leftover args
- **WHEN** user types `/plan foo bar`
- **THEN** `plan_mode` becomes `True`
- **AND** the `foo bar` part is silently discarded
- **AND** no error is raised

#### Scenario: /plan when already in plan mode
- **WHEN** user types `/plan` while `plan_mode=True`
- **THEN** `plan_mode` remains `True`
- **AND** the status bar refreshes (no-op visible change)

### Requirement: /review prompt content
The `/review` command MUST inject a preset prompt into the Agent. The default
prompt body is exactly:

```
请审查当前会话自 {since} 以来的所有改动(patch、命令输出、对话)。
输出三段:## 摘要 / ## 风险点 / ## 建议修复。
```

Where `{since}` is substituted:
- If `args` is non-empty, use `args` as the `since` value
- If `args` is empty, use `"本次会话开始"` as the default

The user MAY override the prefix via `config.commands.review_prompt`. When
the override is set, the default prefix string is replaced, but `{since}`
substitution remains.

#### Scenario: /review with no args
- **WHEN** user types `/review`
- **THEN** the Agent receives: `请审查当前会话自 本次会话开始 以来的所有改动...`
- **AND** the appended `## 摘要 / ## 风险点 / ## 建议修复` suffix is preserved

#### Scenario: /review with since arg
- **WHEN** user types `/review 5 轮前`
- **THEN** the Agent receives: `请审查当前会话自 5 轮前 以来的所有改动...`

#### Scenario: Custom review_prompt config override
- **WHEN** `config.commands.review_prompt` is set to a non-default string
- **AND** user types `/review`
- **THEN** the injected text uses the custom prefix + the `## 摘要 / ## 风险点 / ## 建议修复` suffix
