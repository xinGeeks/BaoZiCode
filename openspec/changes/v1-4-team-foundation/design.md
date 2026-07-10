# v1.4 Team Lead — Foundation Design

## 数据模型

### Team

```python
@dataclass(frozen=True)
class Team:
    name: str                       # 团队名,TeamNameValidator 校验
    lead: str                       # Lead 名字(总是 session 主 Agent)
    created_at: datetime            # ISO 8601 UTC
    members: dict[str, Member]      # name → Member,member 名唯一
    metadata: dict[str, Any]        # 自由扩展(json-safe)
```

JSON 持久化:

```json
{
  "schema_version": "1.0",
  "name": "devops",
  "lead": "lead",
  "created_at": "2026-07-10T08:00:00Z",
  "members": {
    "alice": { "role": "backend", "workdir": ".worktrees/alice/", ... },
    "bob":   { "role": "frontend", "workdir": ".worktrees/bob/", ... }
  },
  "metadata": {}
}
```

### Member

```python
@dataclass(frozen=True)
class Member:
    name: str                                # 队员名,TeamNameValidator 校验
    role: str                                # 自由角色描述(backend / frontend / tester ...)
    workdir: str                             # 工作目录(相对项目根)
    backend: BackendType                     # 运行时后端
    requires_approval: bool = False          # 是否需要 Lead 审批后才动手
    config: dict[str, Any] = field(...)      # 后端特定配置(pane_id / coroutine 等)
```

`BackendType`:

```python
BackendType = Literal[
    "pane-tmux",              # tmux pane 跑完整实例
    "pane-iterm2",            # iTerm2 pane 跑完整实例(macOS)
    "pane-windows-terminal",  # Windows Terminal pane 跑完整实例
    "coroutine",              # 同进程协程轻量跑
    "worktree-coroutine",     # 同进程 + git worktree 隔离
]
```

`config` 字段示例:

```json
// pane-tmux
{ "session_name": "baozicode", "window_index": 0 }

// pane-iterm2
{ "window_id": "abc-def" }

// pane-windows-terminal
{ "profile_id": "Default" }

// coroutine / worktree-coroutine
{}
```

### Message

```python
@dataclass(frozen=True)
class Message:
    sender: str                # 发件人(member name / "lead" / "system")
    body: str                  # 消息正文(plain text,可含 YAML frontmatter)
    timestamp: datetime | None # None → append_message 自动补
    read: bool = False         # 默认未读
    summary: str = ""          # 摘要(收件人读完后可填)
```

`timestamp` 为 None 时由 `Mailbox.append_message` 自动填 `datetime.now(UTC)`。
`summary` 留给 `team-tools` proposal 的 `team_send_message` 工具用 LLM 自动
摘要。

### 文件布局

```
<teams_dir>/
└── <team_name>/
    ├── team.json                    # Team dataclass 序列化
    ├── tasks.jsonl                  # 共享任务清单(v1-4-team-tools 用)
    ├── <member_name>/
    │   ├── inbox.jsonl              # 收件箱(谁给我发的)
    │   ├── outbox.jsonl             # 发件箱(我给谁发的,Lead 读这)
    │   ├── state.json               # { last_active_ts, status, current_task }
    │   ├── wake.signal              # 空文件,Lead touch 触发 wake
    │   └── .lock                    # lockfile,append_message 时拿
    └── ...
```

**为什么 per-member 目录 + per-direction 拆 inbox/outbox**:

- Lead 写 alice 的 inbox + 读 alice 的 outbox → 文件不冲突,不用协调方向
- 每个文件一个 lockfile,粒度细,alice 的 inbox 写锁不阻塞 bob 的 inbox 写
- 单文件 JSONL append-only,跟 v0.8 sessions 风格一致

**wake.signal** 是空文件,touch 改 mtime。pane 内 watchdog 协程 200ms
轮询 `mtime` 变化(详见 `v1-4-team-pane-backend`)。coroutine 后端也读这
个文件(由 `mailbox.py` 暴露 `Mailbox.wait_for_wake(timeout)` 异步 API)。

