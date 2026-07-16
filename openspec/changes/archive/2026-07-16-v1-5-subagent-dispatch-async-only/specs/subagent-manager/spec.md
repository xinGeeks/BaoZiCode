## ADDED Requirements

### Requirement: dispatch async_=False is removed

`SubAgentManager.dispatch()` MUST raise `NotImplementedError` (not return a Task, not hang) when called with `async_=False`. The error message SHALL explain that `async_=True` is the only supported path from v1.5 onwards and point callers to poll `task.state` or listen for idle notifications.

#### Scenario: async_=False raises NotImplementedError
- **WHEN** `SubAgentManager.dispatch(type=..., role=..., prompt=..., async_=False)` is invoked
- **THEN** it raises `NotImplementedError` immediately (not a `ToolResult`, not a Task, not a hang)

#### Scenario: async_=True still returns task_id
- **WHEN** `SubAgentManager.dispatch(type=..., role=..., prompt=..., async_=True)` is invoked
- **THEN** it returns a `str` task_id and the sub-Agent runs in the background

### Requirement: _dispatch_sync_blocking helper is deleted

The `_dispatch_sync_blocking` method MUST be removed from `SubAgentManager`. Any reference to it (in `dispatch()`, in `task_executor`, in docs) MUST be deleted or updated to point to the `async_=True` path.

#### Scenario: Method no longer exists
- **WHEN** `SubAgentManager` is imported and inspected
- **THEN** `_dispatch_sync_blocking` is NOT a member of the class