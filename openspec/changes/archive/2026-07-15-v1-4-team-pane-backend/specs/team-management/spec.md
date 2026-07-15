# Team Management — Pane Backend Delta

This delta adds 7 new requirements to `team-management`: BackendHandle
Protocol, 5 BackendType concrete implementations (pane-tmux /
pane-iterm2 / pane-windows-terminal / coroutine / worktree-coroutine),
BackendManager (env detect + spawn / kill / is_alive + pane session
persistence), `baozicode member run` CLI subcommand, MemberAgent
(role='member'), MemberMainLoop (long-lived polling), and
pane_info.json schema for cross-Lead-restart pane persistence.

All build on top of `v1-4-team-foundation` and `v1-4-team-tools`
requirements.

## ADDED Requirements

### Requirement: BackendHandle Protocol

The `baozicode.teams.pane.BackendHandle` Protocol MUST be the common
interface for all 5 BackendType concrete backend classes:

```python
class BackendHandle(Protocol):
    member_name: str
    team_name: str
    backend_type: BackendType
    pid: int | None  # coroutine backend = None

    def is_alive(self) -> bool: ...
    def kill(self, *, grace_seconds: float = 5.0) -> None: ...
    def title(self, new_title: str) -> None: ...
```

`backend_type` MUST be one of the 5 `BackendType` literals declared in
`v1-4-team-foundation::schema.py`. Implementations MUST NOT mutate
backend_type after construction; the field is the source of truth for
Lead-side bookkeeping (`pane_info.json` restore path reads it).

`pid` MUST return `None` for coroutine-style backends
(`CoroutineBackend`, `WorktreeCoroutineBackend`) because no OS-level
process exists. For pane-style backends (`pane-tmux` / `pane-iterm2` /
`pane-windows-terminal`) `pid` MUST be the child process PID retrieved
from `subprocess.Popen.pid` at spawn time.

#### Scenario: BackendHandle Protocol is structural
- **WHEN** a class implements `member_name` / `team_name` /
  `backend_type` (properties) + `pid` (property) + `is_alive` /
  `kill` / `title` (methods) with matching signatures
- **THEN** `isinstance(instance, BackendHandle)` is True (`@runtime_checkable`)
- **AND** it can be stored as a BackendManager slot value

#### Scenario: Coroutine backend returns None for pid
- **WHEN** a `CoroutineBackend` instance is constructed and `spawn()`
  has been called
- **THEN** `handle.pid is None`
- **AND** `handle.is_alive()` returns `not self._task.done()`
- **AND** `handle.kill()` calls `self._task.cancel()` without OS signal

#### Scenario: Pane tmux backend returns child PID
- **WHEN** `PaneTmuxBackend.spawn()` ran `tmux new-window -d <command>`
  and read back the pane's pid via `tmux list-panes -F '#{pane_pid}'`
- **THEN** `handle.pid` equals the integer pane child PID

### Requirement: 5 BackendType concrete implementations

The 5 backends in `BackendType` MUST each be implemented in
`baozicode.teams.pane`:

#### PaneTmuxBackend
- `available()` MUST return `True` iff `subprocess.run(["tmux", "-V"],
  capture_output=True, text=True, timeout=2)` exits 0 and stdout begins
  with `"tmux "`. False on `FileNotFoundError` or `TimeoutExpired`.
- `spawn()` MUST: (a) `tmux has-session -t <session>` and
  `new-session -d -s <session> -n <placeholder> "exit"` if absent;
  (b) `new-window -t <session> -n <member> -d "<command>"`;
  (c) `list-panes -F '#{pane_id} #{pane_pid}'` to capture pane id + pid;
  (d) `select-window -t <session>:<member>` to give the new pane focus.
- `kill()` MUST send `SIGTERM` to the pane child, then wait
  `grace_seconds`, then escalate to `SIGKILL` if alive. Then
  `tmux kill-window -t <session>:<member>`.
- `title(new_title)` MUST call
  `tmux select-pane -t <session>:<win>.<pane> -T "<new_title>"`.

