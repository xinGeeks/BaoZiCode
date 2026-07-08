## 1. Configuration schema for context window and compaction

- [x] 1.1 In `baozicode/config/schema.py`, add `CompactionConfig` Pydantic model with fields: `per_block_threshold: int = 8192` (positive), `per_message_threshold: int = 20480` (positive), `recent_window_min_messages: int = 5` (≥1), `recent_window_tokens: int = 10000` (≥1), `reserve_tokens_auto: int = 13000` (≥0), `reserve_tokens_manual: int = 3000` (≥0), `max_summary_tokens: int = 2000` (≥100), `max_consecutive_failures: int = 3` (≥1)
- [x] 1.2 In `AgentConfig`, add `context_window_tokens: int = 128_000` (Field with `gt=0`) and `compaction: CompactionConfig = CompactionConfig()`
- [x] 1.3 In `BackendConfig`, add `context_window_tokens: int | None = None` (Field with `gt=0`); update default base_url comment block
- [x] 1.4 Add unit tests covering: full defaults load successfully, `agent.context_window_tokens: -1` rejected, `agent.compaction.per_block_threshold: 0` rejected, `BackendConfig.context_window_tokens = None` allowed, `BackendConfig.context_window_tokens: 0` rejected

## 2. ToolResultBlock offload metadata fields

- [x] 2.1 In `baozicode/llm/base.py`, locate `ToolResultBlock` dataclass and add two fields with defaults: `offloaded_to: Path | None = None` and `original_size: int = 0`; make sure the dataclass is `frozen=False` (or use `field(default=...)` if frozen)
- [x] 2.2 Audit every site that constructs `ToolResultBlock` (likely `baozicode/llm/{anthropic,openai,minimax,deepseek}.py` and `baozicode/mcp/adapter.py`); confirm they still work without specifying the new fields (defaults are fine)
- [x] 2.3 Confirm new fields are NOT included in LLM API payload serialization (only `tool_use_id`, `content`, `is_error` go on the wire) — check the `_to_anthropic` and `_to_openai` conversion paths
- [x] 2.4 Add a unit test that constructs a `ToolResultBlock(content="abc")` and asserts `offloaded_to is None` and `original_size == 0`; another test that constructs with `offloaded_to=Path("..."), original_size=51200` and asserts round-trip through `dataclasses.asdict` preserves both

## 3. Token estimator and context schema

- [x] 3.1 Create `baozicode/context/__init__.py` exposing public API: `maybe_compact`, `OffloadEngine`, `CompactEngine`, `CompactionError`, `estimate_messages_tokens`, `estimate_message_tokens`
- [x] 3.2 Create `baozicode/context/schema.py` with dataclasses: `CompactionResult { triggered: bool, tokens_before: int, tokens_after: int, compactions: int, last_compact_at: datetime | None }`, `ContextConfig { context_window_tokens: int, reserve_tokens: int, per_block_threshold: int, per_message_threshold: int, recent_window_min_messages: int, recent_window_tokens: int, max_summary_tokens: int, max_consecutive_failures: int }`, and `CompactionTelemetry` (compaction_count, total_tokens_saved, last_compact_at)
- [x] 3.3 Create `baozicode/context/estimator.py` with `estimate_message_tokens(message: Message) -> int` and `estimate_messages_tokens(messages: list[Message]) -> int`; rule: 4 tokens role overhead + 3 tokens per block + text blocks use `len(text) // 3` for ASCII-heavy / `len(text) * 3 // 5` for CJK-heavy (CJK ratio > 0.3) + ToolUseBlock adds `len(json.dumps(input)) // 3` + ToolResultBlock uses `original_size` if > 0 else `len(content.encode("utf-8"))`; pure-Python, no `tiktoken` import
- [x] 3.4 Unit tests for estimator: English 300-char user message → ~100 + role overhead; Chinese 300-char user message → ~180 + role overhead; ToolUseBlock with 600-char input → ~200 + role + block overhead; mixed multi-block message sums correctly; ToolResultBlock with `original_size=51200` uses original size not content

## 4. Disk storage for offloaded blocks