**state.json**:

```json
{
  "status": "idle | running | waiting | offline",
  "last_active_ts": "2026-07-10T08:30:00Z",
  "current_task": "task-uuid-or-null",
  "backend_pid": 12345
}
```

`status` 由 `team-tools` proposal 的 Agent Loop 写;foundation 只定义
schema + 提供读写函数,业务状态由后续 proposal 填。

## Lockfile 协议

### 跨平台抽象

```python
class MailboxLock(Protocol):
    def acquire(self, timeout: float = 5.0) -> None: ...
    def release(self) -> None: ...

@contextmanager
def mailbox_lock(path: Path, *, timeout: float = 5.0, stale_seconds: float = 30.0):
    """跨平台分发;POSIX 用 fcntl.flock,Windows 用 msvcrt.locking"""
```

### POSIX 实现(`_PosixMailboxLock`)

```python
import fcntl

class _PosixMailboxLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def acquire(self, timeout: float = 5.0) -> None:
        # 以 O_CREAT | O_RDWR 打开 .lock 文件
        self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._stamp_lockfile()
                return
            except BlockingIOError:
                if self._is_lock_stale(stale_seconds):
                    os.close(self.fd)
                    os.unlink(str(self.path))
                    self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
                    continue
                if time.monotonic() >= deadline:
                    raise MailboxLockTimeout(...)
                time.sleep(0.05)  # 50ms 退避

    def _stamp_lockfile(self) -> None:
        # 把当前进程 PID + hostname + ts 写进 lockfile 便于 debug
        os.write(self.fd, f"{os.getpid()}\n{os.uname().nodename}\n{time.time()}\n".encode())
        os.fsync(self.fd)

    def release(self) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None
```

### Windows 实现(`_WindowsMailboxLock`)

```python
import msvcrt

class _WindowsMailboxLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def acquire(self, timeout: float = 5.0) -> None:
        self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + timeout
        while True:
            # msvcrt.locking 没有 NB,只能阻塞;包一层线程 + timeout
            # 但简单起见:用 LockFileEx + LOCKFILE_FAIL_IMMEDIATELY
            try:
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                self._stamp_lockfile()
                return
            except OSError:
                if self._is_lock_stale(stale_seconds):
                    os.close(self.fd)
                    os.unlink(str(self.path))
                    self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
                    continue
                if time.monotonic() >= deadline:
                    raise MailboxLockTimeout(...)
                time.sleep(0.05)
```

`_is_lock_stale(stale_seconds)`:`stat().st_mtime < now - stale_seconds`
则视为过期可偷。

### Context manager

```python
@contextmanager
def mailbox_lock(path: Path, *, timeout: float = 5.0, stale_seconds: float = 30.0):
    if sys.platform == "win32":
        lock = _WindowsMailboxLock(path)
    else:
        lock = _PosixMailboxLock(path)
    try:
        lock.acquire(timeout=timeout, stale_seconds=stale_seconds)
        yield
    finally:
        lock.release()
```

注意:Windows 上 `msvcrt.locking(fd, LK_NBLCK, 1)` 锁 1 字节。fd 必须
以二进制模式打开(`os.O_BINARY`)避免 newline 转换。

## JSONL 原子写

