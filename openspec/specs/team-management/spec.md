# team-management Specification

## Purpose
TBD - created by archiving change v1-4-team-foundation. Update Purpose after archive.
## Requirements
### Requirement: TeamNameValidator

`TeamNameValidator.validate(name: str) -> None` MUST 是纯函数,无 IO、无副
作用,只做字符集 + 长度 + 起止校验:

- 字符集:`[a-z0-9-]`
- 长度:2–30 字符
- 必须以字母开头
- 必须以字母或数字结尾
- 拒绝空字符串
- 拒绝 `--` 连续(视觉易混)
- 拒绝 `.` / `_` / `\` / `/`(避免和文件路径冲突)

错误枚举:
- `TeamNameTooShort`(< 2)
- `TeamNameTooLong` (> 30)
- `TeamNameBadChar` (含非 `[a-z0-9-]`)
- `TeamNameBadStart` (以 `-` 或数字开头)
- `TeamNameBadEnd` (以 `-` 结尾)
- `TeamNameDoubleHyphen`(含 `--`)

#### Scenario: Accepted names
- **WHEN** `validate("devops")` / `validate("acme-team")` /
  `validate("team-001")`
- **THEN** 不抛异常

#### Scenario: Reject empty / too short
- **WHEN** `validate("")` / `validate("a")`
- **THEN** 抛 `TeamNameTooShort`

#### Scenario: Reject too long
- **WHEN** `validate("a" * 31)`
- **THEN** 抛 `TeamNameTooLong`

#### Scenario: Reject uppercase / special
- **WHEN** `validate("DevOps")` / `validate("team_one")` /
  `validate("team.001")` / `validate("team/sub")` /
  `validate("team\\sub")`
- **THEN** 抛 `TeamNameBadChar`

#### Scenario: Reject start / end
- **WHEN** `validate("-team")` / `validate("001")` / `validate("team-")`
- **THEN** 分别抛 `TeamNameBadStart` / `TeamNameBadStart` /
  `TeamNameBadEnd`

#### Scenario: Reject double hyphen
- **WHEN** `validate("acme--team")`
- **THEN** 抛 `TeamNameDoubleHyphen`

### Requirement: Team dataclass

`Team` MUST 是 frozen dataclass,字段:

- `name: str` —— 团队名,经 `TeamNameValidator.validate`
- `lead: str` —— Lead 名字(默认 `"lead"`,session 内主 Agent)
- `created_at: datetime` —— UTC,创建时填
- `members: dict[str, Member]` —— name → Member,member 名唯一 + 经
  `TeamNameValidator.validate`
- `metadata: dict[str, Any]` —— 自由扩展,json-safe

序列化:`Team.to_json() / Team.from_json() / Team.load(path) /
Team.save(path)`;JSON 字段固定顺序 + `schema_version: "1.0"` 头。

#### Scenario: Frozen prevents mutation
- **WHEN** `team.members["alice"] = new_member`
- **THEN** 抛 `dataclasses.FrozenInstanceError`

#### Scenario: JSON round-trip
- **WHEN** `team = Team(...)` → `team.to_json()` → `Team.from_json(json)`
- **THEN** 字段全等(除 `created_at` ISO 字符串往返解析后值相等)

#### Scenario: Member key/name 一致性
- **WHEN** 构造 `Team(members={"alice": Member(name="bob", ...)})`(dict key 与 Member.name 不一致)
- **THEN** 抛 `ValueError` 含 "key ... 与 Member.name ... 不一致"

注:Python dict 字面量自动去重(`{"alice": m1, "alice": m2}` 只保留 m2),
所以"重复 member 名"无法在 dict 输入端触发;__post_init__ 强制 key 与
Member.name 一致作为工程上的等同约束。

### Requirement: Member dataclass

`Member` MUST 是 frozen dataclass,字段:

- `name: str` —— 队员名,经 `TeamNameValidator.validate`
- `role: str` —— 自由角色描述(`backend` / `frontend` / `tester` ...)
- `workdir: str` —— 工作目录(相对项目根,默认 `.worktrees/<name>/`)
- `backend: BackendType` —— 后端类型(Pydantic Literal 强校验)
- `requires_approval: bool` —— 是否需要 Lead 审批后才动手,默认 `False`
- `config: dict[str, Any]` —— 后端特定配置,默认 `{}`

`BackendType` MUST 是 Pydantic `Literal`:

```
"pane-tmux" | "pane-iterm2" | "pane-windows-terminal"
| "coroutine" | "worktree-coroutine"
```

拼错值(如 `"pane-Tmux"`)Pydantic 直接报 `ValueError`。

#### Scenario: BackendType enum rejects typo
- **WHEN** `Member(..., backend="pane-Tmux")`
- **THEN** 抛 Pydantic `ValidationError` 含
  `unexpected value; permitted: 'pane-tmux', 'pane-iterm2', ...`

#### Scenario: Default approval false
- **WHEN** `Member(name="alice", role="backend", workdir="...",
backend="coroutine")`
- **THEN** `requires_approval is False` + `config == {}`

#### Scenario: Workdir auto-derived
- **WHEN** `Member(name="alice", ...)` 不传 `workdir`
- **THEN** `workdir == ".worktrees/alice/"`(由 `__post_init__` 补)

### Requirement: Message dataclass + JSONL format

`Message` MUST 是 frozen dataclass,字段:

- `sender: str` —— 发件人(member name / `"lead"` / `"system"`)
- `body: str` —— 消息正文(plain text,可含 YAML frontmatter)
- `timestamp: datetime | None` —— `None` → `Mailbox.append_message` 自动
  补 `datetime.now(timezone.utc)`
- `read: bool` —— 默认 `False`
- `summary: str` —— 默认 `""`(收件人读完可填)

JSONL 行格式:每行一个 JSON 对象,字段顺序:
`{sender, body, timestamp, read, summary}`,timestamp ISO 8601 UTC。

#### Scenario: Timestamp auto-filled
- **WHEN** `msg = Message(sender="lead", body="hi")` →
  `Mailbox.append_message(..., msg)`
- **THEN** 落盘行的 `timestamp` 非空,等于 `append_message` 调用瞬间的 UTC
  时间

#### Scenario: Default read false
- **WHEN** 构造 `Message(sender="alice", body="hi")`
- **THEN** `read is False` + `summary == ""`

#### Scenario: JSONL line parseable
- **WHEN** 读 `inbox.jsonl` 第一行
- **THEN** `json.loads(line)` 返回 `Message.from_dict(...)` 字段全等

### Requirement: Mailbox file layout

每个 team member MUST 持久化到独立目录:

```
<teams_dir>/<team_name>/<member_name>/
├── inbox.jsonl              # 收件箱
├── outbox.jsonl             # 发件箱
├── state.json               # { status, last_active_ts, current_task, backend_pid }
├── wake.signal              # 空文件,Lead touch 触发 wake
└── .lock                    # lockfile,append_message 时拿
```

`<teams_dir>/<team_name>/` MUST 额外含:

- `team.json` —— Team dataclass 序列化
- `tasks.jsonl` —— 共享任务清单(v1-4-team-tools 写入,foundation 只建空文件)

每个文件 MUST 满足:

- `inbox.jsonl` / `outbox.jsonl` —— JSONL append-only,空文件视为合法
  (0 字节)
- `state.json` —— JSON 对象,缺字段时读时填默认值
- `wake.signal` —— 空文件或不存在(Lead 创建时不存在,pane watchdog 自
  己 touch)
- `.lock` —— 二进制 lockfile,内容 `{pid}\n{hostname}\n{ts}\n`

#### Scenario: Member dir created on team create
- **WHEN** `TeamStore.create("devops")` + `add_member(alice)`
- **THEN** `<teams_dir>/devops/alice/{inbox,outbox,state,wake.signal,lock}` 全在

#### Scenario: Empty JSONL is valid
- **WHEN** `inbox.jsonl` 是 0 字节
- **THEN** `Mailbox.read_messages(inbox)` 返回 `[]`

#### Scenario: Missing state fields default
- **WHEN** `state.json` 缺 `current_task` 字段
- **THEN** `Mailbox.read_state()` 返回
  `{status: "offline", last_active_ts: None, current_task: None, backend_pid: None}`

### Requirement: MailboxLock protocol + cross-platform dispatch

`MailboxLock` MUST 是 Protocol,提供 `acquire(timeout, stale_seconds)` /
`release()` 方法。`mailbox_lock(path, *, timeout, stale_seconds)` context
manager MUST 按 `sys.platform` 分发:

- POSIX(`!= "win32"`)—— `_PosixMailboxLock` 用 `fcntl.flock(LOCK_EX |
  LOCK_NB)`,失败时按 stale 阈值决定偷锁 vs 重试
- Windows(`== "win32"`)—— `_WindowsMailboxLock` 用
  `msvcrt.locking(fd, LK_NBLCK, 1)`,失败时同样按 stale 阈值偷锁

stale 锁判定:`lockfile.st_mtime < now - stale_seconds` → 视为过期可偷。
默认 `stale_seconds=30`。

`timeout` 默认 5.0 秒;超时抛 `MailboxLockTimeout` 异常(异常带 path +
elapsed 信息)。

#### Scenario: Acquire happy path
- **WHEN** `mailbox_lock(path)` + 无其他进程持锁
- **THEN** `acquire()` 立即返回 + lockfile 内容含 `{pid, hostname, ts}`

#### Scenario: Acquire blocking waits
- **WHEN** 进程 A 持锁 + 进程 B 调 `mailbox_lock(path)` 不超时
- **THEN** B 阻塞(50ms 退避重试)→ A release 后 B 拿到锁

#### Scenario: Stale lock stolen
- **WHEN** 进程 A 持锁后崩 / `kill -9`(lockfile mtime 35s 前,默认
  `stale_seconds=30`)
- **THEN** B 调 `acquire()` 检测 stale → 删 lockfile → 重建 + 拿锁
- **AND** 不抛 `MailboxLockTimeout`

#### Scenario: Timeout raises
- **WHEN** 进程 A 持锁不释放 + B 调 `mailbox_lock(path, timeout=0.5)`
- **THEN** 0.5s 后抛 `MailboxLockTimeout` 含 path + elapsed=0.5

#### Scenario: Release safe on exception
- **WHEN** `with mailbox_lock(path): raise RuntimeError`
- **THEN** `__exit__` 调 `release()` + lockfile fd 关闭(无 ResourceWarning)

#### Scenario: Windows branch via monkeypatch
- **WHEN** `sys.platform = "win32"`(monkeypatch)→ `mailbox_lock(path)`
- **THEN** 实例化 `_WindowsMailboxLock` 而非 `_PosixMailboxLock`(测试用
  `inspect.signature` 验证)

### Requirement: Mailbox atomic append

`Mailbox.append_message(dir, direction, msg)` MUST 原子追加一条消息到
`inbox.jsonl` 或 `outbox.jsonl`,步骤:

1. 若 `msg.timestamp is None` → 替换为 `datetime.now(timezone.utc)`
2. 拿 `mailbox_lock(dir / ".lock")`(默认 timeout=5s, stale=30s)
3. 写临时文件 `dir / f".{direction}.jsonl.{pid}.{rand}"`
4. `flush` + `fsync` 临时文件
5. 用 `shutil.copyfileobj` 把临时文件内容追加到目标 JSONL
6. `flush` + `fsync` 目标文件
7. 删临时文件
8. 释放锁

任何步骤崩溃后,目标 `inbox.jsonl` / `outbox.jsonl` MUST 仍是合法 JSONL
(每行能 `json.loads`,无半行 / 截断)。

#### Scenario: Append creates target
- **WHEN** `inbox.jsonl` 不存在 + `append_message(inbox, msg)`
- **THEN** `inbox.jsonl` 存在 + 第 1 行是 `msg.to_json_line()`

#### Scenario: Multiple appends accumulate
- **WHEN** 连续 `append_message(inbox, msg1)` / `append_message(inbox,
  msg2)` / `append_message(inbox, msg3)`
- **THEN** `inbox.jsonl` 有 3 行,顺序正确,每行可独立 `json.loads`

#### Scenario: Crash mid-append leaves valid file
- **WHEN** `append_message` 跑到第 5 步前 `kill -9` 模拟
- **THEN** `inbox.jsonl` 是合法 JSONL(可能少最后一行,但无半行)

#### Scenario: Concurrent appends serialize
- **WHEN** 两个进程并发 `append_message(inbox, msg_a)` /
  `append_message(inbox, msg_b)`
- **THEN** 两行都进 `inbox.jsonl`,顺序由锁决定,无交错 / 无丢失

### Requirement: TeamsRegistry index + uniqueness

`TeamsRegistry.bootstrap(config) -> TeamsRegistry` MUST 扫 `<teams_dir>/`
下所有 `<team_name>/team.json`,建索引。`create_team(name) -> TeamStore`
MUST 满足:

- 用 `O_CREAT | O_EXCL` 创建 team 目录 + `team.json`
- 失败 → 抛 `TeamAlreadyExists`
- 成功 → 返回 `TeamStore(team_dir)`

`add_member(team, member) -> None` MUST 满足:

- 同 team 内 `member.name` 已存在 → 抛 `MemberAlreadyExists`
- 成功 → 写 `<member_dir>/state.json`(默认 status="offline")

#### Scenario: Create happy
- **WHEN** `registry.create_team("devops")`
- **THEN** `<teams_dir>/devops/team.json` 存在 + `Team.load(...)` 字段全等

#### Scenario: Duplicate create fails
- **WHEN** `registry.create_team("devops")` + 再
  `registry.create_team("devops")`
- **THEN** 第二次抛 `TeamAlreadyExists`

#### Scenario: Add member creates dir
- **WHEN** `registry.add_member(team, Member(name="alice", ...))`
- **THEN** `<teams_dir>/devops/alice/{state.json,inbox.jsonl,outbox.jsonl}` 全在

#### Scenario: Duplicate member name fails
- **WHEN** `add_member(team, alice)` + 再 `add_member(team, alice)`
- **THEN** 第二次抛 `MemberAlreadyExists`

#### Scenario: Bootstrap scans existing
- **WHEN** `<teams_dir>/` 已有 `devops/team.json` + `acme/team.json`
- **THEN** `TeamsRegistry.bootstrap(config).list_teams()` 返回
  `["acme", "devops"]`(字典序)

### Requirement: Lifecycle CLI

CLI MUST 暴露 5 个子命令:

```
baozicode team create <name>            [--scope {user|project}]    # default user
baozicode team list                    [--scope {user|project}]    # default user
baozicode team show <name>             [--scope {user|project}]
baozicode team use <name>               [--scope {user|project}]    # foundation: 仅打印
baozicode team destroy <name>           [--scope {user|project}]    # 默认需 --yes
                                        [--yes | -y]               # 跳过交互确认
                                        [--force]                  # 跳过确认 + 忽略错误
