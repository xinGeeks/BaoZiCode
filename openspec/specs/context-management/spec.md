# context-management Specification (v0.7)

## Purpose
Two-layer token-budget compression for the Agent loop, so long-running sessions do not crash from context overflow.

## Requirements

### Requirement: Per-tool-result offload to disk
The system MUST walk every `Message` in the conversation list before each LLM call. For each `ToolResultBlock`, if `len(content.encode("utf-8")) > per_block_threshold` (default 8192 bytes), the system MUST write the full content to `<project_root>/.baozicode/context/<session_id>/<block_id>.json`, and replace the block's `content` with a preview string that includes (a) the original size in bytes, (b) the first 25 lines, (c) a separator noting how many lines / bytes were omitted, (d) the last 25 lines, and (e) the offload file path. The block's `offloaded_to` field MUST be set to the relative project-root path of the offload file, and `original_size` MUST be set to the original byte length.

#### Scenario: Single oversized block offloaded
- **WHEN** a `ToolResultBlock.content` is 50 KB (a Read of a large file)
- **THEN** the full content is written to `.baozicode/context/<session>/<id>.json`
- **AND** the block's `content` is replaced with a preview containing first 25 + last 25 lines + the disk path
- **AND** `offloaded_to` equals the disk path
- **AND** `original_size` equals 51200

#### Scenario: Block at or below threshold left intact
- **WHEN** a `ToolResultBlock.content` is 4 KB
- **THEN** no offload occurs
- **AND** `offloaded_to` remains `None`
- **AND** the content is sent to the LLM unchanged

#### Scenario: Idempotent re-application
- **WHEN** Layer 1 offload runs a second time on the same conversation (next iteration)
- **THEN** blocks with `offloaded_to` set MUST be skipped (already on disk)
- **AND** the preview content from the first pass is treated as the new content for size thresholding

### Requirement: Per-message aggregate offload
The system MUST, after per-block offload, sum the `len(content.encode("utf-8"))` of all `ToolResultBlock`s in a single `Message(role="tool", content=[...])`. If the sum exceeds `per_message_threshold` (default 20480 bytes), the system MUST sort the blocks by `original_size` (or current `content` size if not previously offloaded) descending and offload the largest ones first until the message sum is at or below `per_message_threshold`.

#### Scenario: Two 12K blocks in one message
- **WHEN** a tool message contains two `ToolResultBlock`s, each 12 KB, and `per_message_threshold=20K`
- **THEN** the larger of the two (or the first if equal) is offloaded
- **AND** the remaining block is left intact
- **AND** the message sum is ≤ 12 KB after offload

#### Scenario: All blocks offloaded when sum exceeds threshold
- **WHEN** a tool message contains three blocks of 8 KB each, total 24 KB, threshold 20 KB
- **THEN** the largest block is offloaded first (sum drops to 16 KB, below threshold)
- **AND** the offload stops at that point

### Requirement: Token-budget-driven summary fallback
The system MUST, after Layer 1 offload, estimate the total token count of the resulting message list using `estimator.estimate_messages()`. If the estimate exceeds `context_window_tokens - reserve_tokens`, the system MUST trigger Layer 2 summary. The default `reserve_tokens` MUST be `13000` for automatic triggers and `3000` for manual triggers (e.g., via the `/compact` slash command).

#### Scenario: Auto trigger when over budget
- **WHEN** estimated message tokens equal `context_window_tokens - 10000` (i.e., within 10 K of budget, below 13 K reserve)
- **THEN** Layer 1 runs but Layer 2 does NOT trigger
- **AND** the next LLM call uses the Layer 1 result

#### Scenario: Auto trigger when budget exceeded
- **WHEN** estimated message tokens equal `context_window_tokens + 5000`
- **THEN** Layer 1 runs first
- **AND** Layer 2 runs to produce a summary message
- **AND** the summary replaces the older portion of history

#### Scenario: Manual trigger uses tighter reserve
- **WHEN** the user invokes `/compact` and current tokens are at `context_window_tokens - 5000`
- **THEN** Layer 2 triggers (5000 > 3000 reserve)
- **AND** `reserve_tokens` is `3000` for this invocation