- [x] 4.1 Create `baozicode/context/storage.py` with `ContextStorage` class: `__init__(project_root: Path, session_id: str)`; `__init__` ensures `<project_root>/.baozicode/context/<session_id>/` exists and ensures `.baozicode/context/` line is in `<project_root>/.gitignore` (idempotent)
- [x] 4.2 Implement `write_block(tool_call_id: str, tool_name: str, content: str) -> Path` that writes `{"content": content, "offloaded_at": iso8601, "tool": tool_name, "tool_call_id": tool_call_id}` to `<session_id>/<tool>_<hash8>_<counter>.json` where `hash8 = sha1(content.encode("utf-8"))[:8]`; returns the relative project-root path (e.g., `.baozicode/context/<sess>/<file>.json`)
- [x] 4.3 Implement `cleanup() -> int` that removes every file under `<session_id>/`, then removes the empty directory; returns the count of files removed
- [x] 4.4 Implement `cleanup_all_sessions_except(keep_session_ids: set[str])` for power users (not used in v0.7 default path, but available for `/clear` if user wants)
- [x] 4.5 Unit tests: write_block creates correct path, file content is valid JSON with the four keys, hash8 is stable for same content, different content → different hash; cleanup removes all files in session but leaves other session dirs intact; gitignore is added idempotently (running twice does not duplicate the line)

## 5. Layer 1 offload engine

- [x] 5.1 Create `baozicode/context/layer1.py` with `OffloadEngine` class: `__init__(storage: ContextStorage, config: ContextConfig)`; exposes `offload(messages: list[Message]) -> list[Message]` that returns a new list with oversized `ToolResultBlock`s replaced by previews
- [x] 5.2 Implement `_offload_single_block(block: ToolResultBlock) -> ToolResultBlock`: if `offloaded_to is not None` return as-is (idempotent); compute `byte_len = len(block.content.encode("utf-8"))`; if `byte_len <= per_block_threshold` return as-is; else build preview `--- preview ({byte_len} bytes) ---\n<first 25 lines>\n... [N lines / M bytes omitted] ...\n<last 25 lines>\n--- offloaded to: <relpath> ---`, write to disk via storage, return new `ToolResultBlock` with `offloaded_to=relpath`, `original_size=byte_len`, `content=preview`
- [x] 5.3 Implement per-message aggregate offload: after per-block offload, sum `len(content.encode("utf-8"))` of all `ToolResultBlock`s in a `Message(role="tool")`; if sum > `per_message_threshold` and there are 2+ blocks not yet offloaded, sort them by `original_size` desc (or current `content` size if `original_size == 0`) and offload largest first until sum ≤ threshold or only 1 left
- [x] 5.4 Implement preview line splitting that handles content shorter than 50 lines (no separator needed), exactly 50 lines (head=tail), and longer (head 25 + separator + tail 25)
- [x] 5.5 Unit tests: single 50 KB block → offloaded with correct preview, `offloaded_to` and `original_size` set; 4 KB block → untouched; two 12 KB blocks in one message with 20K threshold → larger offloaded, smaller left; three 8 KB blocks in one message with 20K threshold → largest offloaded (sum drops to 16K); idempotent: running twice on same messages does not double-offload already-offloaded blocks

## 6. Layer 2 compact engine (summary + parser + circuit breaker)