```

错误处理:

- name 不合法 → 退出码 2 + stderr `Error: <TeamNameInvalid>: <name>`
- team 不存在(`show` / `use` / `destroy`)→ 退出码 3 + stderr
  `Error: team '<name>' not found`
- `destroy` 缺 `--yes` 且非 `--force` → 交互确认(`input()` 等 `y/N`)

#### Scenario: create happy
- **WHEN** `baozicode team create devops`(CLI)
- **THEN** 退出码 0 + stdout `<teams_dir>/devops/team.json` 创建提示

#### Scenario: create invalid name
- **WHEN** `baozicode team create "DevOps"`(大写)
- **THEN** 退出码 2 + stderr 含 `TeamNameBadChar`

#### Scenario: list prints names
- **WHEN** `<teams_dir>/` 下有 `devops` + `acme`
- **THEN** `baozicode team list` 退出码 0 + stdout 含两行(字典序)

#### Scenario: show prints JSON
- **WHEN** `baozicode team show devops`
- **THEN** 退出码 0 + stdout 是 `team.json` 的 pretty JSON

#### Scenario: destroy requires --yes
- **WHEN** `baozicode team destroy devops`(无 `--yes`, stdin EOF)
- **THEN** 退出码非 0 + stderr 含 "aborted"(交互确认拒绝)

#### Scenario: destroy --yes happy
- **WHEN** `baozicode team destroy devops --yes`
- **THEN** 退出码 0 + `<teams_dir>/devops/` 被 `rmtree`

#### Scenario: use foundation stub
- **WHEN** `baozicode team use devops`
- **THEN** 退出码 0 + stdout 含 "已激活 devops"(foundation 实现,后续
  proposal 接入 session)

### Requirement: TeamsConfig schema

`AppConfig.teams: TeamsConfig | None` MUST 在 v1.4 foundation 加入:

```python
class TeamsConfig(BaseModel):
    dir: str = "~/.config/baozicode/teams/"