#### PaneITerm2Backend
- `available()` MUST probe
  `osascript -e 'tell application "iTerm2" to return id of (first
  window)'` — exit 0 and stdout parseable as int → True.
- `spawn()` MUST run an `osascript` that calls `create window with
  default profile command "<command>"` then sets the session name.
- `kill()` MUST send `osascript` `close session id <window_id>`. SIGTERM
  semantics don't apply (iTerm2 windows are children of iTerm2.app, not
  Python) — close-equivalent IS the graceful kill.
- `title(new_title)` MUST run `osascript` calling
  `set name of current session of window id <window_id> to "<title>"`.

#### PaneWindowsTerminalBackend
- `available()` MUST probe `where wt.exe` — exit 0 and stdout contains
  `"wt.exe"` path → True.
- `spawn()` MUST call
  `wt.exe -w 0 new-tab --title "<session>/<member>" <command>`,
  capturing the spawned tab UUID via `--saveCommand` flag or
  `wt.exe ls` look-up by title.
- `kill()` MUST call
  `wt.exe close-tab --tab <uuid>`. Grace seconds are advisory
  (Windows Terminal tab close is immediate).
- `title(new_title)` MUST call
  `wt.exe session --title "<title>" <uuid>` (when supported; else
  close + reopen with new title fallback).

#### CoroutineBackend
- `available()` MUST always return `True` (in-process, no external
  dependency).
- `spawn()` MUST `asyncio.create_task(self._loop.run(), name=<task-name>)`
  on the currently-running Lead asyncio event loop.
- `kill()` MUST `self._task.cancel()` and await with
  `asyncio.shield` for `grace_seconds`; the cancel is instantaneous
  for coroutines.
- `title()` is a no-op.

#### WorktreeCoroutineBackend (extends CoroutineBackend)
- MUST extend `CoroutineBackend` and override `__init__` to record
  `self._workdir = member.workdir` (e.g., `.worktrees/<name>/`).
- `spawn()` MUST first `os.chdir(self._workdir)` IF the directory
  exists; if it doesn't exist, MUST initialize it via
  `WorktreeManager.initialize(name=member)` then `os.chdir`. Then call
  `super().spawn()`.
- All other methods inherited unchanged.

#### Scenario: tmux backend probe guards against missing binary
- **WHEN** `tmux` is not on `$PATH`
- **THEN** `PaneTmuxBackend.available()` returns `False`
- **AND** `BackendManager.effective_backend(member)` will not consider
  `pane-tmux` for upgrade

#### Scenario: iTerm2 probe handles AppleScript timeout
- **WHEN** `osascript` is killed (timeout=2s) because iTerm2 is locked
- **THEN** `available()` returns `False` (graceful degrade)
- **AND** `BackendManager.effective_backend` deprioritizes pane-iterm2

#### Scenario: Coroutine backend is the universal fallback
- **WHEN** none of pane-tmux / pane-iterm2 / pane-windows-terminal
  report available()
- **THEN** `effective_backend` returns `"coroutine"`
- **AND** the member runs in-process as an asyncio Task

#### Scenario: Worktree backend initializes missing worktree
- **WHEN** `member.workdir == ".worktrees/alice/"` and the directory
  does not exist
- **THEN** `WorktreeCoroutineBackend.spawn()` first calls
  `WorktreeManager.initialize(name="alice")`
- **AND** only then `os.chdir` and start the main loop

### Requirement: BackendManager detect / spawn / kill / is_alive

The `baozicode.teams.backend_manager.BackendManager` class MUST be the
Lead-side scheduler for member backends. It is constructed once per
`BaoZiCodeApp` instance at `on_mount`, lives across the entire app
lifetime (NOT cleared on `on_unmount`), and persists pane handles in
`<teams_dir>/<team>/pane_info.json`.

#### `BackendManager.detect_available_backends() -> dict[BackendType, bool]`
MUST probe each of the 4 platform-specific backends with
`available()` (with a 2s timeout default). Result is cached on the
`BackendManager` instance (one probe per `BackendManager` lifetime).
Coroutine backends always True.