### Requirement: Summary preserves tail window
The system MUST, before generating the summary, partition the message list into a `head` (oldest messages) and a `tail` (most recent messages). The `tail` MUST contain at least `recent_window_min_messages` (default 5) messages AND at least `recent_window_tokens` (default 10000) tokens, whichever is larger. Messages MUST be taken from the end of the list moving backward until both thresholds are met.

#### Scenario: Tail meets both thresholds
- **WHEN** the last 5 messages contain 12 K tokens
- **THEN** the tail is those 5 messages (12 K ≥ 10 K token minimum, 5 ≥ 5 message minimum)
- **AND** the head contains all earlier messages

#### Scenario: Tail needs more messages for token minimum
- **WHEN** the last 5 messages contain only 4 K tokens
- **THEN** the system takes additional messages from the end moving backward until token count ≥ 10 K
- **AND** the message-count minimum (5) is already met

#### Scenario: Long single message dominates tail
- **WHEN** the last message alone is 15 K tokens (e.g., a massive Read result)
- **THEN** the tail contains just that single message (15 K ≥ 10 K)
- **AND** the head contains all earlier messages
- **AND** the minimum message count is treated as satisfied (since the token minimum dominates)

### Requirement: Summary prompt structure and tool prohibition
The system MUST build a summary prompt that explicitly forbids the LLM from invoking any tools during summarization, instructs the model to first write analysis between `---ANALYSIS---` and `---END_ANALYSIS---` delimiters, then write the structured summary between `---SUMMARY---` and `---END_SUMMARY---` delimiters. The summary MUST contain exactly these six section headers in this order: `## Goal`, `## Progress`, `## Decisions`, `## Files`, `## Open Issues`, `## Next`. The summary MUST be ≤ 2000 tokens. The prompt MUST be sent using `tools=[]` (no tool definitions) to the LLM.

#### Scenario: Prompt contains all required elements
- **WHEN** the summary prompt is constructed
- **THEN** it contains the string "DO NOT call any tools"
- **AND** it contains the literal `---ANALYSIS---` and `---SUMMARY---` delimiters
- **AND** it lists all six section headers with one-line descriptions

#### Scenario: Summary call has no tools
- **WHEN** the summary LLM request is sent
- **THEN** the `tools` parameter of `LLMClient.stream()` is `[]`
- **AND** the LLM cannot emit any `tool_use` blocks

### Requirement: Summary parsing strips analysis
The system MUST parse the LLM's summary response by extracting only the content between `---SUMMARY---` and `---END_SUMMARY---` (or end-of-string if `---END_SUMMARY---` is missing). Any content before `---SUMMARY---` or in the `---ANALYSIS---` block MUST be discarded.

#### Scenario: Well-formed response parsed correctly
- **WHEN** LLM returns text containing `---ANALYSIS---thinking...---END_ANALYSIS---` followed by `---SUMMARY---## Goal\n...---END_SUMMARY---`
- **THEN** the parsed summary contains only the content after `---SUMMARY---` and before `---END_SUMMARY---`
- **AND** the analysis block is discarded

#### Scenario: Missing END_SUMMARY marker handled
- **WHEN** LLM returns text with `---SUMMARY---` but no closing `---END_SUMMARY---` (model truncation)
- **THEN** the parser takes everything from `---SUMMARY---` to end of string
- **AND** the partial summary is still usable

#### Scenario: Missing SUMMARY delimiter is failure
- **WHEN** LLM returns text without `---SUMMARY---` delimiter
- **THEN** the parser returns `None`
- **AND** the failure is recorded toward the consecutive-failure counter

### Requirement: Three-strike circuit breaker
The system MUST track consecutive summary failures. A failure is any of: (a) `LLMClient.stream()` raises an exception, (b) response text is empty or shorter than 50 characters, (c) `---SUMMARY---` delimiter is not found, (d) post-summary estimated token count still exceeds budget. When the counter reaches 3, the system MUST raise `CompactError` and terminate the Agent loop with `StopReason.COMPACTION_FAILED`. The counter MUST reset to 0 on a successful summary.

