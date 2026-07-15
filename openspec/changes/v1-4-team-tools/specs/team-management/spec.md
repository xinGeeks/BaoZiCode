# Team Management — Collaboration Tools Delta

This delta adds 4 new requirements to `team-management`: shared task
model, approval protocol, mailbox notifier, role visibility scoping. All
build on top of foundation requirements landed by
`v1-4-team-foundation`.

## ADDED Requirements

### Requirement: Task dataclass + tasks.jsonl schema

`Team.MAILBOX` MUST be extended with a second file:

```
<teams_dir>/<team_name>/tasks.jsonl
```

`Task` MUST be a frozen dataclass with fields:

- `id: str` — unique 8-character hex token; stable for lifetime of the task
- `body: str` — free-form task description (may be multi-line)
- `status: Literal["pending","ready","in_progress","done","failed","canceled"]`
- `depends_on: tuple[str, ...]` — tuple of task ids that must complete before this task is ready (empty tuple = no dependencies)
- `assignee: str | None` — member name assigned; `None` = unassigned
- `created_at: datetime` — UTC, set at append time
- `started_at: datetime | None` — UTC of first transition to `in_progress`
- `completed_at: datetime | None` — UTC of transition to terminal status
- `error: str | None` — reason, only populated when `status == "failed"`

A separate `tasks.jsonl` lockfile MUST be used (`<team_dir>/.tasks.lock`),
distinct from per-member mailbox locks, so Lead LLM decisions on
`tasks.jsonl` are not blocked by member inbox writes.

#### Scenario: Task ID uniqueness
- **WHEN** `Tasks.append(team_dir, task1_with_id="3f4a7c01")` +
  `Tasks.append(team_dir, task2_with_id="3f4a7c01")`