#### `BackendManager.effective_backend(member: Member) -> BackendType`
MUST apply auto-upgrade logic:

1. If `member.backend in {coroutine, worktree-coroutine}` AND one of
   `pane-tmux` / `pane-iterm2` / `pane-windows-terminal` is available,
   upgrade by `sys.platform` priority:
   - `sys.platform == "darwin"`: pane-tmux > pane-iterm2 >
     pane-windows-terminal (rare on macOS but WSL/CrossOver paths)
   - `sys.platform == "win32"`: pane-windows-terminal > pane-tmux
   - `else`: pane-tmux > pane-windows-terminal > pane-iterm2 (disabled
     on non-Darwin)
2. If `member.backend` is explicitly a pane-* value, use it (regardless
   of availability) — if unavailable, log warn + fall back to coroutine.
3. Otherwise (member.backend = coroutine AND no pane available),
   return `"coroutine"`.

#### `BackendManager.spawn_if_offline(team, member) -> BackendHandle`
MUST be `async` and MUST:

1. Look up `(team, member)` in `self._handles`; if a handle exists and
   `handle.is_alive()`, return it (idempotent — no double spawn).
2. Acquire `self._spawn_locks[(team, member)]` (per-key asyncio.Lock).
3. Inside the lock, recheck — if a handle now exists and is alive,
   return it; else proceed.
4. Compute `effective_backend(member)`; instantiate the matching
   backend class; call `.spawn()` (synchronous subprocess for panes,
   `create_task` for coroutines).
5. Write `Mailbox.write_state(member_dir, MemberState(status="idle",
   last_active_ts=now, current_task=None, backend_pid=handle.pid))`.
6. Persist `<teams_dir>/<team>/pane_info.json` with the new entry
   (or update existing member entry).
7. Cache the handle in `self._handles[(team, member)]` and return.

#### `BackendManager.is_alive(team, member) -> bool`
MUST return `handle.is_alive()` if a handle exists in
`self._handles`; otherwise `False`. For pane backends,
`is_alive()` does `os.kill(pid, 0)` POSIX (raising
`ProcessLookupError` returns False) or `tasklist /FI "PID eq <pid>"`
on Windows (exit 0 + PID in stdout → True).

#### `BackendManager.kill(team, member, *, reason="", grace_seconds=5.0)`
MUST be `async` and MUST:

1. Pop `self._handles[(team, member)]` (handle).
2. `Mailbox.write_state(member_dir, MemberState(status="offline",
   current_task=None))`.
3. If handle exists, call `handle.kill(grace_seconds=grace_seconds)` via
   `asyncio.to_thread(...)`. Grace chain is SIGTERM → wait →
   SIGKILL (pane); `task.cancel()` for coroutine.
4. Update `pane_info.json` to set `members[<name>].backend_type = None`
   (next spawn will recompute).

#### Scenario: Spawn dedup prevents double start
- **WHEN** two `team_dispatch(team=devops, member=alice)` calls happen
  within 100ms (rare Lead re-dispatch)
- **THEN** the first call enters `spawn_if_offline` and acquires the
  per-`(devops, alice)` lock
- **AND** the second call awaits the same lock and observes the
  spawned handle alive; returns without re-spawning

#### Scenario: Backend probe is cached
- **WHEN** `BackendManager.detect_available_backends()` is called twice
  in the same Lead session
- **THEN** the second call returns the cached dict without re-probing
  the system (no extra `tmux -V` / `osascript` / `where wt.exe`
  subprocess calls)

#### Scenario: Auto-upgrade by sys.platform
- **WHEN** `member.backend == "coroutine"` (default from add-member)
  AND `tmux -V` succeeds (e.g., Linux dev box with tmux installed)
- **THEN** `effective_backend` returns `"pane-tmux"` and
  `PaneTmuxBackend.available()` confirms health
- **AND** `spawn_if_offline` invokes PaneTmuxBackend

#### Scenario: Graceful kill escalates
- **WHEN** `BackendManager.kill(team, member, grace_seconds=5.0)` is
  invoked and the member child process ignores SIGTERM (hung LLM loop)
