# v1.4 Team Pane Backend — Member Runtime Design

## Architecture Overview

```
                    ┌─────────────────────────────────────────────────┐
                    │            Lead 主进程(Textual App)              │
                    │                                                  │
                    │  ┌──────────────────────────────────────────┐   │
                    │  │  BaoZiCodeApp.on_mount 末尾              │   │
                    │  │  backend_manager = BackendManager(teams)  │   │
                    │  └──────────────────────────────────────────┘   │
                    │                       │                          │
                    │  ┌────────────────────▼─────────────────────┐   │
                    │  │ tools.py / team_dispatch executor         │   │
                    │  │   inbox + wake → spawn_if_offline(alice)   │   │
                    │  └────────────────────┬─────────────────────┘   │
                    │                       │                          │
                    │  ┌────────────────────▼─────────────────────┐   │
                    │  │ BackendManager                            │   │
                    │  │  ├─ detect_available_backends()           │   │
                    │  │  ├─ spawn_if_offline(team, member)        │   │
                    │  │  ├─ is_alive(team, member)                 │   │
                    │  │  ├─ kill(team, member, grace=5.0)         │   │
                    │  │  └─ pane_info.json 持久化                 │   │
                    │  └────────────────────┬─────────────────────┘   │
                    │                       │                          │
                    │            ┌──────────┴──────────┬─────────┐    │
                    │            ▼                     ▼         ▼    │
                    │     ┌─────────────┐ ┌─────────────────┐ ┌─────┐│
                    │     │ Coroutine   │ │  PaneTmux       │ │Pane ││
                    │     │ Backend     │ │  Backend        │ │WT/IT││
                    │     │ (in-process │ │  (subprocess)   │ │  ... ││
                    │     │  asyncio    │ │                 │ │     ││
                    │     │  Task)      │ │                 │ │     ││
                    │     └─────────────┘ └─────────────────┘ └─────┘│
                    └─────────────────────────────────────────────────┘
                                       │                            │
                                       │ subprocess.Popen           │
                                       ▼                            │
                    ┌────────────────────────────────────────────────┐
                    │      tmux session:  baozicode-team-devops      │
                    │  ┌─────────────┐ ┌─────────────┐ ┌──────────┐ │
                    │  │ pane: alice │ │ pane: bob   │ │...       │ │
                    │  │ baozicode   │ │ baozicode   │ │          │ │
                    │  │ member run  │ │ member run  │ │          │ │
                    │  │ --team=...  │ │ --team=...  │ │          │ │
                    │  │ --name=...  │ │ --name=...  │ │          │ │
                    │  └──────┬──────┘ └──────┬──────┘ └──────────┘ │
                    └─────────┼──────────────┼───────────────────────┘
                              │              │
                              ▼              ▼
                    ┌──────────────────────────────────────┐
                    │  MemberMainLoop(long-lived polling)   │
                    │                                        │
                    │  while not terminate_signal:            │
                    │    await wait_for_wake(mailbox_dir)    │
                    │    msgs = Mailbox.read_inbox(...)      │
                    │    if not msgs: continue               │
                    │    agent = build_member_agent(...)     │
                    │    async for evt in agent.run(...):    │
                    │      handle_event(evt)                 │
                    │    Mailbox.write_state(idle)           │
                    └──────────────────────────────────────┘
```

## BackendHandle Protocol

