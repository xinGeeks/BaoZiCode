## ADDED Requirements

### Requirement: Sub-agent metadata in lifecycle event payloads
When a lifecycle event is fired by a sub-Agent (one created via
`SubAgentManager.dispatch(type=..., role=..., ...)`), the event payload
MUST include a `subagent` field with the following shape:

```python
{
    "task_id": str,       # e.g. "task-20260709-153022-abc1"
    "role": str | None,   # definition-mode role name; fork mode None
    "type": Literal["definition", "fork"],
    "depth": int,         # always 1 in v1.2 (L1 global deny "task" prevents nesting)
}
```

For events fired by the MAIN Agent, the `subagent` field is either absent
or set to `None` (implementation choice — both are acceptable). The
presence of the field is the source of truth for "is this a sub-agent
event?"

Existing hooks (v1.1, v1.1.1, v1.2 hooks-polish) that do not read the
`subagent` field MUST continue to work unchanged. The new field is
purely additive.

#### Scenario: Sub-agent tool.pre has subagent field
- **WHEN** a sub-Agent runs and calls the `Read` tool
- **THEN** the `tool.pre` event payload (when it's a dict) includes
  `payload["subagent"] = {"task_id": "task-...", "role": "explorer",
  "type": "definition", "depth": 1}`

#### Scenario: Main Agent tool.pre does not have subagent field
- **WHEN** the main Agent runs and calls the `Read` tool
- **THEN** the `tool.pre` event payload does NOT have a `subagent` key
  (or has it as `None` — implementation choice)

#### Scenario: Sub-agent session.start has subagent field
- **WHEN** a sub-Agent's `Agent.run` begins
- **THEN** the `session.start` event payload includes
  `payload["subagent"]["type"] = "definition"` (or `"fork"`)
- **AND** `payload["subagent"]["task_id"]` matches the sub-Agent's
  TaskInfo.task_id

#### Scenario: Sub-agent session.end has subagent field
- **WHEN** a sub-Agent's `Agent.run` exits
- **THEN** the `session.end` event payload includes the same `subagent`
  field as `session.start`
- **AND** the `subagent` field has not changed (task_id / role / type /
  depth are immutable for the sub-Agent's lifetime)

#### Scenario: Sub-agent turn events have subagent field
- **WHEN** a sub-Agent runs an iteration
- **THEN** both `turn.start` and `turn.end` event payloads include the
  `subagent` field with consistent values

#### Scenario: Old hook ignores subagent field
- **WHEN** a v1.1 hook handler reads `payload["tool_call_id"]` and
  does NOT read `payload["subagent"]`
- **AND** the event is fired by a sub-Agent
- **THEN** the hook handler still works correctly
- **AND** the extra `subagent` field is ignored

#### Scenario: New hook filters by subagent role
- **WHEN** a v1.2 hook handler reads
  `payload.get("subagent", {}).get("role")` to filter
- **AND** the event is fired by the main Agent
- **THEN** `payload.get("subagent", {}).get("role")` returns `None`
  (or `.get` returns None via the empty-dict default)
- **AND** the hook can use `if subagent_role == "explorer"` to
  filter sub-agent events

#### Scenario: Audit log records subagent field
- **WHEN** `audit_log_path` is configured
- **AND** a sub-Agent fires a `tool.post` event with `subagent` field
- **THEN** the audit log entry includes the `subagent` dict verbatim
- **AND** filtering audit log by `subagent.task_id` shows all events
  for that sub-Agent

#### Scenario: HookAuditLog does not split per-task
- **WHEN** multiple sub-Agents run concurrently
- **THEN** all their events are written to the SAME audit log file
  (`<session>.audit.jsonl`)
- **AND** events are distinguished by the `subagent.task_id` field
  (NOT by separate files)