#### Scenario: First failure increments counter
- **WHEN** the first summary attempt fails (missing delimiter)
- **THEN** the counter is 1
- **AND** the system retries with a fresh LLM call

#### Scenario: Three consecutive failures trigger breaker
- **WHEN** three consecutive summary attempts fail
- **THEN** `CompactError` is raised
- **AND** Agent loop yields `AgentEvent.error("compaction failed after 3 attempts")`
- **AND** `done.reason` equals `StopReason.COMPACTION_FAILED`

#### Scenario: Success resets counter
- **WHEN** a summary attempt fails then the next attempt succeeds
- **THEN** the counter is reset to 0
- **AND** the next summary cycle starts fresh

### Requirement: Post-summary message replacement and boundary reminder
After a successful summary, the system MUST replace all messages in the `head` partition with a single new `Message` of role `user`, content = the parsed summary text wrapped in `<system-reminder type="context_summary" ttl="sticky">...</system-reminder>`. The system MUST also append a follow-up `Message` of role `user`, content = `<system-reminder type="post_compaction">需要文件细节时请用 Read/Grep/Bash 重新调用对应工具,不要根据摘要脑补代码或路径</system-reminder>`. The `tail` messages MUST be appended unchanged after these two new messages.

#### Scenario: Head replaced by summary message
- **WHEN** the head contains 8 messages and a summary is generated
- **THEN** the resulting message list contains exactly: [user_summary_msg, post_compaction_msg, *tail_messages]
- **AND** the 8 original head messages are gone

#### Scenario: Summary is user-role wrapped in system_reminder
- **WHEN** the summary message is constructed
- **THEN** its role is `user` (not `assistant`)
- **AND** its content is wrapped between `<system-reminder type="context_summary" ttl="sticky">` and `</system-reminder>` tags

#### Scenario: Post-compaction reminder appended
- **WHEN** the summary is appended to the new message list
- **THEN** a follow-up message with `<system-reminder type="post_compaction">` is present immediately after the summary
- **AND** the reminder text mentions "重新调用" or equivalent (instruction to re-call tools)

### Requirement: Storage layout and session scoping
The system MUST write offload files to `<project_root>/.baozicode/context/<session_id>/<block_id>.json`. `<session_id>` MUST be a UUID generated at Agent construction. `<block_id>` MUST be a content hash or counter that uniquely identifies the block within the session. The system MUST add `.baozicode/context/` to `.gitignore` automatically if not already present.

#### Scenario: Files written to project-local context dir
- **WHEN** offload runs for session `abc123`
- **THEN** files are created under `<project_root>/.baozicode/context/abc123/`
- **AND** the project root is the same one used by other `.baozicode/` directories (e.g., permissions)

#### Scenario: Gitignore updated
- **WHEN** offload runs and `.gitignore` does not yet contain `.baozicode/context/`
- **THEN** the line `.baozicode/context/` is appended
- **AND** the change is idempotent (running again does not duplicate the line)

### Requirement: Cleanup on session end and /clear
The system MUST delete all files under `<project_root>/.baozicode/context/<session_id>/` when either (a) the Agent loop terminates (any StopReason), or (b) the user invokes `/clear` while this session is active. Files belonging to other sessions MUST NOT be deleted.

#### Scenario: Cleanup on Agent loop termination
- **WHEN** the Agent loop yields `done` with any StopReason
- **THEN** all files in `<project>/.baozicode/context/<this_session>/` are deleted
- **AND** the directory itself is removed if empty

#### Scenario: Cleanup on /clear
- **WHEN** user types `/clear`
- **THEN** all files in `<project>/.baozicode/context/<this_session>/` are deleted before clearing conversation
- **AND** the conversation history is cleared per the existing interactive-tui spec

#### Scenario: Other sessions preserved
- **WHEN** session `abc123` terminates and files exist under session `xyz789/`
- **THEN** only `abc123/` files are deleted
- **AND** `xyz789/` files remain untouched

### Requirement: Manual /compact trigger via slash command
The system MUST register a `/compact` slash command in the TUI slash dispatcher. Invoking `/compact` MUST set the Agent's `_compact_requested` flag. The Agent loop MUST check this flag immediately before each `llm.stream()` call (i.e., at the top of every iteration) and, if set, interrupt the current iteration, run `maybe_compact(trigger="manual")`, clear the flag, and resume from the same iteration with the new message list.