```python
class BackendHandle(Protocol):
    """Member 后端的具体实例句柄 — 后端生命周期内的活体对象。

    由 BackendManager.spawn_if_offline 返回的实例;不持久到磁盘
    (持久在 pane_info.json 是 backend_identifier 字段字面量)。
    """

    @property
    def member_name(self) -> str: ...

    @property
    def team_name(self) -> str: ...

    @property
    def backend_type(self) -> BackendType: ...

    @property
    def pid(self) -> int | None:
        """成员进程 PID;coroutine backend 返回 None;tmux/iTerm2/WT 子
        进程 PID 由 subprocess.Popen 返回;coroutine 没有 OS 级 pid
        返回 None。
        """
        ...

    def is_alive(self) -> bool:
        """检查后端是否还活。POSIX:`os.kill(self._pid, 0)` 不抛;
        抛 `ProcessLookupError` 返回 False。Windows:`subprocess.run(
        'tasklist', '/FI', f'PID eq {pid}')` exit==0 且 stdout 含 PID。
        coroutine backend:`not self._task.done()`。
        """
        ...

    def kill(self, *, grace_seconds: float = 5.0) -> None:
        """Graceful kill — SIGTERM → grace_seconds → SIGKILL 链。
        全程不挂,即便 SIGKILL 后进程不退也 log warn + return。
        coroutine backend:`self._task.cancel()` 等同 SIGTERM。
        """
        ...

    def title(self, new_title: str) -> None:
        """更新 pane 标题(如 'alice running task=3f4a7c')。
        tmux:`tmux select-pane -t <session>:<win>.<pane> -T "<title>"`。
        iTerm2:Python API `iterm2.Session.set_title()`(不引 pyobjc,
        走 AppleScript)。
        Windows Terminal:`wt.exe session --title <title> <session>`。
        coroutine backend:no-op。
        """
        ...
```

### `PaneTmuxBackend`