```python
def append_message(mailbox_dir: Path, direction: Literal["inbox", "outbox"], msg: Message) -> None:
    """原子追加一条消息到 inbox.jsonl 或 outbox.jsonl"""
    if msg.timestamp is None:
        msg = replace(msg, timestamp=datetime.now(timezone.utc))

    target = mailbox_dir / f"{direction}.jsonl"
    lock = mailbox_dir / ".lock"

    with mailbox_lock(lock):
        # 1. 写临时文件
        tmp = mailbox_dir / f".{direction}.jsonl.{os.getpid()}.{random.randint(0, 9999)}"
        with open(tmp, "a", encoding="utf-8") as f:
            data = msg.to_json_line()  # Pydantic model_dump_json()
            f.write(data + "\n")
            f.flush()
            os.fsync(f.fileno())

        # 2. 追加到目标(同 lock 下不会并发)
        with open(target, "a", encoding="utf-8") as f:
            with open(tmp, "r", encoding="utf-8") as src:
                shutil.copyfileobj(src, f)
            f.flush()
            os.fsync(f.fileno())

        # 3. 删临时文件
        os.unlink(tmp)
```

**为什么先写临时再 copy 而不是直接 append**:`shutil.copyfileobj` +
fsync 保证崩溃后目标文件仍是合法 JSONL(整行追加,不写一半)。

**为什么不直接 `open(target, "a")`**:`open("a")` 在某些 fsync 缺失场景下崩
溃会丢最后一行(已经 write 但内核 buffer 没落盘)。我们显式 fsync 确保每
行落盘。

## Bootstrap 流程

```
TeamsRegistry.bootstrap(config: AppConfig):
    1. teams_dir = config.teams.dir if config.teams else default
    2. teams_dir.mkdir(parents=True, exist_ok=True)
    3. self._index = scan_teams(teams_dir)  # 扫所有 <team_name>/team.json
    4. return self

scan_teams(teams_dir):
    for entry in teams_dir.iterdir():
        if entry.is_dir() and (entry / "team.json").exists():
            yield Team.load(entry / "team.json")

Team.create(name, *, lead="lead") -> TeamStore:
    1. validate name (TeamNameValidator)
    2. team_dir = teams_dir / name
    3. atomic:mkdir + write team.json
       - 用 O_CREAT | O_EXCL 防并发创建
       - 失败 → raise TeamAlreadyExists
    4. return TeamStore(team_dir)
```

## CLI 设计

```
baozicode team create <name>      # 新建空 team
baozicode team list               # 列出所有 team
baozicode team use <name>         # 激活 team(session 内可见 — foundation 只 stub)
baozicode team show <name>        # 打印 team.json JSON
baozicode team destroy <name>     # 销毁 team,默认需 --yes

参数:
  --scope {user|project}     # 默认 user,project 留后续 proposal
  --yes / -y                 # destroy 跳过交互确认
```

CLI 不调用 session 内 Lead,纯静态操作 team 目录。`team use` 在 foundation
只打印"已激活"(实际 `self.teams.use(name)` 由 `BaoZiCodeApp` 在
`on_mount` 时调,不在 CLI 范围)。

## 名字校验

`TeamNameValidator.validate(name)`:

- 字符集:`[a-z0-9-]`
- 长度:2–30 字符
- 必须以字母开头
- 必须以字母或数字结尾
- 拒绝 `--`,`-` 开头结尾,空字符串,纯数字
- 不允许 `.` / `_` / `\` / `/`(避免和 worktree 路径冲突)

错误枚举:`TeamNameInvalid` / `TeamNameTooShort` / `TeamNameTooLong` /
`TeamNameBadChar` / `TeamNameBadStart` / `TeamNameBadEnd`。

## 配置 schema

```python
class TeamsConfig(BaseModel):
    dir: str = "~/.config/baozicode/teams/"
    # 其他字段(coordinator / pane_backend 优先级等)留给后续 proposal

# AppConfig:
teams: TeamsConfig | None = None  # None → bootstrap 默认
```

`config.example.yaml`:

```yaml
teams:
  dir: ~/.config/baozicode/teams/
  # coordinator / pane_backend 字段后续 proposal 加
```

## App 集成

```python
# app.py
class BaoZiCodeApp:
    teams: TeamsRegistry  # 新字段

    def _build_teams_registry(self) -> TeamsRegistry:
        config = self.config
        return TeamsRegistry.bootstrap(config)

    def on_mount(self) -> None:
        # 在 permissions / instructions / memory / sessions 之后
        # (因为 teams 是新加的,放最后)
        self.teams = self._build_teams_registry()