#### Scenario: /compact during idle
- **WHEN** user types `/compact` while no Agent.run() is active
- **THEN** the system runs compact immediately with `trigger="manual"`
- **AND** no Agent loop state changes (since none is active)

#### Scenario: /compact mid-iteration
- **WHEN** user types `/compact` while Agent.run() is iterating
- **THEN** on the next `llm.stream()` entry point, the flag is detected
- **AND** the current iteration's stream is cancelled before sending
- **AND** compact runs with `trigger="manual"`
- **AND** the iteration restarts from the top with the compacted messages

#### Scenario: /compact slash command surfaced in help
- **WHEN** user types `/help`
- **THEN** the help text mentions `/compact` with a one-line description

### Requirement: Context window configuration
The system MUST expose `agent.context_window_tokens: int = 128_000` in `AppConfig` (default 128 K) and `backend.context_window_tokens: int | None = None` on each `BackendConfig` (None means "fall back to `agent.context_window_tokens`"). The effective context window MUST be computed at Agent construction as: `backend.context_window_tokens if set, else agent.context_window_tokens`. The effective value MUST be passed to `maybe_compact()` for every iteration.

#### Scenario: Backend override takes precedence
- **WHEN** `agent.context_window_tokens = 200_000` and `anthropic.context_window_tokens = 128_000`
- **THEN** the effective context window for Anthropic is 128 K
- **AND** for OpenAI (no override) it is 200 K

#### Scenario: No override falls back to global
- **WHEN** `agent.context_window_tokens = 200_000` and no backend has an override
- **THEN** the effective context window is 200 K for all backends

#### Scenario: Schema validation rejects negative
- **WHEN** user sets `agent.context_window_tokens: -1` in YAML
- **THEN** Pydantic validation fails with a clear error message
- **AND** the system does not start

### Requirement: Token estimator with Chinese weighting
The system MUST provide `estimator.estimate_tokens(message: Message) -> int` that computes a token estimate using: (a) a fixed role-based overhead per message (4 tokens), (b) a fixed overhead per content block (3 tokens), (c) for `str` content, `len(content) // 3` for ASCII-heavy text or `len(content) * 3 // 5` for Chinese-heavy text (detected by CJK character ratio > 0.3), (d) for `ToolUseBlock`, an additional `len(json.dumps(input)) // 3` for the input JSON, (e) for `ToolResultBlock`, the same estimator applied to `content`. The function MUST have no external dependencies (no tiktoken).

#### Scenario: English text estimated
- **WHEN** a user message contains 300 ASCII characters
- **THEN** `estimate_tokens` returns approximately 100 (300 // 3) plus role overhead

#### Scenario: Chinese text weighted heavier
- **WHEN** a user message contains 300 CJK characters (>30% of total)
- **THEN** `estimate_tokens` returns approximately 180 (300 * 3 // 5) plus role overhead

#### Scenario: ToolUseBlock includes input JSON
- **WHEN** an assistant message contains a `ToolUseBlock` with `input` containing 600 characters
- **THEN** `estimate_tokens` returns the input estimate (600 // 3 = 200) plus role and block overhead

#### Scenario: Mixed-content message
- **WHEN** a message contains multiple blocks of mixed types
- **THEN** the estimate is the sum of per-block estimates plus role overhead
- **AND** no double-counting occurs

### Requirement: Compaction telemetry surfaced in /status
The system MUST extend `/status` output to display, when a compaction session is active: total compaction events in this session, total tokens saved (sum of `tokens_before - tokens_after` across all compactions), and last compaction timestamp.

#### Scenario: /status shows compaction stats after first compact
- **WHEN** user runs `/status` after the first Layer 2 summary has fired
- **THEN** the status output includes a line like `compactions: 1` and `tokens_saved: 45000`

#### Scenario: /status omits compaction stats when none happened
- **WHEN** user runs `/status` in a fresh session with no compactions
- **THEN** the status output omits the compaction stats block