- **THEN** after 5s the manager escalates to SIGKILL
- **AND** `state.json: status="offline"` is written before AND after
  the escalation (in case partial state recovery is needed)

#### Scenario: Stale PID recovers to coroutine
- **WHEN** Lead process restarts with pane_info.json from previous
  session but the OS-level PID no longer exists (machine rebooted)
- **THEN** `restore_panes()` detects `is_alive() == False` for that
  handle
- **AND** the matching entry is cleared from pane_info.json
- **AND** next `team_dispatch` triggers fresh `spawn_if_offline` on
  the recomputed effective_backend

### Requirement: pane_info.json cross-restart persistence

`BackendManager` MUST persist a JSON file at
`<teams_dir>/<team>/pane_info.json` with the following schema
(versioned `1.0`):

```json
{
  "schema_version": "1.0",
  "team": "<team_name>",
  "tmux_session_name": "baozicode-team-<team>",
  "iterm2_window_id": "<id|null>",
  "wt_tab_uuid": "<uuid|null>",
  "members": {
    "<name>": {
      "backend_type": "pane-tmux",
      "pane_identifier": "%5",
      "pid": 12345,
      "last_spawn_at": "2026-07-15T10:32:18Z"
    }
  }
}
```

`PaneInfo` MUST be a frozen dataclass with fields:
- `schema_version: str = "1.0"`
- `team: str`
- `tmux_session_name: str`
- `iterm2_window_id: str | None`
- `wt_tab_uuid: str | None`
- `members: dict[str, PaneMemberInfo]` (default empty)

`PaneMemberInfo` MUST be a frozen dataclass with fields:
- `backend_type: BackendType | None`
- `pane_identifier: str | None` (tmux pane id / iTerm2 window id / WT
  tab uuid)
- `pid: int | None`
- `last_spawn_at: datetime` (UTC)

Persistence semantics:

1. After `spawn_if_offline` succeeds, `BackendManager._persist_pane_info`
   writes pane_info.json atomically (write-then-rename).
2. After `BackendManager.kill` succeeds, the matching member's
   `backend_type` is cleared (set to `null`) but the row is kept
   (allows UI display of "previously registered").
3. `BackendManager.restore_panes(team)` is called at Lead `on_mount`:
   - Loads pane_info.json (missing → no-op)
   - For each member row, checks `is_alive()` against the stored PID
   - If alive, hydrate `self._handles[(team, member)]` with a thin
     wrapper handle (no respawn)
   - If dead, remove the entry and log info

#### Scenario: Restore re-attaches to live panes
- **WHEN** Lead restarts and `pane_info.json` shows
  `members.alice.pane_identifier=%5, pid=12345`
- **AND** `tmux has-session -t baozicode-team-devops` exits 0
- **THEN** `restore_panes` hydrates a `PaneTmuxBackend` wrapper handle
  for alice pointing at the existing pane
- **AND** next `team_send_message(team=devops, member=alice, body="…")`
  appends to inbox + touches wake, and the existing MemberMainLoop
  picks it up

#### Scenario: Missing pane_info.json is graceful no-op
- **WHEN** team has active members but no `pane_info.json` (foundation
  era state)
- **THEN** `restore_panes(team)` returns without error
- **AND** next `team_dispatch` triggers fresh `spawn_if_offline` which
  creates pane_info.json on first spawn

#### Scenario: Stale pane row cleared
- **WHEN** `members.alice.backend_type = "pane-tmux"` but
  `os.kill(pid=12345, 0)` raises `ProcessLookupError`
- **THEN** `restore_panes` removes the entry from `pane_info.json`
- **AND** `self._handles` does NOT contain alice
- **AND** next `team_dispatch(alice)` will spawn fresh on
  effective_backend

### Requirement: baozicode member run CLI subcommand

A new top-level CLI subcommand `baozicode member run` MUST spawn a
headless `MemberMainLoop` for a given (team, member):

```
baozicode member run --team <team_name> --name <member_name> [--cwd <abs_dir>]
```