```

加载时 `~` MUST 展开为 `Path.home()`;不合法路径(非字符串 / 空字符串)
Pydantic 直接报。

`AppConfig.teams = None` MUST 走 bootstrap 默认(`dir =
"~/.config/baozicode/teams/"`)。

`BaoZiCodeApp._build_teams_registry()` MUST 在 `on_mount` 末尾调
`TeamsRegistry.bootstrap(config)`,挂 `self.teams: TeamsRegistry`。

#### Scenario: Default teams_dir
- **WHEN** `AppConfig()` 不传 `teams`
- **THEN** `config.teams.dir == "~/.config/baozicode/teams/"`(走 Pydantic
  默认)

#### Scenario: Tilde expansion
- **WHEN** `TeamsConfig(dir="~/custom/teams/")`
- **THEN** `TeamsConfig.bootstrap(config).teams_dir ==
  Path.home() / "custom/teams/"`

#### Scenario: Empty dir rejected
- **WHEN** `TeamsConfig(dir="")`
- **THEN** Pydantic `ValidationError`

#### Scenario: App bootstraps registry
- **WHEN** `BaoZiCodeApp.on_mount()` 跑完
- **THEN** `self.teams is TeamsRegistry` + `self.teams.list_teams()` 含
  teams_dir 下所有 team

#### Scenario: Out of scope (deferred)
- **WHEN** `TeamsConfig.coordinator` / `TeamsConfig.pane_backend` 等字段
- **THEN** 不在本 proposal 范围(`v1-4-team-coordinator` /
  `v1-4-team-pane-backend` proposal 加)

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

