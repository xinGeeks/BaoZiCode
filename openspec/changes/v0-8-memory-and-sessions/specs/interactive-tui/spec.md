# interactive-tui Specification (v0.8 deltas)

## Purpose
Modifications to v0.7 `tui/chat_screen.py` to register three new slash commands (`/resume`, `/memory`, `/new`) and extend `/status` output with memory + session stats.

## ADDED Requirements

### Requirement: /resume slash command
The system MUST register `/resume` in the TUI slash command dispatcher. Invoking `/resume` MUST display a modal listing recent sessions (sorted by `last_message_at` descending), each row showing the session ID, title, last-message timestamp, and message count. The user MUST be able to select one to resume (loads via `app.resume_session(id)`) or cancel.

#### Scenario: Multiple sessions available
- **WHEN** user types `/resume` and 5 sessions exist
- **THEN** a modal appears with 5 rows
- **AND** each row shows: ID (truncated to last 12 chars), title (first 40 chars of first user msg), "X hours ago" or "X days ago", message count
- **AND** pressing Enter on a row resumes that session
- **AND** pressing Esc cancels

#### Scenario: No sessions available
- **WHEN** user types `/resume` and the sessions directory is empty
- **THEN** the modal shows "(no sessions found)"
- **AND** Esc dismisses the modal

#### Scenario: Resume loads messages
- **WHEN** user selects session `20260708-153000-a1b2`
- **THEN** `app.resume_session(id)` is called
- **AND** the conversation manager is populated with the loaded messages
- **AND** the TUI displays "[已恢复 session 20260708-153000-a1b2, N 条消息]"
- **AND** any resume warnings (bad lines, orphan, time_gap) are shown as a single info line

### Requirement: /memory slash command
The system MUST register `/memory` in the TUI slash command dispatcher. Invoking `/memory` MUST display the current `MEMORY.md` contents from both user-global and project-local directories in a two-column or two-section layout. If a memory update is in flight, the system MUST show a "笔记更新中..." status line.

#### Scenario: Both indices have notes
- **WHEN** user types `/memory` and user-global has 5 notes + project-local has 3 notes
- **THEN** the TUI displays two sections:
  - `## 用户级记忆 (5 条)` followed by the index entries
  - `## 项目级记忆 (3 条)` followed by the index entries

#### Scenario: One index empty
- **WHEN** user-global is empty
- **THEN** only the `## 项目级记忆 (3 条)` section appears
- **AND** a "用户级暂无笔记" placeholder is shown for the empty section

#### Scenario: Update in flight
- **WHEN** a memory update `asyncio.create_task` is currently running
- **THEN** the TUI shows "[笔记更新中...]" status line above the index display

### Requirement: /new slash command
The system MUST register `/new` in the TUI slash command dispatcher. Invoking `/new` MUST display a confirmation modal: "放弃当前会话,新建一个?". On confirmation, the system MUST clear the conversation manager, generate a new session ID, and reset the archiver.

#### Scenario: Confirmation accepted
- **WHEN** user types `/new` and confirms in the modal
- **THEN** `app.conversation.clear()` is called
- **AND** `app._session_id = format_session_id(datetime.now())` (new ID)
- **AND** `app.archiver` is re-initialized for the new session
- **AND** the TUI shows "[新会话已开始,ID: <new_id>]"

#### Scenario: Confirmation cancelled
- **WHEN** user types `/new` and presses Esc
- **THEN** no state changes occur
- **AND** the modal dismisses

### Requirement: Updated /help text
The system MUST update the `/help` command output to mention the three new slash commands with one-line descriptions:
- `/resume` — list and resume a previous session
- `/memory` — view current memory notes (user + project)
- `/new` — start a new session, clearing the current one

#### Scenario: /help shows new commands
- **WHEN** user types `/help`
- **THEN** the output includes the three new command descriptions in addition to all existing v0.7 commands (e.g., `/compact`, `/permissions`, `/status`)

### Requirement: Extended /status output
The system MUST extend the existing `/status` output to include memory and session stats. The new sections MUST appear only when there is data to report (not shown when 0 notes or fresh session).

#### Scenario: Memory and session data present
- **WHEN** user types `/status` after some notes have been written and a session is active
- **THEN** the status output includes:
  ```
  notes: 7 (user: 4, project: 3)
  session_id: 20260708-153000-a1b2
  memory_updates_triggered: 2
  jsonl_size: 24.3 KB
  ```

#### Scenario: Fresh session, no notes
- **WHEN** user types `/status` immediately after start with no notes yet
- **THEN** the notes line is omitted entirely
- **AND** `memory_updates_triggered: 0` is shown
- **AND** `jsonl_size` is shown as the current JSONL file size (likely small)