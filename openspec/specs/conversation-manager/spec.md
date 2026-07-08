# conversation-manager Specification

## Purpose
Modifications to v0.7 `ConversationManager` to accept an optional `SessionArchiver` callback, enabling transparent JSONL append on every `add_*` call.

## Requirements

### Requirement: Optional archiver parameter
The system MUST extend `ConversationManager.__init__` to accept an optional `archiver: SessionArchiver | None = None` parameter. When provided, the archiver's `append(msg)` method is called automatically after every `add_*` operation. When None, behavior is identical to v0.7 (in-memory only).

#### Scenario: Backward compatibility preserved
- **WHEN** `ConversationManager()` is constructed without the `archiver` parameter
- **THEN** all `add_*` methods behave exactly as in v0.7
- **AND** no filesystem writes occur

#### Scenario: Archiver injected
- **WHEN** `ConversationManager(archiver=mock_archiver)` is constructed
- **THEN** every `add_user`, `add_assistant`, `add_message`, `add_tool_call`, `add_tool_result`, `add_turn` call invokes `mock_archiver.append(msg)` exactly once

### Requirement: set_messages does NOT invoke archiver
The system MUST NOT call `archiver.append()` from the v0.7 `set_messages(messages)` method. This method is a wholesale replacement (used by Layer-2 compaction in v0.7), not an incremental add — re-emitting all messages to the archiver would duplicate history. The system MUST document this in the method's docstring.

#### Scenario: set_messages is silent
- **WHEN** `conversation.set_messages([m1, m2, m3])` is called
- **THEN** the archiver is NOT called
- **AND** the in-memory message list is replaced
- **AND** the JSONL file is NOT modified by this call

#### Scenario: Compaction followed by set_messages
- **WHEN** v0.7 Layer-2 compaction produces `[summary_msg, post_compact_msg, ...tail]`
- **AND** `conversation.set_messages(new_msgs)` is called
- **THEN** the JSONL file does NOT receive the summary or post_compact messages
- **AND** the file remains a faithful record of the original add_* operations

### Requirement: Append failure is logged and swallowed
The system MUST catch any exception raised by `archiver.append()` inside each `add_*` method. The exception MUST be logged to stderr with the message ID and exception type. The caller MUST NOT receive the exception — the conversation continues normally even if disk writes fail.

#### Scenario: Disk full during append
- **WHEN** `archiver.append()` raises `OSError("No space left on device")`
- **AND** this happens inside `add_user("hello")`
- **THEN** the message IS added to the in-memory list
- **AND** a warning is logged `[archiver] append failed: OSError: No space left on device`
- **AND** the caller receives the same return value (the Message) as in v0.7
- **AND** subsequent `add_*` calls continue to attempt writes (no permanent failure flag)

### Requirement: Archiver is injected at ConversationManager construction, not at Agent construction
The system MUST require the archiver to be passed to `ConversationManager()` at construction time, NOT to be looked up from the Agent or App. This decouples the conversation layer from the Agent layer — the conversation manager can be used standalone (e.g., in tests) without an Agent instance.

#### Scenario: ConversationManager in test without Agent
- **WHEN** a unit test constructs `ConversationManager(archiver=mock_archiver)` directly
- **AND** calls `add_user("test")`
- **THEN** the mock archiver receives the call
- **AND** no Agent instance is needed

#### Scenario: ConversationManager in production with Agent
- **WHEN** `BaoZiCodeApp.__init__` constructs `ConversationManager(archiver=self.archiver)`
- **AND** passes the conversation manager to `Agent.__init__`
- **THEN** every Agent.run add_* call triggers the archiver transparently