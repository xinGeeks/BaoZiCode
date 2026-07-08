# v0.8 Memory & Sessions — Tasks

## 1. Configuration schema for memory and sessions

- [ ] 1.1 In `baozicode/config/schema.py`, add `MemoryConfig` Pydantic model with fields: `enabled: bool = True`, `user_dir: Path = Path("~/.baozicode/memory")`, `project_dir: Path = Path(".baozicode/memory")`, `index_max_lines: int = 200` (≥ 50), `index_max_bytes: int = 25600` (≥ 1024), `warning_lines: int = 180` (≥ 25, must be < `index_max_lines`), `warning_bytes: int = 22528` (must be < `index_max_bytes`), `recent_turns_for_update: int = 5` (≥ 1), `auto_compress_per_session: int = 1` (≥ 0)
- [ ] 1.2 Add `SessionConfig` Pydantic model with fields: `enabled: bool = True`, `dir: Path = Path(".baozicode/sessions")`, `retention_days: int = 30` (≥ 1)
- [ ] 1.3 In `AgentConfig`, add `time_gap_threshold_hours: int = 8` (≥ 1, Field with `gt=0`)
- [ ] 1.4 In `AppConfig`, add `memory: MemoryConfig = MemoryConfig()` and `sessions: SessionConfig = SessionConfig()`
- [ ] 1.5 Mark `AppConfig.memory_path` as deprecated (move default to `Path("~/.config/baozicode/memory.md")` but keep field readable); add docstring noting "deprecated in v0.8, will be removed in v0.9; use MemoryConfig.user_dir + MemoryConfig.project_dir instead"
- [ ] 1.6 Update `config/loader.py` `load_config()` to detect non-default `memory_path` and emit a stderr warning: `WARN: memory_path is deprecated, move to <user_dir>/MEMORY.md + <project_dir>/MEMORY.md`
- [ ] 1.7 Add unit tests covering: defaults load successfully, `memory.index_max_lines: 0` rejected, `memory.warning_lines >= index_max_lines` rejected (logical invariant), `sessions.retention_days: 0` rejected, `agent.time_gap_threshold_hours: 0` rejected, deprecated `memory_path` triggers loader warning

## 2. Instructions loader — three-tier BaoZiCode.md scan + @include resolution

- [ ] 2.1 Create `baozicode/instructions/__init__.py` exposing `bootstrap(project_root: Path, config: AppConfig) -> LoadedInstructions` and `NoteType` enum re-export
- [ ] 2.2 Create `baozicode/instructions/schema.py` with dataclasses: `InstructionLayer { source: Literal["user_global", "project_local", "project_root"], path: Path, raw_text: str }`, `LoadedInstructions { layers: tuple[InstructionLayer, ...], concatenated: str, included_files: set[Path], warnings: list[str] }`
- [ ] 2.3 Create `baozicode/instructions/loader.py` with `scan_three_tiers(project_root: Path) -> list[Path]` returning the three candidate paths in priority order: `~/.baozicode/BaoZiCode.md` first, `<project>/.baozicode/BaoZiCode.md` second, `<project_root>/BaoZiCode.md` third; missing files are silently skipped (not errored)
- [ ] 2.4 Implement `load_layer(path: Path) -> InstructionLayer` that reads UTF-8, strips trailing whitespace, returns layer with raw_text
- [ ] 2.5 Implement `_include_pattern` regex matching `@include\s+(.+?)\s*$` on a per-line basis (one include per line, no inline other content)
- [ ] 2.6 Create `baozicode/instructions/include.py` with `resolve_includes(text: str, current_file: Path, project_root: Path, *, max_depth: int = 5, visited: set[Path] | None = None) -> tuple[str, list[str]]` that: parses `@include` lines, resolves paths relative to `current_file.parent`, calls itself recursively with `depth + 1` and `visited | {current_file.resolve()}`, enforces three guards (depth limit, cycle detection via visited, path whitelist via `is_relative_to(project_root) OR is_relative_to(user_baozicode)`), collects warnings on any failure but continues parsing
- [ ] 2.7 Implement `concat(layers: list[InstructionLayer], project_root: Path) -> LoadedInstructions` that: calls `resolve_includes` on each layer in order, joins all resolved text with `\n\n---\n\n` separator, returns `LoadedInstructions` with `concatenated` and the union of all `included_files` and `warnings`
- [ ] 2.8 Inject `LoadedInstructions.concatenated` into system prompt at the top, before any other fixed section; if `concatenated` is empty string, skip injection entirely (no empty `## 项目指令` section)
- [ ] 2.9 If all three BaoZiCode.md files are missing, print a single stderr banner line: `[BaoZiCode] 未找到 BaoZiCode.md,建议创建项目根目录文件`; do not error
- [ ] 2.10 Add unit tests covering: empty (all three missing) returns empty concatenated + banner; one file loads correctly; three files concatenate in order (user_global → project_local → project_root); @include relative path resolved from current_file.parent; @include absolute path rejected; @include path escaping project_root OR user_baozicode rejected; @include cycle (A→B→A) detected and skipped with warning; @include depth 6 rejected; @include missing file → warning, other includes still processed; existing `BaoZiCode.md` is NOT read by this loader (only `BaoZiCode.md` files)

