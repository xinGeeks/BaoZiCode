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