Validation:
- `--team` is required, must pass `TeamNameValidator.validate`
- `--name` is required, must pass `TeamNameValidator.validate`
- If the team does not exist in `TeamsRegistry` → exit code 5
  (`TeamNotFound`)
- If the team exists but `--name` is not a registered member → exit
  code 6 (`MemberNotFound`)
- If `--cwd` is provided, override `member.workdir`; else use
  `member.workdir` from registry
- `os.chdir(cwd)` is called BEFORE `MemberMainLoop.run()` so all
  Bash tool invocations and File operations land in member.workdir

The subcommand MUST NOT raise `KeyboardInterrupt`; instead the
`MemberMainLoop` MUST catch `asyncio.CancelledError` and write
`state.json: status="offline"` before exiting cleanly (exit code 0 on
graceful terminate, 130 on `SIGINT`).

#### Scenario: Missing --team exits with UsageError
- **WHEN** user runs `baozicode member run` without `--team`
- **THEN** argparse exits with code 2 + stderr "the following
  arguments are required: --team"

#### Scenario: Team not found exits with TeamNotFound
- **WHEN** `TeamsRegistry.get("nonexistent")` raises `TeamNotFound`
- **THEN** CLI exits with code 5 + stderr "Error: TeamNotFound: team
  'nonexistent' does not exist"

#### Scenario: cwd is os.chdir'd before MemberMainLoop
- **WHEN** `baozicode member run --team=devops --name=alice` runs and
  member.workdir is `.worktrees/alice/`
- **THEN** `os.getcwd()` inside MemberMainLoop.run() returns
  `<project_root>/.worktrees/alice/`
- **AND** Bash tool invocations will see this as their PWD

#### Scenario: SIGTERM causes graceful exit
- **WHEN** member process receives SIGTERM while waiting in
  `wait_for_wake`
- **THEN** `MemberMainLoop.run` catches `asyncio.CancelledError`
- **AND** writes `state.json: status="offline"`
- **AND** process exits 0

### Requirement: MemberAgent role='member'

`build_member_agent` in `baozicode.teams.member_agent` MUST construct
an `Agent` configured for member runtime as follows:

- `role="member"` — bypasses team_* tools (`role_visibility=['lead']`
  filters them out); member sees only the 7 built-in tools
  (Read/Write/Edit/Bash/Grep/Glob/WebFetch) plus any
  `tool_type='internal'` runtime tools (e.g., `load_skill`)
- `mailbox_layer=MailboxLayer(teams_registry, team, member)` — a thin
  wrapper exposing `mailbox.read_inbox_unread()`, `mailbox.write_outbox(
  message)`, `mailbox.mark_inbox_read(message_ids)`. These are NOT
  ToolDefinitions (LLM cannot call them as tools); they are framework
  hooks used internally by `MemberMainLoop`.
- `conversation=ConversationManager()` — fresh, no resume from disk
  snapshot (per locked decision D5: mailbox is the only state source)
- All other Agent kwargs (`llm_client`, `permissions`,
  `skill_registry`, etc.) MUST mirror the Lead Agent's instantiation
  pattern at `BaoZiCodeApp.on_mount`, except for what role filtering
  already changes

Tools available to MemberAgent MUST include the 7 built-ins and the
v1.0 Skill loader (if enabled). Tools MUST NOT include any `team_*`
tools — `ToolRegistry.get_all_tools(role='member')` already enforces
this via v1-4-team-tools contracts.

#### Scenario: Member sees 7 built-in tools + load_skill
- **WHEN** `build_member_agent` returns an `Agent`
- **THEN** `agent._tool_registry.get_all_tools(role='member')` returns
  7 built-ins + `load_skill` (if Skills enabled)
- **AND** no `team_*` tool is present

#### Scenario: MailboxLayer exposes non-Tool APIs
- **WHEN** MemberAgent.loop runs a turn
- **THEN** framework calls `mailbox.read_inbox_unread()` to build the
  initial user message
- **AND** framework calls `mailbox.write_outbox()` after Agent
  emit_tool messages (so e.g., `team_send_message` from member to
  Lead gets persisted to outbox automatically)