## 3. Memory store — CRUD + frontmatter + index file

- [ ] 3.1 Create `baozicode/memory/__init__.py` exposing `MemoryStore`, `Note`, `NoteType`, `bootstrap(project_root: Path, config: AppConfig) -> tuple[MemoryStore, MemoryStore]` (returns `(user_store, project_store)`)
- [ ] 3.2 Create `baozicode/memory/schema.py` with: `NoteType(str, Enum)` with values `USER_PREF = "user-pref"`, `CORRECTION = "correction"`, `PROJECT = "project"`, `REFERENCE = "reference"`; dataclass `Note { type: NoteType, slug: str, title: str, content: str, created_at: datetime, source_session: str, tags: list[str], access_count: int, last_accessed: datetime, path: Path }`; dataclass `IndexEntry { slug: str, type: NoteType, title: str, one_liner: str }`; dataclass `MemoryIndex { entries: list[IndexEntry], total_lines: int, total_bytes: int }` with method `format_for_prompt(self) -> str` that renders entries as markdown sections per `auto-memory/spec.md` "Index formatting for prompt" requirement (format: `## [<type>] <slug> — <title>\n<one_liner>\n\n` per entry, joined together)
- [ ] 3.3 Create `baozicode/memory/store.py` with `MemoryStore` class: `__init__(root: Path, scope: Literal["user", "project"])`. On `__init__`, call `_ensure_root()` which does `mkdir(parents=True, exist_ok=True)` and creates empty `MEMORY.md` with header `# Memory Index (user|project)\n\n_Empty — no notes yet._\n` if file doesn't exist
- [ ] 3.4 Implement `read_index() -> MemoryIndex`: parse `MEMORY.md` as markdown; each `## [<type>] <slug> — <title>` heading becomes one `IndexEntry`; under each heading, the first non-empty line is the `one_liner` (≤ 80 chars); `total_lines` and `total_bytes` are physical file metrics (not parsed)
- [ ] 3.5 Implement `add_note(note: Note) -> Path`: validate `slug` matches `^[a-z0-9][a-z0-9-]*$` and length ≤ 60; validate `type` is one of 4 enum values; build file content as `---\ntype: <type>\ncreated_at: <iso8601>\nsource_session: <sid>\ntags: [<csv>]\naccess_count: 0\nlast_accessed: <iso8601>\n---\n# <title>\n\n<body>\n`; write to `<root>/<slug>.md` with `f.flush() + os.fsync()` for atomicity; return the path
- [ ] 3.6 Implement `update_note(slug: str, new_content: str) -> None`: rewrite the body section of `<slug>.md`, update `last_accessed` field; reject if source_session != current_session_id AND `delete` operation requested (to prevent cross-session deletion); for `update`, allow content append only (append `new_content` to existing body with `\n\n` separator, do not overwrite existing)
- [ ] 3.7 Implement `delete_note(slug: str) -> bool`: unlink `<slug>.md`, return True if file existed; reject if `source_session` of that note != current_session_id
- [ ] 3.8 Implement `rewrite_index(entries: list[IndexEntry]) -> None`: build new `MEMORY.md` content from entries (one `## [<type>] <slug> — <title>` block per entry, each followed by one_liner and blank line); refuse if total_lines > `config.memory.index_max_lines` OR total_bytes > `config.memory.index_max_bytes`; if refused, raise `IndexOverflowError`
- [ ] 3.9 Implement `increment_access(slug: str) -> None`: parse existing `<slug>.md`, bump `access_count` by 1, update `last_accessed`, rewrite file
- [ ] 3.10 Add unit tests covering: bootstrap creates empty MEMORY.md; read_index on empty returns empty entries; add_note creates valid file with correct frontmatter; slug validation rejects uppercase / spaces / leading dash; update_note appends without losing existing content; delete_note removes file; rewrite_index refuses when over limit; increment_access bumps count; user and project stores are physically isolated (writes to one do not appear in the other)