- **THEN** the second append succeeds writing the task (id uniqueness is
  the LLM's contract — no auto-dedup)
- **AND** `Tasks.read_all(team_dir)` returns 2 entries with the same id
  (Lead LLM is expected to generate unique ids via `secrets.token_hex(4)`)

#### Scenario: Atomic append with stale-lock protection
- **WHEN** Lead LLM calls `Tasks.append` while another process holds
  `.tasks.lock` (mtime 35s ago, default `stale_seconds=30`)
- **THEN** the lock is stolen (delete + recreate + take) and the append
  succeeds without raising `MailboxLockTimeout`

#### Scenario: read_all skip bad lines
- **WHEN** `tasks.jsonl` contains a corrupted line (mid-line kill)
- **THEN** `Tasks.read_all(team_dir)` returns the remaining valid tasks;
  the corrupted line is logged as warning but NOT raised

### Requirement: Tasks.update_status uses read-modify-replace under lock

`Tasks.update_status` MUST serialize status mutations under
`<team_dir>/.tasks.lock` and MUST atomically rewrite the entire
`tasks.jsonl` file (write-then-rename within the lock), specifically
for the call signature
`Tasks.update_status(team_dir, task_id, new_status, *,
assignee=None, error=None) -> bool`:

1. Acquire `tasks.lock`
2. Read all tasks via `Tasks.read_all`
3. Find the matching task by id (linear scan — N typically < 20)
4. Update fields: `status`, `assignee` if provided, `error` if provided,
   `started_at=now` on first transition to `in_progress`,
   `completed_at=now` on terminal transition
5. Atomically rewrite the entire `tasks.jsonl` file (write-then-rename)
6. Release lock

If multiple matching ids exist, the FIRST occurrence MUST be updated and a
warning logged. Return True if updated, False if no match found.

#### Scenario: Update sets started_at only on first transition
- **WHEN** `task.status == "in_progress"` and
  `Tasks.update_status(..., "running")` is called again
- **THEN** the call returns True but `started_at` is NOT overwritten
  (kept as the original transition)

#### Scenario: Concurrent update is serialized
- **WHEN** process A and process B both call `Tasks.update_status`
  for different tasks (or the same task)
- **THEN** the lockfile serializes them; the second waits for the first
- **AND** the final `tasks.jsonl` reflects both updates without losing
  data (one appears after the other, never overwritten by the other)

### Requirement: Tasks.find_ready topological gating

`Tasks.find_ready(team_dir) -> list[str]` MUST return the ids of all tasks
where:

- `task.status == "pending"`
- AND every task in `task.depends_on` is in `{status: done}` or
  `{status: skipped}` (terminal-success states)

Tasks whose dependencies are not yet satisfied MUST NOT appear.

#### Scenario: All deps done → ready
- **WHEN** task t-002 has `depends_on=[t-001]` and t-001 has
  `status=done`
- **THEN** `find_ready()` includes t-002 in its result (after also
  updating t-002.status → "ready")

#### Scenario: One dep failed blocks
- **WHEN** task t-002 has `depends_on=[t-001, t-003]` and t-001 is `done`
  but t-003 is `failed`
- **THEN** `find_ready()` does NOT include t-002 (a failed dep is a
  blocker, not a "passed" dep)

#### Scenario: Self-dependency rejected at creation
- **WHEN** Lead LLM calls `team_task_create` with `depends_on`
  containing the new task's own id (cycle)
- **THEN** creation MUST fail with `TaskCycleError` and no entry written
  to `tasks.jsonl`

### Requirement: Approval protocol via mailbox

The team MUST support a uniform PLAN/APPROVE/REJECT communication
protocol carried entirely through the existing `Mailbox` infrastructure
(no new files, no new locks).

**PLAN messages** (member → Lead, written to member's `outbox.jsonl`):
```
---
Plan body (any text).
---
```
The block MUST be delimited by `---PLAN-<plan_id>---` on its own line at
the start and `---END---` on its own line at the end. `<plan_id>` is an
8-character hex chosen by the member; subsequent `APPROVED: <plan_id>`
or `REJECTED: <plan_id>` references MUST match.

**APPROVED messages** (Lead → member, written to member's `inbox.jsonl`):
```
APPROVED: <plan_id>
```
On its own line; `<plan_id>` references a PLAN the member wrote.

**REJECTED messages** (Lead → member, written to member's `inbox.jsonl`):
```
REJECTED: <plan_id> <reason text>
```
On its own line; `<reason>` is mandatory non-empty text.

`baozicode.teams.approval.ApprovalProtocol` MUST provide:

- `parse_plan(body: str) -> tuple[str, str] | None`
- `parse_approval(body: str) -> tuple[str, Literal["approve","reject"],
  str | None] | None`
- `send_approval(inbox_dir: Path, plan_id: str, action: Literal
  ["approve","reject"], reason: str | None = None) -> None`

#### Scenario: Lead reads a PLAN from outbox
- **WHEN** MailboxNotifier scans alice.outbox and finds a message
  containing `---PLAN-3f4a7c---\n我会先...\n---END---`
- **THEN** it parses `(plan_id="3f4a7c", body="我会先...")`
- **AND** injects `<system-reminder type="team_mailbox">` to Lead with
  the plan body + suggested reply format

#### Scenario: Lead approves a plan via team_send_message
- **WHEN** Lead LLM calls
  `team_send_message(team=devops, member=alice,
  body="APPROVED: 3f4a7c")`
- **THEN** `Mailbox.append_message(alice.inbox, Message(sender="lead",
  body="APPROVED: 3f4a7c"))` succeeds
- **AND** `Mailbox.touch_wake(alice.dir)` is called after append
- **AND** the parser recognizes it on the next wake

#### Scenario: Plan body with no delimiters is ignored
- **WHEN** a member sends an outbox message without the
  `---PLAN-<id>---` block
- **THEN** `parse_plan` returns None and the message passes through
  unchanged (no protocol effect)

#### Scenario: Multiple PLANS, pick the right one
- **WHEN** member sends PLAN-3f4a7c then later PLAN-9d2e3f (revision)
- **THEN** `MailboxNotifier` surfaces both plans to Lead
- **AND** Lead can choose which to approve (rejection of old PLAN is
  implicit; member sees the latest APPROVED/REJECTED id and acts on the
  matching plan)

### Requirement: MailboxNotifier for Lead

The Lead Agent's `Agent.run` loop MUST inject a
`<system-reminder type="team_mailbox">` block before each LLM decision if
there are unread team mailbox events since the last iteration.

The notifier MUST:

1. Scan `<teams_dir>/<team>/<member>/outbox.jsonl` for every member of the
   active team
2. Compare new message lines against a per-iteration dedup set
   (`self._mailbox_seen: set[str] = set()` in `BaoZiCodeApp`)
3. For each new message, classify:
   - `TASK-COMPLETE-<task_id>` → member finished; parse body for summary
   - `TASK-FAILED-<task_id>` → member errored; parse body for error
   - `---PLAN-<id>---` → approval request; surface plan body
   - plain text → surface sender + body truncated to 200 chars
4. Include member `state.json.status` and last_active_ts in the reminder
5. After a member's TASK-COMPLETE or TASK-FAILED, call
   `Tasks.update_status` to mark the task done or failed and update the
   member's state to `idle`
6. Append the reminder; LLM may then call `team_send_message` to
   acknowledge (approve / reject / ask follow-up) without blocking

The reminder MUST be cleared / dedup'd after the LLM processes it so the
same message does not appear in two consecutive iterations.

#### Scenario: Member completes → Lead is notified
- **WHEN** alice outbox gets `{sender:alice, body:"---TASK-COMPLETE-t-001---\ndone: 健康检查 done\n---END---"}`
- **AND** alice state was `running, current_task=t-001`
- **THEN** MailboxNotifier:
  - marks tasks.jsonl t-001 as `done`, sets `completed_at=now`
  - updates alice state to `idle`, clears `current_task`
  - adds reminder to Lead conversation with the summary
- **AND** on the next Lead LLM iteration, Lead sees the reminder and
  chooses next action (e.g., dispatch t-002 or run `team_merge`)

#### Scenario: Member has been waiting 6 minutes — surfaced to Lead
- **WHEN** alice outbox has `---PLAN-3f4a7c---` at 14:00 and Lead is now
  at 14:06
- **THEN** the reminder includes `alice (waiting since 14:00, 6m ago)`
  text so Lead can decide to nudge or cancel

#### Scenario: Deduplication across iterations
- **WHEN** MailboxNotifier runs for two consecutive Agent.run iterations
  with no new outbox messages in between
- **THEN** the second iteration produces an empty reminder (no
  re-injection of the same plan)
- **AND** a new matching message body that arrives after the first
  iteration triggers re-injection in the next

### Requirement: team_merge per-member branch sequential

`run_team_merge(project_root, team, target='main')` MUST:

1. Verify `project_root` is a git repo (`git rev-parse` exits 0)
2. `git -C project_root checkout <target>` (must succeed; if not, abort
   and return error)
3. Iterate `team.members` in name-sorted order; for each member:
   - `git -C project_root merge --no-ff wt/<member> -m "Merge wt/<member>
     from team <team>"`
   - On success (returncode 0), append `<member>` to merged list
   - On failure (returncode != 0):
     - `git -C project_root merge --abort`
     - Append `{member, reason=<stderr first line>}` to aborted list
     - Continue to next member (per locked decision: best-effort,
       abort, surface)
4. Return `dict(status: 'complete'|'partial', merged: list[str],
   aborted: list[dict], target: str)`

`team_merge` MUST NOT be invoked from `Bash` (Coordinator decision 7):
Lead LLM uses the `team_merge` ToolCall, never a raw `git merge` bash
command.

#### Scenario: All members merge clean
- **WHEN** team has alice + bob, both branches auto-merge into target
- **THEN** return `{"status":"complete", "merged":["alice","bob"],
  "aborted":[], "target":"main"}`
- **AND** `git log` shows two `--no-ff` merge commits in order

#### Scenario: bob conflicts
- **WHEN** alice merges cleanly but bob's merge fails with conflicts in
  `src/feature.py`
- **THEN** `git -C project_root merge --abort` runs
- **AND** return `{"status":"partial", "merged":["alice"],
  "aborted":[{"member":"bob","reason":"CONFLICT in src/feature.py"}],
  "target":"main"}`
- **AND** the working tree is CLEAN (no half-merged state)

#### Scenario: dry_run skips git
- **WHEN** Lead LLM calls `team_merge(team=devops, dry_run=true)`
- **THEN** returns `{"status":"would-merge", "members":["alice","bob"]}`
  without touching any git state

#### Scenario: Not a git repo
- **WHEN** `project_root` is not a git repo
- **THEN** return `error_result("", "team_merge: project_root is not a
  git repository")`

### Requirement: Tool visibility scoped to Lead role

Tools whose primary actor is the Lead MUST be tagged
`role_visibility=['lead']`. The ToolRegistry MUST filter tools by the
calling agent's role at construction time:

- `Agent(role='lead')` receives tools with `role_visibility is None`
  OR `'lead' in role_visibility`
- `Agent(role='member')` and `Agent(role='subagent')` receive tools
  with `role_visibility is None` only (no team tools visible)

If a future coordinator mode is added (`Agent(role='coordinator')`),
those tools can opt in via `role_visibility=['lead', 'coordinator']`.

Member agents MUST NOT see `team_dispatch`, `team_send_message`,
`team_cancel`, `team_merge`, `team_task_create`, `team_task_query`
because doing so would allow recursion (member-dispatching-member) and
merge abuse (member merging code before Lead review).

#### Scenario: Lead sees all 13 tools
- **WHEN** Lead Agent is constructed with `role='lead'`
- **THEN** `get_all_tools(role='lead')` returns 7 built-in + 6 team_*
  = 13 ToolDefinition

#### Scenario: Member sees 7 tools only
- **WHEN** a member Agent is constructed with `role='member'`
- **THEN** `get_all_tools(role='member')` returns 7 built-in tools;
  the 6 team_* tools are NOT in the list

#### Scenario: Subagent sees 7 + filtered subagent tools
- **WHEN** a sub-Agent is constructed with `role='subagent'`
- **THEN** `get_all_tools(role='subagent')` returns 7 built-in tools
  and any `role_visibility is None` runtime tools; team_* tools are
  excluded

#### Scenario: Internal tools bypass role filter
- **WHEN** `load_skill` is registered with `tool_type='internal'`
- **AND** `role_visibility is None`
- **THEN** all roles can see it (default-allow)
- **AND** when `role='member'`, `load_skill` is still in
  `get_all_tools(role='member')` because its `role_visibility is None`