- [x] 6.1 Create `baozicode/context/boundary.py` with two functions: `wrap_summary_message(summary_text: str) -> str` returning `<system-reminder type="context_summary" ttl="sticky">\n{summary_text}\n</system-reminder>`; `post_compaction_reminder() -> str` returning `<system-reminder type="post_compaction">需要文件细节时请用 Read/Grep/Bash 重新调用对应工具,不要根据摘要脑补代码或路径</system-reminder>`
- [x] 6.2 Create `baozicode/context/layer2.py` with `CompactEngine` class: `__init__(llm: LLMClient, config: ContextConfig, telemetry: CompactionTelemetry)`; exposes `async compact(messages: list[Message]) -> list[Message]`
- [x] 6.3 Implement `_partition_tail(messages: list[Message]) -> tuple[list[Message], list[Message]]` returning `(head, tail)`; take from the end moving backward until BOTH `recent_window_min_messages` AND `recent_window_tokens` are met; if a single message already exceeds `recent_window_tokens`, tail is just that one message (token threshold dominates, message count is "satisfied" for that branch)
- [x] 6.4 Implement `_build_summary_prompt(head: list[Message]) -> str` containing: (a) explicit "DO NOT call any tools" instruction; (b) the `---ANALYSIS---` and `---SUMMARY---` delimiter instructions; (c) the six section headers with one-line descriptions (`## Goal / ## Progress / ## Decisions / ## Files / ## Open Issues / ## Next`); (d) instruction to keep total ≤ `max_summary_tokens`; (e) the head messages serialized (use existing `Message.to_text()` or similar)
- [x] 6.5 Implement `_call_summary_llm(prompt: str) -> str` that calls `llm.stream(messages=[Message(role="user", content=prompt)], system="You are a context-compaction assistant. Never call tools.", tools=[], cache_breakpoints=None)` and returns the concatenated text; raises `CompactionError` if the call raises
- [x] 6.6 Implement `_parse_summary(text: str) -> str`: extract content between `---SUMMARY---` and `---END_SUMMARY---` (or end-of-string if `---END_SUMMARY---` is missing); discard everything else; return `None` if `---SUMMARY---` is missing
- [x] 6.7 Implement circuit breaker: track `_consecutive_failures: int = 0`; on success, reset to 0; on failure (any of: `stream()` exception, returned text < 50 chars, parser returns `None`, post-summary estimated tokens still > `context_window - reserve`), increment and if ≥ `max_consecutive_failures` raise `CompactionError("compaction failed after N attempts")`; otherwise retry the LLM call once
- [x] 6.8 Implement `compact()` orchestrator: partition → build prompt → call LLM (with retry per circuit breaker) → parse → on success wrap as `Message(role="user", content=wrap_summary_message(parsed))` + append `Message(role="user", content=post_compaction_reminder())` + tail messages; on final failure raise `CompactionError`
- [x] 6.9 Update telemetry after every successful compaction: increment `compaction_count`, add `tokens_before - tokens_after` to `total_tokens_saved`, set `last_compact_at = utcnow()`
- [x] 6.10 Unit tests: partition respects both thresholds (5-message case, single-15K-message case, 4-message-needs-more-for-tokens case); parser handles well-formed, missing END_SUMMARY, missing SUMMARY; circuit breaker increments and resets; compact produces expected [summary, post_compaction, *tail] message structure

## 7. maybe_compact orchestrator

- [x] 7.1 In `baozicode/context/__init__.py`, add `maybe_compact(messages: list[Message], *, trigger: Literal["auto", "manual"], ctx: AgentContext) -> tuple[list[Message], CompactionResult]` that wires Layer 1 + (conditional) Layer 2: always run Layer 1 offload, then estimate tokens, then if > `context_window - reserve_tokens` (where `reserve_tokens = ctx.config.agent.compaction.reserve_tokens_{trigger}`) run Layer 2
- [x] 7.2 On `CompactError` from Layer 2, return messages unchanged with `result.triggered = False` and a flag/exception that the Agent loop can catch and convert to `StopReason.COMPACTION_FAILED`
- [x] 7.3 Unit tests: small messages → no compaction, just Layer 1 ran; large messages → Layer 2 ran and reduced token count; Layer 2 raises CompactionError → result is "no compaction" + error code; manual trigger uses 3K reserve not 13K

## 8. Agent loop integration

