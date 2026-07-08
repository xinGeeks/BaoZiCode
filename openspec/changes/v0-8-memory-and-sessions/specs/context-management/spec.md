# context-management Specification (v0.8 deltas)

## Purpose
Modifications to v0.7 context management (`baozicode/context/`) to align the session ID format from `uuid4().hex` to `YYYYMMDD-HHMMSS-xxxx`, with automatic migration of existing uuid-based directories at startup.

## ADDED Requirements

### Requirement: Session ID format change
The system MUST change `session_id` generation in `baozicode/context/orchestrator.py` (and any callers) from `uuid.uuid4().hex` (32 hex chars) to `format_session_id(datetime.now())` returning `YYYYMMDD-HHMMSS-xxxx` (17 chars: 15 timestamp + 1 dash + 4 random hex chars). The new format MUST be human-readable (a person can identify the date from the ID alone).

#### Scenario: New session ID at runtime
- **WHEN** the App boots at `2026-07-08T15:30:00`
- **THEN** `self._session_id` is `20260708-153000-a1b2` (timestamp + 4 random hex)
- **AND** `self.context_storage._session_id` is the same value

#### Scenario: Format validation
- **WHEN** the session ID is checked against regex `^\d{8}-\d{6}-[a-f0-9]{4}$`
- **THEN** the regex matches

### Requirement: v0.7 uuid directory migration at startup
The system MUST, during `BaoZiCodeApp.__init__`, scan `.baozicode/context/` for subdirectories whose names match the v0.7 uuid4 hex pattern `^[a-f0-9]{32}$`. For each such directory:
1. Read `<uuid>/_meta.json` if it exists, extract `created_at` (ISO 8601)
2. If `_meta.json` does not exist, use `directory.stat().st_mtime`
3. Compute new ID via `format_session_id(dt)`
4. Rename `<project_root>/.baozicode/context/<uuid>/` to `<project_root>/.baozicode/context/<new_id>/`
5. If the target name already exists, append `_legacy_<n>` where `n` starts at 1 and increments until unique
6. Log INFO with `old_name → new_name` for each migration

The system MUST skip empty uuid directories (those with no offload files inside) to avoid spurious renames.

#### Scenario: Single uuid directory migrated
- **WHEN** `.baozicode/context/abc123def456abc123def456abc12345/` exists with files
- **AND** its `_meta.json` has `created_at: 2026-07-07T10:00:00`
- **THEN** the directory is renamed to `.baozicode/context/20260707-100000-xxxx/`
- **AND** an INFO log line is emitted

#### Scenario: Empty uuid directory skipped
- **WHEN** `.baozicode/context/abc123.../` exists but contains no files
- **THEN** it is NOT renamed
- **AND** no log line is emitted

#### Scenario: Collision gets _legacy suffix
- **WHEN** uuid `aaa...` migrates to `20260707-100000-aaaa`
- **AND** that target directory already exists (from a different uuid with same timestamp)
- **THEN** uuid `aaa...` is renamed to `20260707-100000-aaaa_legacy_1`
- **AND** an INFO log includes the `_legacy_1` suffix

#### Scenario: Migration is idempotent
- **WHEN** the migration runs a second time (e.g., on next startup)
- **THEN** no uuid4 directories exist anymore
- **AND** the migration does nothing
- **AND** the bootstrap proceeds normally

### Requirement: Migration ordering before current session_id assignment
The system MUST run the uuid → timestamp migration BEFORE assigning `self._session_id` for the current session. This ensures the current session's new ID does not collide with a freshly-migrated directory name.

#### Scenario: Migration before current session
- **WHEN** `app.__init__` is called
- **THEN** step 1: run uuid migration
- **THEN** step 2: assign `self._session_id = format_session_id(datetime.now())`
- **AND** if the new ID matches an already-migrated directory, the system retries once with a new random suffix (same logic as D3)

### Requirement: format_session_id helper
The system MUST expose `baozicode/sessions/_id.py::format_session_id(dt: datetime) -> str` returning `f"{dt.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"`.

#### Scenario: Helper produces correct format
- **WHEN** `format_session_id(datetime(2026, 7, 8, 15, 30, 0))` is called
- **THEN** the result is `20260708-153000-<random-4-hex>`
- **AND** length is 17 chars

#### Scenario: Random portion differs across calls
- **WHEN** `format_session_id` is called twice with the same datetime
- **THEN** the random suffix differs between the two calls
- **AND** the timestamp portion is identical

### Requirement: v0.7 schema fields preserved
The system MUST NOT remove or rename any v0.7 `CompactionConfig` fields, `ContextWindow` fields, or `CompactionResult` fields. The change is purely additive — new ID format only.

#### Scenario: CompactionConfig still loadable
- **WHEN** a config.yaml from v0.7 is loaded by v0.8
- **THEN** the `compaction` block parses successfully
- **AND** all v0.7 defaults are preserved

### Requirement: v0.7 cleanup lifecycle preserved
The system MUST preserve v0.7 `ContextStorage.cleanup()` behavior in `BaoZiCodeApp.on_unmount` (deletes the just-finished session's offload files under `.baozicode/context/<sid>/`). v0.8 `sessions.cleanup_expired()` adds EXPIRED-session cleanup at startup (different lifecycle: removes JSONLs whose mtime > retention_days, plus their associated `.baozicode/context/<sid>/` directories). The two lifecycles MUST NOT conflict:
- Startup `cleanup_expired` only removes sessions older than `retention_days` (today's session is preserved)
- `on_unmount` removes the just-finished session regardless of age
- A session loaded via `/resume` keeps its `.baozicode/context/<sid>/` files intact during the resume; the next `on_unmount` cleans them up normally

#### Scenario: on_unmount cleanup unchanged from v0.7
- **WHEN** the user exits the App
- **THEN** `BaoZiCodeApp.on_unmount` calls `self.context_storage.cleanup()` exactly as in v0.7
- **AND** the current session's `.baozicode/context/<sid>/` directory is removed (files + empty dir)
- **AND** sessions from other sids are NOT touched

#### Scenario: Resumed session keeps its context files during run
- **WHEN** user runs `/resume 20260708-153000-a1b2` and runs more turns
- **THEN** the resumed session's `.baozicode/context/<sid>/` files remain intact during the run
- **AND** `on_unmount` cleanup at the end of THIS session removes them (the resumed session effectively "becomes" the current session)

#### Scenario: Expired session cleanup at next startup
- **WHEN** session `20260501-120000-aaaa` (40 days old) is expired AND the App starts
- **THEN** `sessions.cleanup_expired()` removes both `20260501-120000-aaaa.jsonl` AND `.baozicode/context/20260501-120000-aaaa/`
- **AND** today's session (`20260708-153000-a1b2`) is NOT touched