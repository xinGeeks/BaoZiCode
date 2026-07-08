# session-archive Specification

## Purpose
JSONL append-only persistence of Agent conversation messages, with the ability to list / resume / cleanup sessions. Each session is one JSONL file under `<project_root>/.baozicode/sessions/`. The session ID format is `YYYYMMDD-HHMMSS-xxxx` (timestamp + random suffix).

## Requirements

### Requirement: Session ID format
The system MUST generate session IDs in the format `YYYYMMDD-HHMMSS-xxxx`, where `xxxx` is 4 hex chars from `secrets.token_hex(2)`. Total length is 17 characters (15 timestamp + 1 dash + 4 random). The ID MUST be unique enough to avoid collisions across concurrent CLI launches in the same second.

#### Scenario: ID generated on startup
- **WHEN** the App boots at `2026-07-08T15:30:00` local time
- **THEN** the session ID is `20260708-153000-a1b2` (timestamp portion deterministic + 4 random hex chars)
- **AND** the JSONL file is named `<session_id>.jsonl`

#### Scenario: ID collision triggers re-roll
- **WHEN** the generated ID matches an existing JSONL file (rare random collision)
- **THEN** the system generates a new random suffix and retries
- **AND** this loop continues until an unused ID is found
- **AND** no file is overwritten

### Requirement: Append-only JSONL write with fsync
The system MUST append one JSON line per `Message` added to the `ConversationManager`. Each append MUST flush the file buffer and call `os.fsync()` to ensure crash-safety. The system MUST NOT maintain any separate metadata file — all metadata (title, message count, etc.) is derived by scanning JSONL contents.

#### Scenario: Append writes one JSON line
- **WHEN** the conversation manager receives `add_user("hello")`
- **THEN** one line is written to `<project_root>/.baozicode/sessions/<session_id>.jsonl`
- **AND** the line is valid JSON with the message's role, content blocks, and timestamp

#### Scenario: Multiple appends in same process
- **WHEN** a session runs through 30 add_* operations
- **THEN** the JSONL file contains exactly 30 non-empty lines
- **AND** each line is independently parseable as JSON

#### Scenario: Crash safety via fsync
- **WHEN** the process is killed between two appends (mid-write)
- **THEN** at most the LAST line in the JSONL file is incomplete or missing
- **AND** all prior lines remain valid JSON
- **AND** on resume, the broken line is detected and skipped

### Requirement: Bad line skipped on resume
The system MUST parse JSONL files line-by-line on resume. Any line that fails `json.loads()` MUST be silently skipped with a warning logged to stderr. Parsing MUST continue with subsequent lines.

#### Scenario: One corrupted line in middle
- **WHEN** the JSONL file contains N lines, and line K is malformed JSON
- **THEN** lines 1..K-1 are loaded as valid messages
- **AND** line K is skipped with a warning `skip bad line {K}: <error>`
- **AND** lines K+1..N are loaded as valid messages
- **AND** no exception is raised

#### Scenario: Trailing partial line after crash
- **WHEN** the JSONL file's last line is `{"role": "user", "content": "hel` (incomplete)
- **THEN** the line fails JSON parsing
- **AND** is skipped with a warning
- **AND** the resume continues with the prior complete lines

### Requirement: Orphan tool_call truncation
The system MUST detect tool result entries whose `tool_call_id` does not match any `ToolUseBlock.id` in earlier entries. When an orphan is detected, the system MUST truncate the message list at that point (drop the orphan entry and all subsequent entries) and append a warning.

#### Scenario: Tool call without matching result
- **WHEN** the JSONL contains `[user1, assistant_with_tool_use(id=X), tool_result(id=Y), ...]`
- **AND** there is no `ToolUseBlock` with id `Y`
- **THEN** truncation occurs at the `tool_result(id=Y)` line
- **AND** all subsequent lines are dropped
- **AND** a warning is logged `orphan tool_result id=Y dropped, truncated {N} lines`

#### Scenario: All tool results have matching tool uses
- **WHEN** every `ToolResultBlock.tool_call_id` matches a prior `ToolUseBlock.id`
- **THEN** no truncation occurs
- **AND** the full message list is returned

### Requirement: Token-budget-driven compaction on resume
The system MUST, after loading and orphan-truncating the message list, estimate the total token count. If the estimate exceeds `context_window - 8_000` tokens (manual reserve for resume), the system MUST invoke `maybe_compact(messages, trigger="resume", ctx=compact_ctx)` and use the compacted message list.