## 4. Sessions archiver — JSONL append-only

- [ ] 4.1 Create `baozicode/sessions/__init__.py` exposing `list_sessions(root: Path) -> list[SessionMeta]`, `SessionArchiver`, `load_session(id: str, root: Path, ...)`, `bootstrap(project_root: Path, config: AppConfig) -> tuple[SessionArchiver, list[SessionMeta]]`
- [ ] 4.2 Create `baozicode/sessions/schema.py` with dataclasses: `SessionMeta { id: str, title: str, created_at: datetime, last_message_at: datetime, message_count: int, size_bytes: int, path: Path }`; `SessionEntry { timestamp: datetime, role: Literal["user", "assistant", "tool"], blocks: list[dict], tool_call_id: str | None }`; `ResumeResult { messages: list[Message], meta: SessionMeta, warnings: list[str], applied_compact: bool, time_gap_inserted: bool }`
- [ ] 4.3 Create `baozicode/sessions/archive.py` with `SessionArchiver` class: `__init__(root: Path, session_id: str)`; on init, `mkdir(parents=True, exist_ok=True)` for `<root>/`; resolves `self._path = root / f"{session_id}.jsonl"`; exposes `append(message: Message) -> None`
- [ ] 4.4 Implement `append(message: Message)`: serialize Message via custom encoder that handles `TextBlock`/`ToolUseBlock`/`ToolResultBlock` (use `dataclasses.asdict` recursively with `default=str` for `datetime`); open file in `"a"` mode, write `json.dumps(...) + "\n"`; call `f.flush() + os.fsync(f.fileno())`; never raise on serialization errors — log warning and skip the entry (do not block conversation)
- [ ] 4.5 Add `.baozicode/sessions/` to `.gitignore` idempotently (similar to v0.7 ContextStorage gitignore handling); do NOT add `.baozicode/memory/` to .gitignore (user choice — they may want to commit user-global)
- [ ] 4.6 Add unit tests covering: append creates file with one JSON line per call; append after multiple calls produces N valid JSON lines; flush+fsync visible via `os.fsync` mock; bad Message serialization is skipped with warning; multiple appends within same process produce same file

## 5. Sessions resume — exception handling four-piece set

- [ ] 5.1 Create `baozicode/sessions/resume.py` with `load_session(session_id: str, sessions_root: Path, *, context_storage: ContextStorage, llm: LLMClient, compact_ctx: MaybeCompactContext, time_gap_threshold_hours: int) -> ResumeResult`
- [ ] 5.2 Implement line-by-line JSON parse loop: `for line in path.read_text().splitlines(): try: json.loads(line); append to raw_entries except JSONDecodeError: log warning("skip bad line {N}"); continue`
- [ ] 5.3 Implement orphan tool_call truncation: maintain `seen_tool_use_ids: set[str]` populated from `ToolUseBlock` entries; for each `tool` role entry, check its `tool_call_id` against seen set; if not found, truncate from this line onwards (drop the line + all subsequent lines) and append warning
- [ ] 5.4 Implement token-budget-driven compact: after building `messages: list[Message]` from raw_entries, call `await maybe_compact(messages, trigger="resume", ctx=compact_ctx)`; if `compact_result.triggered`, replace messages with new ones; record `applied_compact = True`
- [ ] 5.5 Implement time_gap insertion: compute `gap_hours = (now - first_user_entry.timestamp).total_seconds() / 3600`; if `gap_hours > time_gap_threshold_hours`, build a `<system-reminder type="time_gap" ttl="once">距上次会话已过 X 小时,中间可能发生过上下文变化。请确认以下事项仍然成立:<列出该 session 的最后 5 条 user 消息标题>` message and insert at `messages[-2]` position (before last user msg); record `time_gap_inserted = True`
- [ ] 5.6 Convert raw_entries back to `Message` objects via inverse of step 4.4 serialization (use dataclass constructors based on `role` + block types)
- [ ] 5.7 Return `ResumeResult { messages, meta, warnings, applied_compact, time_gap_inserted }`; TUI logs warnings to status line; does NOT raise on any warning (resume is best-effort)
- [ ] 5.8 Add unit tests covering: well-formed JSONL → exact message reconstruction; one bad line → skipped + warning, rest intact; orphan tool_result → truncated at that line + warning; resume triggers Layer 2 compact when tokens over budget (mock LLM, use real maybe_compact); time_gap inserted when gap > threshold; time_gap NOT inserted when gap ≤ threshold; empty JSONL → empty messages list + warning

