# auto-memory Specification

## Purpose
Automatic extraction of long-term notes from Agent conversations, stored as Markdown files with YAML frontmatter under two physically isolated directories (user-global and project-local). Notes are organized in 4 categories (user-pref, correction, project, reference) and indexed by `MEMORY.md` files capped at 200 lines / 25KB. Memory updates are triggered asynchronously after natural Agent stops, with deduplication delegated to the LLM.

## Requirements

### Requirement: Four-category note taxonomy
The system MUST classify every note into exactly one of four types:
- `user-pref` — user preferences (e.g., "user dislikes emoji in responses")
- `correction` — user corrections / feedback (e.g., "user said don't use tabs")
- `project` — project-specific knowledge (e.g., "this project uses uv not poetry")
- `reference` — references to external resources (e.g., "test fixtures at tests/conftest.py")

The system MUST reject any note with a type not in this set.

#### Scenario: Note created with valid type
- **WHEN** the LLM emits `{"action": "add", "type": "user-pref", "slug": "no-emoji", ...}`
- **THEN** the note is created with `type="user-pref"`
- **AND** the frontmatter contains `type: user-pref`

#### Scenario: Note created with invalid type
- **WHEN** the LLM emits `{"action": "add", "type": "random-thought", ...}`
- **THEN** the operation is rejected
- **AND** a warning is logged `invalid note type: random-thought`
- **AND** no file is created

### Requirement: Note file format with YAML frontmatter
The system MUST store each note as a Markdown file with the following structure:

```
---
type: <user-pref|correction|project|reference>
created_at: <ISO 8601 timestamp>
source_session: <session_id>
tags: [<comma-separated values>]
access_count: 0
last_accessed: <ISO 8601 timestamp>
---

# <Title>

<Body content as Markdown>
```

#### Scenario: Valid frontmatter parsing
- **WHEN** a note file is read
- **THEN** `type`, `created_at`, `source_session`, `tags`, `access_count`, and `last_accessed` are correctly parsed from the YAML frontmatter
- **AND** the body content starts after the closing `---` line

#### Scenario: Missing frontmatter field rejected
- **WHEN** a note file is missing the required `type` field
- **THEN** reading the note fails with a clear error message
- **AND** the file is left untouched

### Requirement: Two physically isolated memory directories
The system MUST maintain two separate memory directories:
- User-global: `~/.baozicode/memory/` (configurable via `memory.user_dir`)
- Project-local: `<project_root>/.baozicode/memory/` (configurable via `memory.project_dir`)

The system MUST NOT allow writes from one scope to affect the other scope. Reading from one MUST NOT include notes from the other.

#### Scenario: User note does not appear in project index
- **WHEN** a note `no-emoji.md` is added to user-global directory
- **THEN** `project_store.read_index()` does NOT contain `no-emoji`
- **AND** `user_store.read_index()` DOES contain `no-emoji`

#### Scenario: Project note does not appear in user index
- **WHEN** a note `uses-uv.md` is added to project-local directory
- **THEN** `user_store.read_index()` does NOT contain `uses-uv`
- **AND** `project_store.read_index()` DOES contain `uses-uv`

### Requirement: MEMORY.md index file with size limits
The system MUST maintain a `MEMORY.md` file in each memory directory. The index file MUST contain one block per note:
```
## [<type>] <slug> — <title>
<one-line summary, max 80 chars>

```
The index file MUST be capped at `config.memory.index_max_lines` (default 200) lines AND `config.memory.index_max_bytes` (default 25 KB). The system MUST refuse to write an index exceeding these limits and raise `IndexOverflowError`.

#### Scenario: Index under limit, write succeeds
- **WHEN** the proposed new index has 150 lines / 18 KB
- **THEN** the rewrite succeeds
- **AND** the file is updated atomically (write to temp + rename)

#### Scenario: Index over lines limit, write refused
- **WHEN** the proposed new index has 250 lines
- **THEN** `rewrite_index()` raises `IndexOverflowError("lines 250 > max 200")`
- **AND** the existing `MEMORY.md` is left unchanged

#### Scenario: Index over bytes limit, write refused
- **WHEN** the proposed new index has 30 KB
- **THEN** `rewrite_index()` raises `IndexOverflowError("bytes 30720 > max 25600")`
- **AND** the existing `MEMORY.md` is left unchanged

### Requirement: Three-tier layered overflow handling
The system MUST handle index overflow via a four-state machine:
- `NORMAL`: lines < `warning_lines` AND bytes < `warning_bytes` → no action
- `WARN`: `warning_lines` ≤ lines < `max_lines` OR `warning_bytes` ≤ bytes < `max_bytes` → log yellow warning, return `OverflowAction.WARN`
- `AUTO_COMPRESS`: lines ≥ `max_lines` OR bytes ≥ `max_bytes` AND session_limiter < `auto_compress_per_session` → schedule `asyncio.create_task(_auto_compress(store))`, increment session_limiter, return `OverflowAction.AUTO_COMPRESS_SCHEDULED`
- `HUMAN_NEEDED`: triggered when AUTO_COMPRESS has been used `auto_compress_per_session` times this session OR when `_auto_compress` itself fails → log red error, return `OverflowAction.HUMAN_NEEDED`