```

## 测试矩阵

| 模块              | 单元测试覆盖                                       |
|-------------------|----------------------------------------------------|
| TeamNameValidator | 合法 / 空 / 太短 / 太长 / 大写 / 特殊字符 / 数字开头 |
| Team dataclass    | frozen 不可变 / JSON round-trip / 缺字段报错       |
| Member dataclass  | frozen / BackendType enum / config dict 任意     |
| Message dataclass | timestamp 自动补 / 默认 read=False / JSONL 行格式 |
| Mailbox 原子写    | inbox / outbox / fsync 后 kill 仍合法 / 写一半崩   |
| Lockfile POSIX    | 拿锁 / 阻塞 / stale 偷锁 / context manager 异常   |
| Lockfile Windows  | msvcrt / 跨平台分发(monkeypatch sys.platform)    |
| TeamStore         | create / load / list / show / destroy / 同名报错  |
| Registry          | 同名 team 并发只一个成功 / 同 team 同 member 报错 |
| CLI               | 5 子命令各 happy + 参数错误 + 缺参数              |
| TeamsConfig       | 缺省默认 / 自定义路径 / 不合法路径报错             |
| App 集成          | `_build_teams_registry` / on_mount bootstrap      |

合计约 `45` 个新测试。

## 与后续 proposal 的接口约定

foundation 只暴露给后续 proposal 这几个 API:

```python
# teams/__init__.py
from .schema import Team, Member, Message, BackendType, TeamNameValidator
from .registry import TeamsRegistry
from .store import TeamStore
from .mailbox import Mailbox  # append_message / read_messages / touch_wake / wait_for_wake

__all__ = [
    "Team", "Member", "Message", "BackendType",
    "TeamNameValidator",
    "TeamsRegistry", "TeamStore",
    "Mailbox",
]
```

`team-tools` proposal 在这之上加 `team_dispatch` / `team_send_message` /
`team_cancel` / `team_merge` 工具(全部走 `Mailbox` + `TeamStore` 接口)。
`team-pane-backend` proposal 在这之上加 `Mailbox.wait_for_wake` 的 pane
端实现(watchdog 协程)。
`team-coordinator` proposal 在 `team-tools` 之上加工具白名单收缩。

## 已锁定的 12 个 v1.4 决策(全部由后续 proposal 落地)

foundation 不直接实现,但 schema 必须为后续铺路:

| #  | 决策                                       | 落地 proposal                |
|----|--------------------------------------------|------------------------------|
| 1  | Team 长期固定 + 多次复用                    | team-tools (`team_use`)      |
| 2  | Lead `team_dispatch(name="alice")` 必填     | team-tools                   |
| 3  | 跨 worktree 路径走 L2 sandbox 白名单        | team-tools                   |
| 4  | P2P 消息 = 名称注册表 + 邮箱文件            | team-tools + pane-backend    |
| 5  | Wake = Lead touch 信号 + 200ms watchdog    | pane-backend                 |
| 6  | Idle 仅 task 完成触发                       | team-tools                   |
| 7  | Approval = YAML frontmatter 格式            | team-tools                   |
| 8  | Tasks 文件 `~/.config/baozicode/teams/<t>/tasks.jsonl` | foundation(只建文件,工具后续加) |
| 9  | Mailbox 持久化 = `<teams_dir>/<team>/<member>/{inbox,outbox,wake.signal,state.json}` | foundation |
| 10 | 消息格式 = sender/body/timestamp/read/summary | foundation                  |
| 11 | Lead 终止 = 主 Loop 自驱到底                | team-tools                   |
| 12 | Resume 加载 = 全 conversation + 新邮件 user msg | pane-backend               |

foundation 覆盖决策 8 / 9 / 10(schema + 文件层)。其他由后续 proposal 实现。