#### Scenario: Small session, no compaction on resume
- **WHEN** a 10-message session with total ~3K tokens is resumed
- **THEN** no compaction is triggered
- **AND** the original messages are returned unchanged

#### Scenario: Large session triggers Layer 1 + possibly Layer 2
- **WHEN** a 50-message session with total ~120K tokens is resumed (budget 128K - 8K reserve)
- **THEN** Layer 1 offload runs (oversized blocks trimmed)
- **AND** Layer 2 summary may run if still over budget
- **AND** the compacted messages are returned

### Requirement: Time-gap reminder injection
The system MUST compute the gap between the current wall-clock time and the first user message's timestamp in the resumed session. If the gap exceeds `agent.time_gap_threshold_hours` (default 8 hours), the system MUST insert a `<system-reminder type="time_gap" ttl="once">` user-role message at the `messages[-2]` position (before the last user message).

#### Scenario: Gap under threshold, no reminder
- **WHEN** the first user message is 4 hours old and threshold is 8 hours
- **THEN** no time-gap reminder is inserted
- **AND** resume proceeds normally

#### Scenario: Gap over threshold, reminder inserted
- **WHEN** the first user message is 12 hours old and threshold is 8 hours
- **THEN** a `<system-reminder type="time_gap" ttl="once">` message is inserted at `messages[-2]`
- **AND** the reminder body lists the last 5 user message titles for context
- **AND** the reminder is removed after the first LLM response (ttl="once")

### Requirement: Session listing from JSONL
The system MUST list sessions by scanning `<project_root>/.baozicode/sessions/*.jsonl`. For each file, the system MUST extract metadata by:
- `id` = filename without `.jsonl` extension
- `title` = first user message's text content, truncated to 60 chars
- `created_at` = file's `st_mtime` (or `_meta.json` created_at if present)
- `last_message_at` = file's `st_mtime`
- `message_count` = line count of the file
- `size_bytes` = file size

Sessions MUST be sorted by `last_message_at` descending (most recent first).

#### Scenario: Five sessions in directory
- **WHEN** the sessions directory contains 5 JSONL files with various mtimes
- **THEN** `list_sessions()` returns a list of 5 `SessionMeta`
- **AND** the list is sorted by mtime descending
- **AND** each entry has title from first user message

#### Scenario: Empty directory
- **WHEN** the sessions directory is empty
- **THEN** `list_sessions()` returns an empty list
- **AND** no error is raised

### Requirement: 30-day retention cleanup at startup
The system MUST, at App startup, scan `.baozicode/sessions/*.jsonl` and remove files whose `st_mtime` is more than `config.sessions.retention_days` (default 30) old. The system MUST also remove the corresponding `<project_root>/.baozicode/context/<session_id>/` directory if it exists. The system MUST NOT remove today's session (defensive guard).

#### Scenario: Expired session removed
- **WHEN** a session's JSONL file has mtime 31 days old
- **THEN** at startup, that JSONL file is removed
- **AND** its corresponding `.baozicode/context/<sid>/` directory is also removed if it exists

#### Scenario: Today's session preserved
- **WHEN** a session's JSONL file has mtime from today (even 1 hour old)
- **THEN** the file is preserved
- **AND** no cleanup is performed

#### Scenario: Other sessions' context dirs preserved
- **WHEN** session `20260708-153000-a1b2` is being removed
- **AND** session `20260708-180000-c3d4` has its own context dir
- **THEN** only `20260708-153000-a1b2`'s JSONL and context dir are removed
- **AND** `20260708-180000-c3d4`'s files remain untouched

### Requirement: Gitignore for sessions directory
The system MUST ensure `.baozicode/sessions/` is added to `<project_root>/.gitignore` (idempotently — running the check multiple times does not duplicate the line). The system MUST NOT add `.baozicode/memory/` to `.gitignore` (user-global memory may be intentionally committed by user choice).

#### Scenario: Gitignore missing the line
- **WHEN** `.gitignore` does not yet contain `.baozicode/sessions/`
- **THEN** the line is appended
- **AND** subsequent runs do not duplicate the line

### Requirement: Configurable enable/disable
The system MUST honor `config.sessions.enabled: bool` (default True). When False, no JSONL files are created, no resume is supported, and the sessions directory is not scanned. The `cleanup_expired()` call is also skipped.

#### Scenario: Disabled via config
- **WHEN** `config.sessions.enabled = False` is set
- **THEN** no `SessionArchiver` is constructed
- **AND** `ConversationManager.add_*` operations do NOT write to any file
- **AND** `/resume` is not registered as a slash command