- **AND** `mailbox.write_state(idle)` is called after the turn ends

#### Scenario: No conversation snapshot resume
- **WHEN** MemberAgent spawns fresh after a previous turn exited
- **THEN** the Agent's `ConversationManager` is empty
- **AND** the initial prompt is built from `mailbox.read_inbox_unread()`
  messages formatted as user-role messages
- **AND** no snapshot is loaded from disk

### Requirement: MemberMainLoop long-lived polling

`baozicode.teams.member_loop.MemberMainLoop` MUST be the long-lived
polling loop that runs in pane-derived `baozicode member run`
processes. Lifetime:

```
async def run(self) -> None:
    os.chdir(self._member_obj.workdir)
    Mailbox.wake_initialized(self._member_dir)
    while not self._terminate_flag.is_set():
        woke = await Mailbox.wait_for_wake(self._member_dir,
                                           timeout=30.0)
        if self._terminate_flag.is_set():
            break
        if not woke:
            continue
        msgs = Mailbox.read_messages(self._member_dir, "inbox",
                                     unread_only=True)
        if not msgs:
            continue
        Mailbox.write_state(self._member_dir,
                            MemberState(status="running",
                                        last_active_ts=now(),
                                        current_task=...))
        agent = build_member_agent(self._registry, self._team,
                                   self._member)
        try:
            await self._run_turn(agent, msgs)
        finally:
            Mailbox.write_state(self._member_dir,
                                MemberState(status="idle",
                                            last_active_ts=now()))
            Mailbox.mark_read(self._member_dir, "inbox",
                              message_ids=[m.timestamp for m in msgs])
    Mailbox.write_state(self._member_dir,
                        MemberState(status="offline"))
```

Termination scenarios:
- `self._terminate_flag.set()` from `team_cancel(terminate=True)` →
  loop exits at next check; if a turn is active, it is cancelled via
  `self._active_turn_task.cancel()`
- `SIGTERM` from outer process → `asyncio.CancelledError` in
  `wait_for_wake`; caught, `state.json: status="offline"` written,
  re-raises for `asyncio.run` to handle the exit
- `SIGINT` (Ctrl-C) → same path; CLI exits with code 130
- Uncaught exception in turn → logged, `state.json: status="idle"`
  written, loop continues (NOT propagated unless terminate_flag)

The loop MUST catch exceptions per iteration so that a single LLM hiccup
does not kill the long-lived process. Permanent fatal errors
(config parse failure, TeamStore corruption) MUST propagate and exit
nonzero.

#### Scenario: wait_for_wake unblocks on touch_wake
- **WHEN** MemberMainLoop is at `await wait_for_wake(...)` and Lead
  calls `Mailbox.touch_wake(member_dir)` after writing to inbox
- **THEN** the awaitable returns True within ~200ms (poll interval)
- **AND** the loop reads the inbox, runs a turn, writes
  `state.json: status="idle"`

#### Scenario: Fresh Agent per wake, no cross-turn state
- **WHEN** Lead dispatches two separate tasks to alice across 5
  minutes
- **THEN** each wake runs a fresh `build_member_agent` instance
- **AND** the second Agent starts with empty conversation
- **AND** the inbox messages from the dispatch events are the
  single source of context for that turn

#### Scenario: Turn cancellation propagates
- **WHEN** a turn is running (`agent.run` mid-stream) and Lead calls
  `team_cancel(terminate=True)`
- **THEN** `request_terminate()` sets the flag AND calls
  `self._active_turn_task.cancel()`
- **AND** the running Agent.run raises `asyncio.CancelledError`
- **AND** the cleanup path writes `state.json: status="offline"` and
  the loop exits

#### Scenario: Empty inbox does not spawn Agent
- **WHEN** `Mailbox.wait_for_wake` returns True (someone touched
  wake.signal) but `Mailbox.read_messages(...,unread_only=True)`
  returns empty
- **THEN** the loop continues without building an Agent
- **AND** no spurious `state.json: status="running"` write occurs