#### Scenario: NORMAL state, no action
- **WHEN** index has 150 lines / 18 KB
- **THEN** `check_and_act()` returns `OverflowAction.NOOP`
- **AND** no log output

#### Scenario: WARN state, yellow warning logged
- **WHEN** index has 185 lines / 23 KB
- **THEN** `check_and_act()` returns `OverflowAction.WARN`
- **AND** a warning is logged to stderr: `WARN: MEMORY.md 接近上限 (185/200 lines, 23/25 KB),考虑执行 /memory compress`

#### Scenario: AUTO_COMPRESS triggered, task scheduled
- **WHEN** index has 210 lines / 26 KB AND session_limiter = 0
- **THEN** `check_and_act()` returns `OverflowAction.AUTO_COMPRESS_SCHEDULED`
- **AND** session_limiter becomes 1
- **AND** an `asyncio.create_task(_auto_compress(store))` is fired
- **AND** `_auto_compress` runs the LLM-based merge in the background

#### Scenario: AUTO_COMPRESS second time triggers HUMAN_NEEDED
- **WHEN** index is over limit AND session_limiter already equals `auto_compress_per_session` (1)
- **THEN** `check_and_act()` returns `OverflowAction.HUMAN_NEEDED`
- **AND** no LLM call is made
- **AND** a red error is logged: `ERROR: 自动压缩已达本会话上限,请手动执行 /memory compress 或 /memory prune`

#### Scenario: _auto_compress successful returns to NORMAL
- **WHEN** _auto_compress merges duplicate notes and shrinks index to 150 lines / 18 KB
- **THEN** the next `check_and_act()` call returns `OverflowAction.NOOP`
- **AND** the index file is updated atomically

### Requirement: Memory update triggered on natural stop
The system MUST trigger an asynchronous memory update when the Agent loop terminates with `StopReason.COMPLETED` or `StopReason.MAX_ITERATIONS_REACHED`. The system MUST NOT trigger on `StopReason.USER_CANCELLED`, `StopReason.STREAM_ERROR`, `StopReason.COMPACTION_FAILED`, or other abnormal terminations.

#### Scenario: COMPLETED triggers update
- **WHEN** the Agent loop yields `done(reason=COMPLETED)`
- **THEN** `asyncio.create_task(memory_updater.update(snapshot))` is fired
- **AND** the snapshot is a frozen copy of the conversation messages

#### Scenario: USER_CANCELLED does NOT trigger update
- **WHEN** the user presses Esc / Ctrl+C mid-iteration
- **AND** the Agent loop terminates with `done(reason=USER_CANCELLED)`
- **THEN** no memory update is fired
- **AND** the conversation JSONL still receives the partial assistant message via the SessionArchiver

#### Scenario: MAX_ITERATIONS_REACHED triggers update
- **WHEN** the Agent loop runs out of iterations without natural completion
- **THEN** `done(reason=MAX_ITERATIONS_REACHED)` is yielded
- **AND** a memory update is fired (the conversation likely contains valuable context)

### Requirement: Snapshot-based concurrency
The system MUST capture a frozen copy of the conversation messages at the moment of trigger, then process the snapshot asynchronously in the background. The user MAY submit additional turns while the memory update is in flight. When the update completes, the system MUST verify the current session ID matches the snapshot's session ID; if not, the update is aborted silently.

#### Scenario: Snapshot frozen at trigger time
- **WHEN** the Agent yields `done(reason=COMPLETED)` with messages [m1, m2, m3]
- **THEN** the snapshot passed to `update(snapshot)` is a list copy of [m1, m2, m3]
- **AND** the user typing a new turn adds m4 to the conversation manager
- **AND** the background update continues working with [m1, m2, m3] (not affected by m4)

#### Scenario: Session changed during update, abort
- **WHEN** the user calls `/new` while a memory update is in flight
- **AND** the update completes after the new session_id has been generated
- **THEN** `current_session_id()` returns the NEW session ID
- **AND** the update aborts before writing to disk
- **AND** an info log line `session changed during update, abort` is written

### Requirement: LLM extraction prompt with fenced JSON output
The system MUST call the LLM with `tools=[]` (no tools available) and a fixed prompt template. The prompt MUST instruct the LLM to output JSON inside a fenced ` ```json ... ``` ` block. The system MUST parse the response using regex `r"```json\s*\n(.*?)\n```"` with `re.DOTALL`. The system MUST reject any response that does not contain a parseable fenced JSON block.

#### Scenario: Well-formed fenced JSON parsed
- **WHEN** the LLM returns text containing a fenced JSON block with valid `operations` array
- **THEN** the parser returns the parsed dict
- **AND** the operations are applied to the store