## 6. Sessions cleanup + listing

- [ ] 6.1 Create `baozicode/sessions/cleanup.py` with `cleanup_expired(sessions_root: Path, *, context_root: Path, retention_days: int) -> int` returning count of removed sessions
- [ ] 6.2 For each `<session_id>.jsonl` under `sessions_root`: if `(now - path.stat().st_mtime).days > retention_days`, remove the JSONL file AND the corresponding `<context_root>/<session_id>/` directory if it exists
- [ ] 6.3 Skip cleanup if `retention_days <= 0` (disabled) or if session's mtime is today (defensive guard)
- [ ] 6.4 All operations wrapped in try/except per session; one failure must not abort the whole sweep; log warning per failure
- [ ] 6.5 Implement `list_sessions(sessions_root: Path) -> list[SessionMeta]`: scan all `*.jsonl`, parse each file's first line for `title` (first user message first 60 chars) and `created_at`; count lines for `message_count`; stat for `size_bytes` and `last_message_at` (mtime as approximation); sort by `last_message_at` descending (most recent first)
- [ ] 6.6 Add unit tests covering: expired session removed; today's session preserved; corresponding context dir removed when JSONL removed; corresponding context dir NOT removed when other session's JSONL is removed (no cross-contamination); list_sessions returns sorted by mtime desc; empty dir returns empty list

## 7. v0.7 session_id migration: uuid4 → YYYYMMDD-HHMMSS-xxxx

- [ ] 7.1 In `baozicode/app.py:74`, change `self._session_id: str = uuid.uuid4().hex` to `self._session_id: str = format_session_id(datetime.now())` calling the new helper in `baozicode/sessions/_id.py` (task 7.2). `baozicode/context/orchestrator.py` does NOT generate session_id itself — it only receives `MaybeCompactContext` with a session_id already assigned by the caller, so this file needs no change.
- [ ] 7.2 Create `baozicode/sessions/_id.py` with `format_session_id(dt: datetime) -> str` returning `f"{dt.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"` (16 chars + dash = 17 chars total: `20260708-153000-a1b2`)
- [ ] 7.3 In `baozicode/app.py` `__init__`, add a new step BEFORE `self._session_id` assignment: scan `.baozicode/context/` for subdirectories matching `^[a-f0-9]{32}$` (uuid4 hex pattern)
- [ ] 7.4 For each uuid directory: read `_meta.json` if exists for `created_at`; else use directory `st_mtime`; compute new id via `format_session_id(dt)`; check `.baozicode/context/<new_id>/` doesn't already exist; if collision, append `_legacy_<n>` suffix where n starts at 1 and increments until unique
- [ ] 7.5 Rename directory using `Path.rename()` (atomic on POSIX, near-atomic on Windows); log INFO for each migration with `old_name → new_name`
- [ ] 7.6 Skip if uuid directory is empty (just-created with no offload files); this avoids spurious renames
- [ ] 7.7 Run migration BEFORE setting `self._session_id = format_session_id(datetime.now())` so current session doesn't collide with a freshly-migrated one
- [ ] 7.8 Add unit tests covering: single uuid4 dir renamed correctly; multiple uuid4 dirs renamed with unique new ids (different timestamps); collision detection appends `_legacy_<n>`; empty uuid4 dir skipped; non-uuid4 dir (e.g., new timestamp format) left alone; migration is idempotent (running twice does nothing on second pass); `_meta.json` created_at preferred over mtime

## 8. Conversation manager — archiver callback integration