```python
class PaneTmuxBackend:
    """PANE_BACKEND_TMUX:tmux session-per-team,window-per-member。

    派生:`subprocess.run(["tmux", "new-session", "-d", "-s",
        "baozicode-team-devops", "-n", "alice", "<command>"])` 启
        session;再 `split-window` 给其他 member;最后 `select-window -t
        <member>` 让 pane 拿到焦点。

    命名约定:`pane_title = f"baozicode-team-{team}"]`
              `session_name = f"{prefix}-{team}"`(prefix 默认
              `"baozicode-team"`,可被 `Member.config[
              "tmux_session_prefix"]` 覆盖)
    """

    def __init__(
        self, team: str, member: str, command: list[str],
        *, tmux_session_prefix: str = "baozicode-team",
    ):
        self._session_name = f"{tmux_session_prefix}-{team}"
        self._window_name = member
        self._command = command
        self._pane_id: str | None = None  # tmux pane id "%5"
        self._pid: int | None = None        # child PID

    @classmethod
    def available(cls) -> bool:
        """`subprocess.run(["tmux", "-V"], capture_output=True)`.
        exit=0 + stdout startswith "tmux " → True。
        """
        try:
            r = subprocess.run(
                ["tmux", "-V"], capture_output=True, text=True,
                timeout=2,
            )
            return r.returncode == 0 and r.stdout.startswith("tmux ")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def spawn(self) -> None:
        """创 session(if not exists)+ window + pane + run command。

        步骤(同步,on Lead 主进程):
        1. `tmux has-session -t <session_name>` —— 不存在则
           `tmux new-session -d -s <session_name> -n <member> "exit"`
           (占位空)
        2. `tmux new-window -t <session_name> -n <member> -d "<cmd>"`
           创建具体 member pane(<cmd> 含 `baozicode member run ...`)
        3. `tmux list-panes -t <session_name> -F '#{pane_id} #{pane_pid}
           #{window_name}'` —— 找到刚建的 pane,缓存 pane_id / pid
        4. `tmux select-window -t <session_name>:<window_name>` 拿焦点
        """
        # ... (实际 subprocess.run 调用,失败 raise PaneBackendError)
```

### `PaneITerm2Backend`

```python
class PaneITerm2Backend:
    """PANE_BACKEND_ITERM2 — macOS iTerm2 per-team window,per-member tab。

    派生:AppleScript `tell application "iTerm2" to create window with
    default profile command "<cmd>"` 创 window,再 `tell current session
    of current window to set name to "<member>"`。

    命名:`window name = f"{prefix}-{team}"`(默认
    `"baozicode-team"`)
    """

    @classmethod
    def available(cls) -> bool:
        """`subprocess.run(["osascript", "-e", 'tell application
        "iTerm2" to id'], capture_output=True)`. exit=0 + 输出
        string-of-int → True。"""
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "iTerm2" to return id of (first window)'],
                capture_output=True, text=True, timeout=2,
            )
            return r.returncode == 0 and r.stdout.strip().isdigit()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def spawn(self) -> None:
        """subprocess.run(["osascript", "-e", applescript_template,
        "--", session_name, member_name, cmd_str])"""
```

### `PaneWindowsTerminalBackend`

```python
class PaneWindowsTerminalBackend:
    """PANE_BACKEND_WT — Windows 11+ Terminal per-team tab,per-member split。

    派生:`subprocess.run(["wt.exe", "-w", "0", "new-tab", "--title",
        f"{prefix}-{team}/{member}", cmd_str])` 创 tab + 在 split
        pane 跑 command。
    """

    @classmethod
    def available(cls) -> bool:
        """`subprocess.run(["where", "wt.exe"], capture_output=True,
        text=True)` exit=0 + stdout 含 'wt.exe' 路径 → True。"""
```

### `CoroutineBackend`

```python
class CoroutineBackend:
    """PANE_BACKEND_COROUTINE — 同进程 asyncio Task,纯 Python,无外部
    命令。Member Main Loop 在 Lead asyncio loop 里跑。

    优点:零依赖 / 零 GUI 启动,headless CI 默认 backend
    缺点:同进程 — Lead / Member Agent loop 共用 asyncio loop,
    Member LLM 阻塞会阻塞 Lead UI(异步 yield 应缓解)
    """

    def __init__(self, team: str, member: str, member_loop: MemberMainLoop):
        self._loop = member_loop
        self._task: asyncio.Task | None = None

    @classmethod
    def available(cls) -> bool:
        return True  # 永远可用

    def spawn(self) -> None:
        """Lead 主进程 `asyncio.create_task(self._loop.run())`.
        self._task 留 PID 替代品(None — coroutine 没 OS-level PID)。
        """
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(
            self._loop.run(), name=f"member-{self._team}-{self._member}",
        )

    def kill(self, *, grace_seconds: float = 5.0) -> None:
        """`self._task.cancel()`;MemberMainLoop.run 收 CancelledError
        退出循环。grace 语义不适用(Task cancel 即时)。
        """

    def is_alive(self) -> bool:
        return self._task is not None and not self._task.done()
```

### `WorktreeCoroutineBackend`

```python
class WorktreeCoroutineBackend(CoroutineBackend):
    """PANE_BACKEND_WORKTREE_COROUTINE — Coroutine + 强制 chdir 到
    .worktrees/<name>/。v1.3 worktree + v1.4 pane 的组合 backend。

    spawn 时检查 `.worktrees/<name>/` 存在(不存在走 v1.3
    WorktreeInitializer 初始化);`os.chdir` 后启 MemberMainLoop。
    """
```

## BackendManager

```python
class BackendManager:
    """Lead 主进程的 backend 调度器。

    单例 per `BaoZiCodeApp` 实例;on_mount 构造;panes 跨 on_unmount
    持久(不清理)。
    """

    def __init__(
        self,
        teams_registry: TeamsRegistry,
        *,
        backend_detection_timeout: float = 2.0,
        member_run_command: list[str] | None = None,  # 默认 = sys.argv[0] + ["member","run","--team","{team}","--name","{name}"]
    ):
        self._registry = teams_registry
        self._handles: dict[tuple[str, str], BackendHandle] = {}
        # (team, member) → BackendHandle 实例(同 Lead 进程活跃句柄)
        self._spawn_locks: dict[tuple[str, str], asyncio.Lock] = {}
        # 防止并发 spawn race
        self._backend_detection_timeout = backend_detection_timeout
        self._member_run_command = member_run_command or self._default_command()
        # 探测结果 cache(per BackendManager 实例)
        self._detected_available: dict[BackendType, bool] | None = None
        # pane session 持久
        self._pane_info_path: Callable[[str], Path] = (
            lambda team: teams_registry.teams_dir / team / "pane_info.json"
        )

    # -------------------------- 探测 ----------------------------

    def detect_available_backends(self) -> dict[BackendType, bool]:
        """探测可用 backend,结果缓存 per 实例(Leader 进程内单次探测)。

        返回:{"pane-tmux": bool, "pane-iterm2": bool,
              "pane-windows-terminal": bool, "coroutine": True,
              "worktree-coroutine": True}
        """
        if self._detected_available is not None:
            return self._detected_available
        result = {
            "pane-tmux": PaneTmuxBackend.available(),
            "pane-iterm2": PaneITerm2Backend.available(),
            "pane-windows-terminal": PaneWindowsTerminalBackend.available(),
            "coroutine": True,  # 永远可用
            "worktree-coroutine": True,
        }
        self._detected_available = result
        return result

    def effective_backend(self, member: Member) -> BackendType:
        """Member.backend 决策 —— auto-upgrade 路径:

        1. Member.backend 显式非 "coroutine" → 走 detect 看是否健康,
           不健康降级 coroutine + log warn
        2. Member.backend 显式 "coroutine" → 仍探测 pane 后端,
           健康则 upgrade:`coroutine → pane-tmux / pane-iterm2 /
           pane-windows-terminal`(按 sys.platform 优先级)>,否则
           coroutine
        3. 探测优先级:
           - Darwin(Linux/BSD):pane-tmux > pane-iterm2 >
             pane-windows-terminal > coroutine
           - Windows:pane-windows-terminal > pane-tmux(WSL?) >
             coroutine
           - 其他:pane-tmux > coroutine
        """
        # ... (实现见 proposal R3 mitigation)

    # -------------------------- spawn ----------------------------

    async def spawn_if_offline(
        self, team: str, member: str,
    ) -> BackendHandle:
        """派生 member 在其 effective_backend 上;
        同 (team, member) 已活 → 返现有 handle;未活 + 已有 in-flight
        spawn → await 该 task 完成后返句柄;未活 + 无 in-flight →
        获 asyncio.Lock + spawn。
        """
        key = (team, member)
        # 1. 快路径:已活直接返
        handle = self._handles.get(key)
        if handle and handle.is_alive():
            return handle
        # 2. 拿锁(防止并发 spawn)
        lock = self._spawn_locks.setdefault(key, asyncio.Lock())
        async with lock:
            # 进锁后二次检查(可能 in-between 已派生好)
            handle = self._handles.get(key)
            if handle and handle.is_alive():
                return handle
            member_obj = self._registry.get(team).members[member]
            backend = self.effective_backend(member_obj)
            handle = self._construct_backend(backend, team, member, member_obj)
            # pane 类型 backend 是 subprocess 启动 — 同步阻塞 1-3s
            if isinstance(handle, (PaneTmuxBackend, PaneITerm2Backend,
                                  PaneWindowsTerminalBackend)):
                handle.spawn()  # sync
            else:
                handle.spawn()  # async Task create
            # 写 state.json 持久化
            member_dir = (
                self._registry.teams_dir / team / member
            )
            Mailbox.write_state(member_dir, MemberState(
                status="idle",
                last_active_ts=datetime.now(timezone.utc),
                current_task=None,
                backend_pid=handle.pid,
            ))  # backend_pid 写实际 PID(coroutine 是 None)
            # pane session 持久
            self._persist_pane_info(team, member, backend, handle)
            self._handles[key] = handle
            return handle

    # -------------------------- is_alive -------------------------

    def is_alive(self, team: str, member: str) -> bool:
        """检查 member 后端当前是否活。

        对 pane 类型:`os.kill(self._handles[(team, member)].pid, 0)`
        POSIX 不抛;Windows `tasklist` 查 PID。
        对 coroutine 类型:`not self._handles[...]._task.done()`。
        对 stale state.json:`backend_pid` 字段存在但 in-memory handle
        失效 → 警告 + 返 False,让上层重新 spawn。
        """
        handle = self._handles.get((team, member))
        if handle is None:
            return False
        try:
            return handle.is_alive()
        except Exception as e:  # noqa: BLE001
            log.warning("is_alive %s/%s failed: %s", team, member, e)
            return False

    # -------------------------- kill -----------------------------

    async def kill(
        self, team: str, member: str, *,
        reason: str = "", grace_seconds: float = 5.0,
    ) -> None:
        """Graceful kill:state.json:status="offline" + handle.kill(
        grace_seconds) + log warn if grace 超时。

        步骤:
        1. 拿 (team, member) 的 handle;无 → 警告,直接写 state.json =
           offline(可能 pane-backend 重启后 stale)
        2. write_state(offline) 先标 offline
        3. handle.kill(grace_seconds=grace_seconds) — SIGTERM →
           grace → SIGKILL 链
        4. 从 self._handles 移除 key
        5. pane_info.json 标 member backend_type=None(下次 spawn 走
           effective_backend 重算)
        """
        handle = self._handles.pop((team, member), None)
        member_dir = self._registry.teams_dir / team / member
        Mailbox.write_state(member_dir, MemberState(
            status="offline", current_task=None,
        ))
        if handle is None:
            return  # 无 in-memory handle;但 state.json 已 offline
        try:
            await asyncio.to_thread(handle.kill, grace_seconds=grace_seconds)
        except Exception as e:  # noqa: BLE001
            log.error("kill %s/%s failed: %s", team, member, e)
        # pane_info persistence:把 member 后端句柄清掉
        pane_info = self._load_pane_info(team)
        if pane_info and member in pane_info.members:
            pane_info.members[member]["backend_type"] = None
            self._save_pane_info(team, pane_info)

    # -------------------------- pane_info ----------------------

    def _persist_pane_info(
        self, team: str, member: str,
        backend: BackendType, handle: BackendHandle,
    ) -> None:
        """写 `<teams_dir>/<team>/pane_info.json` 持久化每 member 的
        backend_type + pane identifier(tmux session name / iTerm2
        window id / WT tab uuid)+ last_spawn_at。
        """

    def _load_pane_info(self, team: str) -> PaneInfo | None:
        """读 pane_info.json;文件不存在返 None(走默认 + 即时派生)。"""

    def restore_panes(self, team: str) -> None:
        """Lead 启动时调:扫 pane_info.json,把上次 lead 派生过的
        pane 重新 attach(handle 不重新 spawn,而是 verify tmux session
        还在 / iTerm2 window 还在 / WT tab 还在)。

        verify 通过 → 写 in-memory handle
        verify 失败 → 移除 stale entry(下次 spawn_if_offline 重派生)
        """
```

## pane_info.json Schema

```json
{
  "schema_version": "1.0",
  "team": "devops",
  "tmux_session_name": "baozicode-team-devops",
  "iterm2_window_id": null,
  "wt_tab_uuid": null,
  "members": {
    "alice": {
      "backend_type": "pane-tmux",
      "pane_identifier": "%5",
      "pid": 12345,
      "last_spawn_at": "2026-07-15T10:32:18Z"
    },
    "bob": {
      "backend_type": "pane-tmux",
      "pane_identifier": "%7",
      "pid": 12350,
      "last_spawn_at": "2026-07-15T10:32:18Z"
    }
  }
}
```

字段语义:
- `schema_version` — 当前 `"1.0"`,BackendManager 启动时 check
  (未来 schema 演进)
- `team` — team 名(校验)
- `tmux_session_name` — per-team tmux session 名;PANEL 多 team
  共用一个 Lead 进程时 `<prefix>-<team>` 防冲突
- `members[<name>].backend_type` — 当前 backend 字面量;
  `None` 表示已被 kill 等下次 lazy spawn
- `members[<name>].pane_identifier` — tmux pane id(`%5`)/ iTerm2
  `window_id` / WT `tab_uuid`;用来 reuse 现有 pane 而不是新建
- `members[<name>].pid` — 最近派生时拿到的 child PID;
  Lead 重启后 verify 用
- `members[<name>].last_spawn_at` — ISO 8601 UTC;BackendManager
  启动时如果 last_spawn_at > 1h ago,默认 auto-restart

```python
@dataclass(frozen=True)
class PaneInfo:
    schema_version: str = "1.0"
    team: str = ""
    tmux_session_name: str = ""
    iterm2_window_id: str | None = None
    wt_tab_uuid: str | None = None
    members: dict[str, PaneMemberInfo] = field(default_factory=dict)

@dataclass(frozen=True)
class PaneMemberInfo:
    backend_type: BackendType | None
    pane_identifier: str | None
    pid: int | None
    last_spawn_at: datetime
```

## MemberMainLoop

```python
class MemberMainLoop:
    """Member 进程内的主循环 —— 长生命周期 polling。

    入口:`baozicode member run --team=X --name=Y` 命令解析后调
    `MemberMainLoop(teams_registry, team, member).run()`。

    退出:`terminate=True`(pane kill)/ 进程 signal /
    uncaught exception(写 state.json=offline + log)。

    关键不变量:每次 inbox 唤醒处理是**fresh Agent run**(per
    D5 locked)—— 不在 Agent 主循环跨 turn 持久 conversation;
    永久内容由 mailbox + tasks.jsonl 承担。
    """

    def __init__(
        self,
        teams_registry: TeamsRegistry,
        team: str, member: str,
    ):
        self._registry = teams_registry
        self._team = team
        self._member = member
        self._member_obj = teams_registry.get(team).members[member]
        self._member_dir = teams_registry.teams_dir / team / member
        self._terminate_flag = asyncio.Event()
        self._active_turn_task: asyncio.Task | None = None

    async def run(self) -> None:
        """Member main loop headlessly。

        步骤:
        0. `os.chdir(<self._member_obj.workdir>)`(`.worktrees/<name>/`)
        1. `Mailbox.wake_initialized(member_dir)` 记录起点
        2. while True:
           a. `await Mailbox.wait_for_wake(member_dir, timeout=30.0)`
              — False(超时)→ continue;True(被 Lead 改)→ break
           b. `terminate_flag.is_set()` → break
           c. `messages = Mailbox.read_messages(member_dir, "inbox",
              unread_only=True)`(排除已 read)
           d. 若 messages 空 → `Mailbox.mark_read(...)` 清标记 +
              `continue`(异常路径:有人 touch_wake 但 inbox 空)
           e. `Mailbox.write_state(idle=running)` —— status="running"
           f. `agent = build_member_agent(self._registry, self._team,
              self._member)`
           g. `events = agent.run(messages=...)`(或 build initial user
              msg from inbox messages)
           h. handle events — capture outbox writes
           i. `Mailbox.write_state(idle)`,`Mailbox.mark_read(...)`
              当前 inbox 消息全标 read
           j. terminate_flag 检查
        3. 退出:`Mailbox.write_state(offline)` + log info
        """

    def request_terminate(self) -> None:
        """`team_cancel(terminate=True)` 调:设 flag + 终止 active turn
        `asyncio.Task.cancel()`.MemberMainLoop.run 退出循环。
        """
        self._terminate_flag.set()
        if self._active_turn_task and not self._active_turn_task.done():
            self._active_turn_task.cancel()

    # helper

    async def _run_turn(self, msgs: list[Message]) -> None:
        """跑一个 Member Agent turn:fresh Agent 实例 + 处理 inbox。
        Turn 期间所有 ToolResult 中需发往 outbox 的消息由 mailbox
        集成在 `tools.py` 6 个工具里(共享 Member 工具 = 7 内置 +
        mailbox_write/mailbox_read 内部 wrapper);Agent.run 走完自
        动写 outbox。
        """
        # 由 build_member_agent 注入 mailbox-aware 工具
        ...
```

## MemberAgent

```python
def build_member_agent(
    teams_registry: TeamsRegistry,
    team: str, member: str,
) -> Agent:
    """构造 Member Agent —— 复用 Agent(role='member', ...) 全套。

    关键不变量:
    - role='member' → 7 builtin 工具(Read/Write/Edit/Bash/Grep/Glob/
      WebFetch);无 team_* 工具(Lead-only);无 SubAgent 委派工具
    - cwd = member.workdir(via os.chdir 在 MemberMainLoop.run
      步骤 0 完成)
    - skills: 默认拉 v1.0 skills(config.yaml SkillsConfig,不主动
      拉 sub-skills);独立 sub-Skills 留给未来 proposal
    - tools: 7 builtin + mailbox helpers(mailbox_read/mailbox_send
      作为 `tool_type='internal'` wrapper,绕过 Skill 白名单)
    """

    config = ... # lead App.app_config(从 environment 取)
    conversation = ConversationManager()
    permissions = permissions_for_member(config)
    skill_registry = skill_registry_for_member(config)
    tool_registry = ToolRegistry()  # 7 builtin + runtime 注册
    mailbox = MailboxLayer(teams_registry, team, member)

    return Agent(
        llm_client=llm_factory.create_client(config),
        tools=tool_registry.get_all_tools(role="member"),
        conversation=conversation,
        permissions=permissions,
        config=config,
        role="member",
        skill_registry=skill_registry,
        skill_activation=skill_activation_for_member(),
        # custom hook for mailbox:
        mailbox_layer=mailbox,  # used by agent loop to write tools
    )
```

## CLI `baozicode member run`

```python
# cli.py
def add_member_subcommand(subparsers: argparse._SubParsersAction):
    """`baozicode member <action>` 子命令组 — 第一阶段只 `run`"""
    member_p = subparsers.add_parser("member", help="...")
    member_sub = member_p.add_subparsers(dest="member_action", required=True)
    run_p = member_sub.add_parser("run", help="启动 member 长主循环")
    run_p.add_argument("--team", required=True,
                       help="Team 名(TeamNameValidator 校验)")
    run_p.add_argument("--name", required=True,
                       help="Member 名(TeamNameValidator 校验)")
    run_p.add_argument("--cwd", default=None,
                       help="覆盖 workdir(默认走 Member.workdir)")
    return member_p

async def main_member_run(args: argparse.Namespace) -> int:
    """`baozicode member run` 入口:

    1. init Logging
    2. load_config(`./config.yaml` + `.env`)
    3. teams_registry = TeamsRegistry.bootstrap(config)
    4. team = teams_registry.get(args.team)
    5. member = team.members[args.name]
    6. if args.cwd: os.chdir(args.cwd)
       else: os.chdir(member.workdir)
    7. loop = MemberMainLoop(teams_registry, args.team, args.name)
    8. await loop.run()
    9. return 0
    """
```

```python
# __main__.py
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baozicode")
    parser.add_argument("--config", ...)
    subparsers = parser.add_subparsers(dest="cmd", required=False)
    # 原有:`baozicode team <action>` 子命令
    add_team_subcommand(subparsers)
    # 新加:`baozicode member <action>` 子命令
    add_member_subcommand(subparsers)
    args = parser.parse_args(argv)
    if args.cmd == "team":
        return team_cli.main(args)
    elif args.cmd == "member":
        if args.member_action == "run":
            return asyncio.run(main_member_run(args))
    # 无 cmd + TUI 路径(原行为)
    ...
```

## team-tools 集成(spawn 钩子)

```python
# baozicode/teams/tools.py(team_dispatch executor 末尾)
async def execute_team_dispatch(
    teams_registry: TeamsRegistry,
    backend_manager: BackendManager,  # 新注入
    team: str, member: str, task_id: str | None,
) -> ToolResult:
    """写 inbox + wake + spawn + transition task status。"""
    # ... 既有 mailbox write + wake + Tasks.update_status 逻辑 ...
    spawn_result = await backend_manager.spawn_if_offline(team, member)
    return ToolResult.success(
        "", f"dispatched {member} task={task_id} backend={spawn_result.backend_type}"
    )
```

```python
# baozicode/teams/tools.py(team_cancel terminate=True 分支)
async def execute_team_cancel(
    teams_registry: TeamsRegistry,
    backend_manager: BackendManager,
    team: str, member: str,
    reason: str = "", *, terminate: bool = False,
) -> ToolResult:
    # ... 既有 mailbox cancel + state offline + Tasks.update_status 逻辑 ...
    if terminate:
        await backend_manager.kill(team, member, reason=reason, grace_seconds=5.0)
    # 既有 return ...
```

## App + TUI 接线

```python
# baozicode/app.py
class BaoZiCodeApp:
    def on_mount(self) -> None:
        # ... 既有 permissions / instructions / memory / sessions /
        # commands / skills / teams bootstrap ...
        # v1.4 pane-backend:BackendManager 单例
        self.backend_manager = BackendManager(self.teams)
        # 既有 _register_team_tools(self.tool_registry) 不变
        # spawn 钩子在 executor 内部透明调(self.backend_manager
        # 通过 tools.py 的 executor 闭包注入)

    def use_team(self, name: str) -> None:
        # ... 既有 active_team_name 切换 + MailboxNotifier 构造 ...
        # v1.4 pane-backend:非首次激活不需立刻 spawn,等首次 dispatch
        # pass:`backend_manager = self.backend_manager` 不重建
```

`on_unmount` **不变** —— BackendManager 单例引用保留,panes 跨 Lead
重启持久。

## 与 team-tools MailboxNotifier 协作

MailboxNotifier 已经每轮 Lead Agent 决策前扫所有 member outbox ——
这一行为**完全不变**。区别:pane-backend 之前,inbox 永远空(members
不消费)→ outbox 也永远空→ MailboxNotifier 形同虚设。pane-backend
之后,members 真消费,outbox 真填,MailboxNotifier 真起作用。

`MailboxNotifier.mark_task_complete` / `mark_task_failed` 在 v1-4-
team-tools 已实现 — 本 proposal 不动。

## 测试矩阵

| 模块                       | 单元测试覆盖                                              |
|----------------------------|-----------------------------------------------------------|
| BackendHandle Protocol     | 5 个 BackendType 都 `is_alive` / `kill` / `title` 接口齐全 |
| PaneTmuxBackend            | available 检测 / spawn (mocked tmux) / kill_session / select-pane -T 标题 / pane_info 写 |
| PaneITerm2Backend          | available 检测 / spawn (mocked osascript) / window 名 / title |
| PaneWindowsTerminalBackend | available 检测 / spawn (mocked wt.exe) / new-tab 标题 |
| CoroutineBackend           | 100% 覆盖:same-process spawn / kill / is_alive / title no-op |
| WorktreeCoroutineBackend   | os.chdir 后 PWD = .worktrees/<name>/;子目录自动创建 |
| BackendManager             | detect_available_backends / effective_backend auto-upgrade / spawn_if_offline dedup / is_alive stale / kill grace chain / pane_info 加载+保存 / restore_panes |
| MemberMainLoop             | polling 主循环 / fresh-Agent-per-turn / Task.cancel 退出 / state.idle 写回 |
| build_member_agent         | role='member' + 7 工具 + mailbox 注入 |
| baozicode member run CLI   | 解析 --team/--name 必填 / 缺参数报错 / member 不存在 / team 不存在 |
| spawn 钩子                 | `execute_team_dispatch` 末尾 spawn_if_offline / dedup / 失败回退 coroutine |
| team_cancel terminate      | 走 BackendManager.kill / grace chain / state offline |

合计 ~98 个新测试。Coroutines 100% 单测(POSIX + Win 都试);
pane 后端用 `monkeypatch subprocess.run` 屏蔽(real tool 测试在
engineer 本机 + CI sa nity check 跑)。

## 已锁定的 8 个决策(全部由 pane-backend 落地)

| # | 决策                                          | 本 proposal 落地                                                                                                       |
|---|-----------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| 1 | Pane backend priority                         | `effective_backend` 自动升级函数:tmux > iTerm2 > WT > coroutine(按 sys.platform 优先级) |
| 2 | Lazy dispatch spawn                           | `team_dispatch` 末尾 spawn_if_offline; 同 `(team, member)` 已活 / in-flight 跳过                       |
| 3 | Member 进程 = headless CLI                    | `baozicode member run --team=X --name=Y` headless 启 MemberMainLoop                                              |
| 4 | 同 (team, member) spawn dedup                 | BackendManager._spawn_locks per-(team, member) asyncio.Lock                                                       |
| 5 | Resume / wake = mailbox + fresh Agent per turn| MemberMainLoop 每次 wake 启 fresh Agent;mailbox 是唯一可信源                                                       |
| 6 | Window/Tab/Session 持久化                     | `<teams_dir>/<team>/pane_info.json` 持久化 tmux session_name / pane_identifier / pid;Lead 重启 restore_panes       |
| 7 | 同 process 多 backend 抽象                    | BackendHandle Protocol + 5 个 concrete class                                                                      |
| 8 | state.json: backend_pid 字段填真值            | spawn 后 `Mailbox.write_state(status="idle", backend_pid=handle.pid)`;coroutine = None                              |

本 proposal 落 1 / 2 / 3 / 4 / 5 / 6 / 7 / 8 共 8 个决策;foundation + 
team-tools 之前已落 9 / 10 / 11。coordinator 提案在 5 之上加
`role='coordinator'` 子 Agent + 工具白名单收缩。