- [x] 8.1 In `baozicode/agent/events.py`, add `COMPACTION_FAILED = "compaction_failed"` to the `StopReason` enum (between `STREAM_ERROR` and any future values)
- [x] 8.2 In `baozicode/agent/loop.py`, add `self._compact_requested: bool = False` to `Agent.__init__`; add public method `request_compact()` that sets the flag from a non-async caller (TUI)
- [x] 8.3 In the main `Agent.run` loop, at the top of every iteration (BEFORE any LLM call), call `_check_compact_request()`: if flag set, cancel current stream task if any, run `maybe_compact(self._messages, trigger="manual", ctx=self._ctx)`, clear the flag, and restart the iteration from the top (do not advance iteration counter)
- [x] 8.4 At the top of the iteration (BEFORE the user's `_check_compact_request` check), call `maybe_compact(self._messages, trigger="auto", ctx=self._ctx)` to handle automatic token-budget-driven compaction
- [x] 8.5 Wrap the `llm.stream()` call so that if it raises `CompactionError`, the Agent loop yields `AgentEvent.error("compaction failed after 3 attempts")` and a final `AgentEvent.done(reason=StopReason.COMPACTION_FAILED)`
- [x] 8.6 Update `Agent.__init__` to accept the new context window resolution: `effective_context_window = backend_config.context_window_tokens or agent_config.context_window_tokens`; pass `ctx` with this and the `CompactionConfig` into the loop
- [x] 8.7 Unit tests: Agent iteration calls maybe_compact auto-trigger; request_compact mid-iteration cancels stream and re-runs with trigger="manual"; CompactError during auto-trigger yields done with COMPACTION_FAILED; idempotent (Layer 1 offload doesn't re-offload already-offloaded blocks)

## 9. TUI integration (/compact slash command + /status + /clear)

- [x] 9.1 In `baozicode/tui/chat_screen.py`, register `/compact` in the slash command dispatcher; route to `_handle_compact()`; also list it in the `/help` text
- [x] 9.2 Implement `_handle_compact()`: if `self.app.current_agent` is None or not running, call `await self.app.run_compact_now()`; if Agent is running, call `self.app.current_agent.request_compact()`; in both cases, print a one-line status message ("已请求压缩..." or "已压缩: X → Y tokens")
- [x] 9.3 In `baozicode/app.py`, add `async run_compact_now()` that constructs a temporary context with the current messages and calls `maybe_compact(trigger="manual", ctx=ctx)`; updates `self._compaction_telemetry`
- [x] 9.4 In `_handle_clear` (existing `/clear` handler), also call `self._context_storage.cleanup()` to delete all files in the current session's `.baozicode/context/` dir
- [x] 9.5 In `BaoZiCodeApp.on_unmount` (or equivalent shutdown path), call `self._context_storage.cleanup()` before `mcp_manager.shutdown()` so offload files don't leak across sessions
- [x] 9.6 In `_handle_status` (existing `/status` handler), append a "compression" subsection when `self._compaction_telemetry.compactions > 0`: lines `compactions: N`, `tokens_saved: M`, `last_compact: <iso8601>`; omit entirely when compactions == 0
- [x] 9.7 Add `_clear_partial_cards()` helper that removes any text/tool_call/tool_result cards emitted by the current in-flight stream (so a cancelled /compact doesn't leave ghost cards on screen); call it from `_handle_compact` before signalling the Agent
- [x] 9.8 Unit tests: slash dispatch routes /compact to handler; handler calls app.run_compact_now() when idle; handler calls agent.request_compact() when running; /status includes compression subsection when telemetry.compactions > 0; /clear calls storage.cleanup

## 10. README, config example, and integration tests

- [x] 10.1 Update `README.md` "当前版本" section to mention v0.7 two-layer compression; add a new "## 上下文管理 (v0.7)" section explaining Layer 1 (per-block / per-message offload with disk preview) and Layer 2 (LLM-generated 6-section summary with circuit breaker), plus the `/compact` manual trigger
- [x] 10.2 Add `config.example.yaml` snippet under a new comment block showing `agent.context_window_tokens: 128000` and the full `agent.compaction:` block with each field and its default
- [x] 10.3 Create `tests/context/__init__.py` and `tests/context/conftest.py` with shared fixtures: `tmp_project_root` (autouse `tmp_path`), `context_storage` (builds ContextStorage with random session_id), `context_config` (defaults), `compaction_telemetry` (fresh)
- [x] 10.4 Add `tests/context/test_estimator.py` with 8 tests covering English / Chinese / ToolUseBlock / mixed blocks / ToolResultBlock original_size preference
- [x] 10.5 Add `tests/context/test_storage.py` with 6 tests covering write / cleanup / gitignore / hash stability / other-session-isolation
- [x] 10.6 Add `tests/context/test_layer1.py` with 6 tests covering single-block / below-threshold / aggregate / idempotency / preview-format
- [x] 10.7 Add `tests/context/test_layer2.py` with 8 tests using a mock LLMClient: well-formed summary / missing END_SUMMARY / missing SUMMARY / tail-window-three-scenarios / circuit-breaker-three-failures / circuit-breaker-reset-on-success / compaction-result-message-structure / telemetry-update
- [x] 10.8 Add `tests/context/test_maybe_compact.py` with 4 tests covering: small-messages-no-compaction / large-messages-Layer2-runs / Layer2-raises-error-propagates / manual-trigger-3K-reserve
- [x] 10.9 Add `tests/context/test_agent_loop_integration.py` with 5 tests: Agent auto-triggers Layer 1 only at low token count, Agent auto-triggers Layer 2 at high token count, _compact_requested mid-iteration cancels and re-runs, CompactError yields done with COMPACTION_FAILED, /clear + on_unmount call storage.cleanup
- [x] 10.10 Run the full test suite (`pytest tests/`) and confirm v0.6 tests still pass; commit the change with a clear message