- [ ] 8.1 In `baozicode/conversation/manager.py`, change `__init__` signature to accept optional `archiver: SessionArchiver | None = None` parameter; store as `self._archiver`
- [ ] 8.2 In `add_user`, `add_assistant`, `add_message`, `add_tool_call`, `add_tool_result`, `add_turn`: after `self._messages.append(msg)`, call `self._archiver.append(msg) if self._archiver else None` (silent no-op when no archiver)
- [ ] 8.3 Existing `set_messages` (added in v0.7) does NOT call archiver (it's a wholesale replacement, not an incremental append); document this in docstring
- [ ] 8.4 Add unit tests covering: archiver=None → no append calls (back-compat with v0.7 tests); archiver set → each add_* method triggers one append; append failures from archiver do NOT propagate to caller (logged + skipped, conversation continues)

## 9. Agent loop hookpoints — JSONL append + memory update trigger

- [ ] 9.1 In `baozicode/agent/loop.py`, add `self._archiver: SessionArchiver | None = None` to `Agent.__init__`; expose `set_archiver(archiver)` setter (allows late binding after Agent construction)
- [ ] 9.2 In `Agent.run()` after `self._conversation.add_user(user_message)` (existing line), no change needed because ConversationManager itself handles the append via injected archiver (task 8.2)
- [ ] 9.3 In `_inject_reminders`, add support for two new reminder types: `time_gap` (static, body = `<time_gap_body>`) and `memory_refreshed` (sticky, body = `<last note update summary>`); both go to messages[-2] splice like existing types
- [ ] 9.4 At the COMPLETED / MAX_ITERATIONS_REACHED exit path (around line 318-320 and the `finally` block), add: `if terminate_reason in (StopReason.COMPLETED, StopReason.MAX_ITERATIONS_REACHED): snapshot = list(self._conversation.to_list()); asyncio.create_task(self._memory_updater.update(snapshot))`
- [ ] 9.5 The `_memory_updater` is set via `set_memory_updater(updater)` setter on Agent; if None, the trigger is silently skipped (no-op for users who disable memory)
- [ ] 9.6 In `MemoryUpdater.update(snapshot)`, after the LLM call returns: if `self._current_session_id != snapshot_session_id`, log info and abort (no disk write); else `self._store.apply(operations)`
- [ ] 9.7 Add unit tests covering: set_archiver None is no-op; set_archiver active causes JSONL writes (use fake archiver); COMPLETED triggers create_task; USER_CANCELLED does NOT trigger; session_id mismatch during async update aborts silently

## 10. Memory updater — LLM extraction + fenced JSON parser + overflow handling

- [ ] 10.1 Create `baozicode/memory/updater.py` with `MemoryUpdater` class: `__init__(llm: LLMClient, store: MemoryStore, config: MemoryConfig, current_session_id_fn: Callable[[], str])`
- [ ] 10.2 Implement `async update(messages_snapshot: list[Message]) -> None`: extract last N turns from snapshot (N = `config.recent_turns_for_update`), build prompt via `_build_prompt(turns, store.read_index())`, call LLM via `llm.stream(messages=[Message(role="user", content=prompt)], system=NOTE_SYSTEM, tools=[], cache_breakpoints=None)`, parse response via `_parse_fenced_json(response_text)`, apply operations via `_apply_operations(operations)`
- [ ] 10.3 Create `baozicode/memory/prompt.py` with `NOTE_EXTRACTION_SYSTEM` constant and `build_extraction_prompt(turns, user_index, project_index) -> str`. Prompt MUST include: (a) the 4 note types with one-line descriptions, (b) instruction to output ONLY `add|update|delete` operations, (c) instruction to dedup against existing index, (d) example fenced JSON output, (e) explicit "DO NOT call any tools" instruction
- [ ] 10.4 Implement `_parse_fenced_json(text: str) -> dict | None`: regex `r"```json\s*\n(.*?)\n```"` with `re.DOTALL`; on match, `json.loads(group(1))`; on parse error or no match, return None and record `parse_failures` counter
- [ ] 10.5 Implement `_apply_operations(operations: dict) -> list[Note]`: for each operation in `operations["operations"]`, dispatch by `action`: `add` → `store.add_note(Note(...))`, `update` → `store.update_note(...)`, `delete` → `store.delete_note(...)` (subject to source_session check from task 3.7); collect successfully-applied notes
- [ ] 10.6 After applying, call `overflow.check_and_act(store, session_limiter)` (task 11); if it triggered STATE_AUTO_COMPRESS, fire another `asyncio.create_task(_auto_compress(store))` (task 11 implementation)
- [ ] 10.7 Add unit tests covering: well-formed fenced JSON parses; missing fenced block → returns None + parse_failures++; invalid JSON in block → returns None; add operation creates note file; update operation appends to existing; delete operation removes file; delete of cross-session note is rejected; LLM stream exception → silent skip + retry counter bump

## 11. Memory overflow — three-tier layered handler

- [ ] 11.1 Create `baozicode/memory/overflow.py` with `MemoryOverflowHandler` class: `__init__(config: MemoryConfig, llm: LLMClient)`; state machine tracked via instance attributes (`_state: Literal["NORMAL", "WARN", "AUTO_COMPRESS", "HUMAN_NEEDED"]`, `_session_limiter: int = 0`, `_last_warning_at: datetime | None`)
- [ ] 11.2 Implement `check_and_act(store: MemoryStore) -> OverflowAction`: read index metrics via `store.read_index().total_lines` and `.total_bytes`; transition state based on thresholds; return one of `OverflowAction.NOOP`, `OverflowAction.WARN`, `OverflowAction.AUTO_COMPRESS_SCHEDULED`, `OverflowAction.HUMAN_NEEDED`
- [ ] 11.3 STATE_NORMAL (lines < warn_lines AND bytes < warn_bytes) → return NOOP
- [ ] 11.4 STATE_WARN (warn_lines ≤ lines < max_lines OR warn_bytes ≤ bytes < max_bytes) → log yellow warning, return WARN
- [ ] 11.5 STATE_AUTO_COMPRESS (lines ≥ max_lines OR bytes ≥ max_bytes) AND `_session_limiter < config.auto_compress_per_session` → increment limiter, schedule `asyncio.create_task(self._auto_compress(store))`, return AUTO_COMPRESS_SCHEDULED
- [ ] 11.6 STATE_HUMAN_NEEDED → log red error, return HUMAN_NEEDED (caller is responsible for surfacing to user via TUI sticky reminder)
- [ ] 11.7 Implement `async _auto_compress(store: MemoryStore)`: build LLM prompt asking to merge duplicate notes, shorten descriptions, drop empty sections, keep unique business records; constraint: "压缩后目标体积 ≤ 阈值 70%"; call LLM with `tools=[]`, parse fenced JSON operations; apply; re-check state; if still over, transition to HUMAN_NEEDED
- [ ] 11.8 Add unit tests covering: NORMAL → NOOP; WARN → NOOP but warning logged; AUTO_COMPRESS schedules task; AUTO_COMPRESS second time same session → HUMAN_NEEDED; _auto_compress successful → state returns to NORMAL or WARN; _auto_compress failure → state transitions to HUMAN_NEEDED; _auto_compress exceeds token limit → catch exception → HUMAN_NEEDED

## 12. Prompt section — memory index integration

- [ ] 12.1 In `baozicode/prompt/sections/memory.py`, change `render(ctx: BuildContext) -> str` to read from `ctx.memory_index_user: str | None` and `ctx.memory_index_project: str | None` (new BuildContext fields); if both empty, return "" (no section rendered)
- [ ] 12.2 If only one is non-empty, render only that section; if both, render two sections separated by `\n\n`
- [ ] 12.3 Section format: `## 长期记忆 (项目级)\n<index>` and `## 长期记忆 (用户级)\n<index>`
- [ ] 12.4 Add `memory_index_user: str | None = None` and `memory_index_project: str | None = None` to `BuildContext` in `baozicode/prompt/types.py`
- [ ] 12.5 In `PromptBuilder.build()`, after constructing `ctx`, call `memory_store_user.read_index().format_for_prompt()` and same for project, pass into ctx via `BuildContext.replace(...)` or dataclass `replace`
- [ ] 12.6 Keep backward compatibility: if `config.memory_path` is set (deprecated) AND new memory dirs are empty, fall back to reading the single file as the user-level index
- [ ] 12.7 Add unit tests covering: empty dirs → no section rendered; only user dir has notes → only user section rendered; only project dir has notes → only project section rendered; both have notes → two sections rendered in order (user first, project second); deprecated memory_path fallback works when new dirs empty

## 13. TUI integration — /resume, /memory, /new slash commands

- [ ] 13.1 In `baozicode/tui/chat_screen.py`, register `/resume` in slash command dispatcher: list sessions via `app.sessions_list()`, show modal with titles + dates, user picks one or cancels; on pick, call `app.resume_session(session_id)`
- [ ] 13.2 Register `/memory`: read `app.memory_store_user.read_index()` + `app.memory_store_project.read_index()`, render in two-column display; if `app.memory_update_in_progress`, show "笔记更新中..." status line
- [ ] 13.3 Register `/new`: confirm modal "放弃当前会话,新建?", on yes clear `app.conversation`, generate new session_id, clear `app.archiver` state
- [ ] 13.4 Update `/help` text to mention the three new commands
- [ ] 13.5 Update `/status` to include `notes: N (user: X, project: Y)` and `session_id: <sid>` and `memory_updates_triggered: N`
- [ ] 13.6 Add unit tests covering: slash dispatch routes /resume /memory /new to handlers; /resume modal lists sessions sorted by date; /memory displays both indices; /new confirmation prevents accidental clear

## 14. CLI integration — --resume / --new flags + banner

- [ ] 14.1 In `baozicode/cli.py`, add `--resume ID` argument: if provided, set `args.target_session_id = ID` and skip the interactive selection
- [ ] 14.2 Add `--new` argument: if provided, force new session regardless of existing sessions (do not show selection modal)
- [ ] 14.3 Add `--no-banner` argument: if provided, suppress the startup banner (instructions + memory + sessions summary); default is to show banner
- [ ] 14.4 Modify banner output: print `[BaoZiCode] 指令: 3 layers loaded (user_global + project_local + project_root)` or `(none found, 建议创建 BaoZiCode.md)`; print `[BaoZiCode] 记忆: N notes (user: X, project: Y), index: L lines / B bytes (state: NORMAL|WARN|AUTO_COMPRESS|HUMAN_NEEDED)`; print `[BaoZiCode] 会话: N sessions found, latest: <id> (<title>)` or `(none)`
- [ ] 14.5 When `--resume` not provided AND sessions exist AND no `--new`: pass `pending_session_selection = True` to App, which triggers the `/resume`-equivalent modal at app.on_mount BEFORE ChatScreen is pushed
- [ ] 14.6 When `--resume` provided: validate session_id exists in list_sessions; if not, error and exit 1; else pass `resume_target = session_id` to App
- [ ] 14.7 Update `_parse_args` to make all new args optional; existing args (`--config`) unchanged
- [ ] 14.8 Add unit tests covering: --resume existing id loads; --resume nonexistent id errors; --new skips selection; no flag with sessions shows selection modal; banner reflects actual loaded state

## 15. App bootstrap — instructions → memory → sessions order

- [ ] 15.1 In `baozicode/app.py` `__init__`, BEFORE existing `permissions_bootstrap` call, add: `self._instructions = instructions_bootstrap(project_root, config)` (no failure, banner-only)
- [ ] 15.2 AFTER permissions bootstrap, add: `user_store, project_store = memory_bootstrap(project_root, config)`; store on `self.memory_store_user` and `self.memory_store_project`
- [ ] 15.3 AFTER memory bootstrap, add: run v0.7 session_id migration (task 7), then `self._session_id = format_session_id(datetime.now())`; `archiver, sessions_meta = sessions_bootstrap(project_root, config, self._session_id)`; store on `self.archiver` and `self.sessions_meta`
- [ ] 15.4 Pass `self.archiver` into `ConversationManager()` construction (changed in task 8.1); pass `self.archiver` and `memory_updater` into Agent via setters (task 9.1, 9.5)
- [ ] 15.5 In `on_mount`, run pending session selection BEFORE pushing ChatScreen (if `pending_session_selection` set); on selection, call `app.resume_session(id)` to load; if user presses Esc / cancels the modal, default to creating a fresh session (do NOT block startup, log info `"session selection cancelled, starting fresh session"`); if "new session" explicitly chosen, skip resume and generate new sid
- [ ] 15.6 Update `on_unmount` to also call `self.archiver.close()` if archiver has a close method (no-op currently, but reserves the hook for future fsync batching)
- [ ] 15.7 Pass `memory_index_user` and `memory_index_project` strings into `PromptBuilder.build()` via `BuildContext` (task 12.5)
- [ ] 15.8 Add unit tests covering: bootstrap order is permissions → instructions → memory → sessions (verify by call ordering); bootstrap with config.memory.enabled = False skips memory init; bootstrap with config.sessions.enabled = False skips sessions init (and no JSONL files created)

## 16. Migration README + deprecation notice

- [ ] 16.1 Create `docs/migrations/v0.7-to-v0.8.md` with sections: (a) what changed (3 mechanisms), (b) how to migrate `memory_path` to `user_dir/MEMORY.md` + `project_dir/MEMORY.md`, (c) how to disable memory or sessions via config, (d) example new `config.yaml` snippet, (e) FAQ about session_id format change
- [ ] 16.2 Update `README.md` "当前版本" section to mention v0.8 three mechanisms
- [ ] 16.3 Update `config.example.yaml` with full `memory:` + `sessions:` + `agent.time_gap_threshold_hours` blocks
- [ ] 16.4 Add a deprecation note in `baozicode/config/schema.py` on `AppConfig.memory_path` field: docstring says "Deprecated since v0.8. Will be removed in v0.9. Use MemoryConfig.user_dir + MemoryConfig.project_dir with MEMORY.md index files instead."
- [ ] 16.5 Update CHANGELOG.md with v0.8 entry listing new capabilities, modified capabilities, deprecated fields

## 17. Integration tests — end-to-end memory + sessions

- [ ] 17.1 Create `tests/instructions/__init__.py` and `tests/instructions/conftest.py` with shared fixtures: `tmp_project_root` (autouse tmp_path), `write_baozicode_md` helper, `mock_user_baozicode_dir` (monkeypatch user dir)
- [ ] 17.2 Add `tests/instructions/test_loader.py` with 8 tests: empty (no files) + banner; one layer; three layers in correct order; @include relative; @include absolute rejected; @include cycle; @include depth limit; @include path escape
- [ ] 17.3 Create `tests/memory/__init__.py` and `tests/memory/conftest.py` with fixtures: `memory_store_user`, `memory_store_project`, `sample_note`
- [ ] 17.4 Add `tests/memory/test_store.py` with 10 tests: bootstrap creates empty MEMORY.md; CRUD round-trip; frontmatter parse; slug validation; rewrite_index overflow refusal; increment_access; user/project isolation; deprecated memory_path fallback
- [ ] 17.5 Add `tests/memory/test_updater.py` with 6 tests using a mock LLM: well-formed fenced JSON; missing fenced block; invalid JSON; operations applied correctly; session_id mismatch aborts; LLM exception silent skip
- [ ] 17.6 Add `tests/memory/test_overflow.py` with 8 tests: state transitions NORMAL→WARN→AUTO_COMPRESS→HUMAN_NEEDED; _auto_compress success returns to NORMAL; _auto_compress failure HUMAN_NEEDED; session_limiter monotonic
- [ ] 17.7 Create `tests/sessions/__init__.py` and `tests/sessions/conftest.py` with fixtures: `tmp_sessions_root` (autouse `tmp_path`), `archiver` (SessionArchiver rooted at `tmp_sessions_root`), `mock_resume_context` (a `MaybeCompactContext` with a `MockLLMClient` returning canned 6-section summary text and a real `ContextStorage` rooted at `tmp_path`; provides async fixture factory `_make_compact_ctx(messages: list[Message]) -> MaybeCompactContext` that wires the mock LLM to assert on the input format)
- [ ] 17.8 Add `tests/sessions/test_archive.py` with 6 tests: append JSONL; flush+fsync; bad serialization skipped; multiple appends
- [ ] 17.9 Add `tests/sessions/test_resume.py` with 8 tests: well-formed; bad line skipped; orphan tool_call truncated; token over budget triggers maybe_compact (mock LLM); time_gap inserted; time_gap not inserted at low gap; empty JSONL
- [ ] 17.10 Add `tests/sessions/test_cleanup.py` with 5 tests: expired removed; today's preserved; corresponding context dir removed; cross-session isolation; idempotent
- [ ] 17.11 Add `tests/sessions/test_id_migration.py` with 5 tests: single uuid4 renamed; multiple uuids unique new ids; collision _legacy suffix; empty uuid skipped; idempotent
- [ ] 17.12 Add `tests/integration/test_v08_e2e.py` with 3 end-to-end scenarios: (a) start fresh session → run short conversation → verify JSONL has all messages → verify notes file auto-created; (b) start fresh session → run conversation → exit → start with --resume → verify conversation continues from where it left off; (c) start with /resume on existing session → verify time_gap reminder when gap > threshold
- [ ] 17.13 Run full test suite (`pytest tests/`) and confirm v0.7 tests still pass; commit with clear message