#### Scenario: Missing fenced block
- **WHEN** the LLM returns text without any fenced JSON block
- **THEN** the parser returns `None`
- **AND** `parse_failures` counter increments
- **AND** the update is recorded as a failure (no disk writes)

#### Scenario: Invalid JSON in fenced block
- **WHEN** the fenced block contains malformed JSON
- **THEN** `json.loads()` raises an exception
- **AND** the parser catches the exception and returns `None`
- **AND** `parse_failures` counter increments

### Requirement: LLM input scope — last N turns + complete memory index
The system MUST send the LLM only the last N turns (default N=5, configurable via `memory.recent_turns_for_update`) of the conversation, plus the complete `MEMORY.md` index from BOTH user and project directories. The system MUST NOT send the full conversation history or the full text of existing notes.

#### Scenario: Default N=5 turns
- **WHEN** the conversation has 30 messages and `recent_turns_for_update=5`
- **THEN** the prompt input contains the last 10 messages (5 turns = ~10 messages assuming text-only)
- **AND** the prompt also contains the full text of `~/.baozicode/memory/MEMORY.md` and `<project>/.baozicode/memory/MEMORY.md`

#### Scenario: Configurable N
- **WHEN** `recent_turns_for_update=10` is set in YAML
- **THEN** the last 20 messages are included in the prompt
- **AND** the memory index text is unchanged

### Requirement: LLM-driven deduplication
The system MUST delegate deduplication to the LLM. The system MUST provide the current `MEMORY.md` index as context so the LLM can identify which notes already exist. The LLM MUST emit `update` operations for changes to existing notes and `add` operations for new notes. The system MUST NOT apply any automatic string-matching dedup before calling the LLM.

#### Scenario: LLM identifies existing note and updates
- **WHEN** `MEMORY.md` already contains a note `## [user-pref] no-emoji — User dislikes emoji`
- **AND** the conversation reveals a refinement ("specifically dislike them in tool output")
- **THEN** the LLM emits `{"action": "update", "slug": "no-emoji", "content": "...refined..."}`
- **AND** the existing note's body is appended (not overwritten)

#### Scenario: LLM creates a new note with similar slug
- **WHEN** `MEMORY.md` has no existing note for `python-style`
- **AND** the conversation reveals a new user preference
- **THEN** the LLM emits `{"action": "add", "slug": "python-style", ...}`
- **AND** the new note file is created

### Requirement: Operations include delete with cross-session protection
The system MUST support `delete` operations in the LLM output. The system MUST reject any `delete` operation whose target note's `source_session` does not match the current session ID, to prevent cross-session data loss from LLM mistakes.

#### Scenario: Delete own-session note
- **WHEN** the LLM emits `{"action": "delete", "slug": "obsolete-note"}`
- **AND** the note's `source_session` matches the current session
- **THEN** the note file is removed

#### Scenario: Reject delete of cross-session note
- **WHEN** the LLM emits `{"action": "delete", "slug": "old-note"}`
- **AND** the note's `source_session` is a DIFFERENT session ID
- **THEN** the delete is rejected
- **AND** a warning is logged `cannot delete cross-session note: old-note`
- **AND** the note file remains

### Requirement: Index injection into system prompt
The system MUST inject the contents of both `MEMORY.md` files (user-global and project-local) into the system prompt as TWO separate sections: `## 长期记忆 (用户级)` and `## 长期记忆 (项目级)`. The system MUST inject these via the v0.4 modular prompt builder's optional `memory` section.

#### Scenario: Both indices present
- **WHEN** user-global `MEMORY.md` has 5 entries and project-local has 3 entries
- **THEN** the system prompt contains both `## 长期记忆 (用户级)` and `## 长期记忆 (项目级)` sections
- **AND** user-global appears first (as foundational rules)
- **AND** project-local appears second (as project-specific overrides)

#### Scenario: One index empty, one present
- **WHEN** user-global `MEMORY.md` is empty (no notes yet) and project-local has 3 entries
- **THEN** only `## 长期记忆 (项目级)` section appears in the system prompt
- **AND** no empty `## 长期记忆 (用户级)` placeholder is rendered

#### Scenario: Both indices empty
- **WHEN** neither directory has any notes
- **THEN** no memory section is rendered
- **AND** the prompt is identical to v0.7 behavior

### Requirement: Configurable enable/disable
The system MUST honor `config.memory.enabled: bool` (default True). When False, no `MemoryStore` is constructed, no memory update is triggered, and the system prompt contains no memory sections. The Agent behaves identically to v0.7 in this case.

#### Scenario: Disabled via config
- **WHEN** `config.memory.enabled = False`
- **THEN** no memory directories are created
- **AND** `Agent.run()` does NOT fire any `asyncio.create_task(memory_updater.update(...))`
- **AND** the system prompt's memory section is omitted entirely