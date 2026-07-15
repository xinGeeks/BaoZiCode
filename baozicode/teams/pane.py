"""v1.4 Pane Backend — Member 派生 / 唤醒 / Resume 运行时。

公开 API:

- `BackendHandle` —— Protocol(`is_alive()` / `kill(grace_seconds)` /
  `title(new_title)` + `member_name` / `team_name` / `backend_type` /
  `pid` 属性),5 种 backend 都实现
- `PaneTmuxBackend` —— tmux 多窗格后端:`tmux new-session -d` 占位
  + `new-window -t <session> -n <member>` 起 pane + `list-panes` 拿
  pane_id / pid;SIGTERM → grace → SIGKILL + `kill-window`
- `PaneITerm2Backend` —— macOS iTerm2 后端,osascript 调
  `create window with default profile command`
- `PaneWindowsTerminalBackend` —— Windows Terminal 后端,`wt.exe
  new-tab --title <title> <command>` + `wt.exe ls` 取 tab uuid
- `CoroutineBackend` —— in-process asyncio.Task 后端,永远可用
- `WorktreeCoroutineBackend(CoroutineBackend)` —— coroutine 之上
  `os.chdir(member.workdir)` + `WorktreeManager.create(name)` 初
  始化目录
- `tmux_session_name(team_name)` / `tmux_pane_target(...)` ——
  共享 helper

设计要点:

- 5 个 backend 都是 `BackendHandle` Protocol 实现,BackendManager 据
  此多态调用;不在 Protocol 暴露 `spawn()`,由 backend 自行实现
- 每个 backend 的 `available()` 是 classmethod,跑 `subprocess.run`
  探测(2s timeout),失败返回 False
- Pane 进程 spawn 失败 → BackendManager 降级到 coroutine;进程
  alive 状态走 `os.kill(pid, 0)` POSIX / `subprocess tasklist`
  Windows(简化:统一走 `os.kill`,Windows 下行为略有差异但足够)
- kill chain:SIGTERM → `asyncio.sleep(grace_seconds)` → 检查还活
  → SIGKILL + pane-specific 清理
- Coroutine backend 没有真实进程,`pid=None`、`is_alive()` 走
  `not self._task.done()`;`kill()` = `task.cancel()` 即时
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .schema import BackendType, Member

log = logging.getLogger(__name__)

# 默认探测 / kill 参数
_PROBE_TIMEOUT = 2.0
_DEFAULT_GRACE_SECONDS = 5.0
# 默认 tmux session 前缀;BackendManager 启动时扫此前缀回收 orphan
DEFAULT_TMUX_SESSION_PREFIX = "baozicode-team"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def tmux_session_name(team_name: str, *, prefix: str = DEFAULT_TMUX_SESSION_PREFIX) -> str:
    """生成 tmux session 名 — 全团队 panes 共享一个 session。

    格式:`<prefix>-<team>`,例 `baozicode-team-devops`。
    """
    return f"{prefix}-{team_name}"


def tmux_window_target(
    team_name: str,
    member_name: str,
    *,
    prefix: str = DEFAULT_TMUX_SESSION_PREFIX,
) -> str:
    """生成 tmux window target — `session:window-name` 形式,BackendManager
    kill / title 用。"""
    return f"{tmux_session_name(team_name, prefix=prefix)}:{member_name}"


def _run_probe(args: list[str], *, timeout: float = _PROBE_TIMEOUT) -> subprocess.CompletedProcess:
    """跑探测命令;异常统一返回非零 returncode(不抛)。"""
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
        # 探测阶段 → 静默,不挂
        log.debug("pane probe %s 失败: %s", args, e)
        return subprocess.CompletedProcess(
            args=args, returncode=-1, stdout="", stderr=str(e),
        )


def _safe_kill(pid: int, sig: int) -> bool:
    """`os.kill(pid, sig)` 包一层,ProcessLookupError / OSError 不挂。"""
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, OSError) as e:
        log.debug("kill pid=%s sig=%s 失败: %s", pid, sig, e)
        return False


def _pid_alive(pid: int) -> bool:
    """POSIX `os.kill(pid, 0)`;Windows 走 OpenProcess 简化路径。"""
    if pid is None or pid <= 0:
        return False
    if platform.system() == "Windows":
        # Windows 下 `os.kill` 实际是 TerminateProcess;改走 tasklist 探测
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
                check=False,
            )
            return str(pid) in out.stdout
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


# ---------------------------------------------------------------------------
# BackendHandle Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class BackendHandle(Protocol):
    """Member 派生后端的统一接口。BackendManager 据此多态调用。

    5 个 backend 都满足此 Protocol。`spawn()` 不在 Protocol(各 backend
    构造参数不同);BackendManager 直接持有 backend 实例,instance
    方法调用即可。
    """

    member_name: str
    team_name: str
    backend_type: BackendType
    pid: int | None

    def is_alive(self) -> bool:
        """进程 / 任务是否仍在运行。coroutine backend 走 task.done()。"""
        ...

    def kill(self, *, grace_seconds: float = _DEFAULT_GRACE_SECONDS) -> None:
        """SIGTERM → grace → SIGKILL chain。coroutine 即时 cancel。"""
        ...

    def title(self, new_title: str) -> None:
        """更新 pane / window 标题;no-op 实现也算合规。"""
        ...


# ---------------------------------------------------------------------------
# PaneTmuxBackend
# ---------------------------------------------------------------------------


@dataclass
class PaneTmuxBackend:
    """tmux 多窗格后端。

    拓扑:每 team 一个 tmux session(`baozicode-team-<team>`),每个
    member 一个 window(`<member>`);window 内 pane 跑 member 进程。

    `spawn()` 步骤:
      1. `tmux has-session -t <session>` 探测 session 是否存在;
         不存在 → `new-session -d -s <session> -n placeholder` 占位
      2. `tmux new-window -t <session> -n <member> -d "<command>"`
         创建 member window
      3. `tmux list-panes -t <session>:<member>` 解析 pane_id 与 pid
      4. `tmux select-window -t <session>:<member>` 给焦点

    `kill()`:SIGTERM → grace → SIGKILL + `kill-window` 清理。
    """

    member_name: str
    team_name: str
    command: list[str]
    session_prefix: str = DEFAULT_TMUX_SESSION_PREFIX
    backend_type: BackendType = "pane-tmux"
    pane_id: str = ""
    pid: int | None = None

    @classmethod
    def available(cls) -> bool:
        """`tmux -V` exit=0 + stdout startswith 'tmux ' → True。"""
        cp = _run_probe(["tmux", "-V"])
        return cp.returncode == 0 and cp.stdout.lstrip().startswith("tmux ")

    def _session(self) -> str:
        return tmux_session_name(self.team_name, prefix=self.session_prefix)

    def _target(self) -> str:
        return tmux_window_target(self.team_name, self.member_name, prefix=self.session_prefix)

    def spawn(self) -> BackendHandle:
        session = self._session()
        target = self._target()
        # 1) session 不存在 → 占位 session
        cp = _run_probe(["tmux", "has-session", "-t", session], timeout=_PROBE_TIMEOUT)
        if cp.returncode != 0:
            placeholder = _run_probe(
                ["tmux", "new-session", "-d", "-s", session, "-n", "placeholder"],
                timeout=_PROBE_TIMEOUT,
            )
            if placeholder.returncode != 0:
                raise RuntimeError(
                    f"tmux new-session 占位失败: {placeholder.stderr or placeholder}"
                )
        # 2) 起 member window
        cmd_str = " ".join(shlex.quote(p) for p in self.command)
        cp = _run_probe(
            ["tmux", "new-window", "-t", session, "-n", self.member_name, "-d", cmd_str],
            timeout=_PROBE_TIMEOUT,
        )
        if cp.returncode != 0:
            raise RuntimeError(
                f"tmux new-window 失败: {cp.stderr or cp}"
            )
        # 3) 解析 pane_id / pid
        cp = _run_probe(
            ["tmux", "list-panes", "-t", target, "-F", "#{pane_id} #{pane_pid}"],
            timeout=_PROBE_TIMEOUT,
        )
        if cp.returncode != 0 or not cp.stdout.strip():
            raise RuntimeError(
                f"tmux list-panes 失败: {cp.stderr or cp.stdout}"
            )
        first_line = cp.stdout.strip().splitlines()[0].strip()
        parts = first_line.split()
        if len(parts) < 2:
            raise RuntimeError(f"tmux list-panes 输出异常: {first_line!r}")
        pane_id, pid_str = parts[0], parts[1]
        try:
            pid = int(pid_str)
        except ValueError as e:
            raise RuntimeError(f"tmux pane pid 解析失败: {pid_str!r}") from e
        self.pane_id = pane_id
        self.pid = pid
        # 4) 给焦点
        _run_probe(
            ["tmux", "select-window", "-t", target],
            timeout=_PROBE_TIMEOUT,
        )
        log.info("tmux pane 已派生: %s pane_id=%s pid=%s", target, pane_id, pid)
        return self

    def is_alive(self) -> bool:
        if self.pid is None:
            return False
        return _pid_alive(self.pid)

    def kill(self, *, grace_seconds: float = _DEFAULT_GRACE_SECONDS) -> None:
        """SIGTERM → grace → SIGKILL + `kill-window` 收尾。"""
        if self.pid is not None:
            _safe_kill(self.pid, 15)  # SIGTERM
            # grace 等待 — sync 阻塞(BackendManager.kill 调
            # asyncio.to_thread 包装,不阻塞 event loop)
            import time as _time
            deadline = _time.monotonic() + grace_seconds
            while _time.monotonic() < deadline:
                if not _pid_alive(self.pid):
                    break
                _time.sleep(0.1)
            else:
                _safe_kill(self.pid, 9)  # SIGKILL
        # kill-window 清理(无论进程是否真死)
        _run_probe(
            ["tmux", "kill-window", "-t", self._target()],
            timeout=_PROBE_TIMEOUT,
        )

    def title(self, new_title: str) -> None:
        """`select-pane -T` 改 pane 标题。"""
        if not self.pane_id:
            return
        target = f"{self._session()}:{self.member_name}.{self.pane_id.lstrip('%')}"
        _run_probe(
            ["tmux", "select-pane", "-t", target, "-T", new_title],
            timeout=_PROBE_TIMEOUT,
        )


# ---------------------------------------------------------------------------
# PaneITerm2Backend
# ---------------------------------------------------------------------------


@dataclass
class PaneITerm2Backend:
    """iTerm2 后端(macOS 专属)。

    `spawn()`:`osascript` 调 `create window with default profile command`
    + `set name of session of window id <id> to "<member>"`。
    `kill()`:`close session id <id>`,grace chain advisory。
    """

    member_name: str
    team_name: str
    command: list[str]
    backend_type: BackendType = "pane-iterm2"
    window_id: str = ""
    pid: int | None = None

    @classmethod
    def available(cls) -> bool:
        """osascript 探测 iTerm2 是否安装(若 -e 'tell application "iTerm2"
        to return version' 成功 → True)。"""
        cp = _run_probe(
            ["osascript", "-e", 'tell application "iTerm2" to return version'],
            timeout=_PROBE_TIMEOUT,
        )
        return cp.returncode == 0 and bool(cp.stdout.strip())

    def spawn(self) -> BackendHandle:
        # 转 command → shell 字符串(双引号 escape)
        cmd_str = " ".join(self.command)
        safe_cmd = cmd_str.replace("\\", "\\\\").replace('"', '\\"')
        # 1) create window
        script = (
            f'tell application "iTerm2"\n'
            f'  set newWin to (create window with default profile '
            f'command "{safe_cmd}")\n'
            f'  set name of current session of newWin to "{self.member_name}"\n'
            f'  return id of newWin\n'
            f'end tell\n'
        )
        cp = _run_probe(
            ["osascript", "-e", script],
            timeout=_PROBE_TIMEOUT,
        )
        if cp.returncode != 0:
            raise RuntimeError(
                f"iTerm2 create window 失败: {cp.stderr or cp}"
            )
        window_id = cp.stdout.strip().splitlines()[-1].strip()
        if not window_id:
            raise RuntimeError(f"iTerm2 window id 解析失败: {cp.stdout!r}")
        self.window_id = window_id
        # iTerm2 window id → 进程 PID;osascript 取
        pid_script = (
            f'tell application "iTerm2"\n'
            f'  set targetSession to (first session of window id {window_id})\n'
            f'  return (unix id of targetSession)\n'
            f'end tell\n'
        )
        cp = _run_probe(["osascript", "-e", pid_script], timeout=_PROBE_TIMEOUT)
        if cp.returncode == 0 and cp.stdout.strip():
            try:
                self.pid = int(cp.stdout.strip().splitlines()[-1])
            except ValueError:
                self.pid = None
        log.info("iTerm2 pane 已派生: window_id=%s pid=%s", window_id, self.pid)
        return self

    def is_alive(self) -> bool:
        if self.window_id:
            # 检查 window 是否还存在
            script = (
                f'tell application "iTerm2" to return (count of window id '
                f'{self.window_id})'
            )
            cp = _run_probe(["osascript", "-e", script], timeout=_PROBE_TIMEOUT)
            if cp.returncode == 0 and cp.stdout.strip() == "1":
                return True
        if self.pid is not None:
            return _pid_alive(self.pid)
        return False

    def kill(self, *, grace_seconds: float = _DEFAULT_GRACE_SECONDS) -> None:
        """SIGTERM(若有 pid)→ grace → close window。"""
        if self.pid is not None:
            _safe_kill(self.pid, 15)
            import time as _time
            deadline = _time.monotonic() + grace_seconds
            while _time.monotonic() < deadline:
                if not _pid_alive(self.pid):
                    break
                _time.sleep(0.1)
            else:
                _safe_kill(self.pid, 9)
        if self.window_id:
            script = (
                f'tell application "iTerm2" to close window id {self.window_id}'
            )
            _run_probe(["osascript", "-e", script], timeout=_PROBE_TIMEOUT)

    def title(self, new_title: str) -> None:
        if not self.window_id:
            return
        safe_title = new_title.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            f'tell application "iTerm2"\n'
            f'  set name of (first session of window id {self.window_id}) to '
            f'"{safe_title}"\n'
            f'end tell\n'
        )
        _run_probe(["osascript", "-e", script], timeout=_PROBE_TIMEOUT)


# ---------------------------------------------------------------------------
# PaneWindowsTerminalBackend
# ---------------------------------------------------------------------------


@dataclass
class PaneWindowsTerminalBackend:
    """Windows Terminal 后端(Win10+)。

    `spawn()`:`wt.exe -w 0 new-tab --title "<session>/<member>"
    <command>`;`wt.exe ls` 解析最新 tab uuid。kill:`wt.exe close-tab`
    (grace advisory)。title:`wt.exe session --title <title> <uuid>`。
    """

    member_name: str
    team_name: str
    command: list[str]
    session_prefix: str = DEFAULT_TMUX_SESSION_PREFIX
    backend_type: BackendType = "pane-windows-terminal"
    tab_uuid: str = ""
    pid: int | None = None

    @classmethod
    def available(cls) -> bool:
        """`where wt.exe` 探测;Win-only。"""
        if platform.system() != "Windows":
            return False
        cp = _run_probe(["where", "wt.exe"], timeout=_PROBE_TIMEOUT)
        return cp.returncode == 0 and "wt.exe" in cp.stdout.lower()

    def _title(self) -> str:
        return f"{self.session_prefix}-{self.team_name}/{self.member_name}"

    def spawn(self) -> BackendHandle:
        cmd_str = " ".join(f'"{p}"' if " " in p else p for p in self.command)
        cp = _run_probe(
            [
                "wt.exe", "-w", "0", "new-tab",
                "--title", self._title(),
                cmd_str,
            ],
            timeout=_PROBE_TIMEOUT,
        )
        if cp.returncode != 0:
            raise RuntimeError(
                f"wt.exe new-tab 失败: {cp.stderr or cp}"
            )
        # `wt.exe ls` 解析最新 tab uuid(`--title` 含 member 名 → grep 锁定)
        cp = _run_probe(["wt.exe", "ls"], timeout=_PROBE_TIMEOUT)
        if cp.returncode != 0:
            raise RuntimeError(f"wt.exe ls 失败: {cp.stderr or cp}")
        uuid = self._parse_uuid_from_ls(cp.stdout, self._title())
        if not uuid:
            raise RuntimeError(
                f"wt.exe ls 未找到 title={self._title()!r} 的 tab"
            )
        self.tab_uuid = uuid
        log.info("Windows Terminal tab 已派生: uuid=%s title=%s", uuid, self._title())
        return self

    @staticmethod
    def _parse_uuid_from_ls(stdout: str, title: str) -> str:
        """`wt.exe ls` 输出多行 tab 块,顺序不固定。

        按 `Profile:` 切块,块内收集 Title + GUID(plain 或 `{GUID}` 都
        支持);返回首个 title 匹配 target 的块的 GUID。顺序无关。
        """
        target = title.lower()
        guid_re = re.compile(
            r"\{{0,1}([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{12})\}{0,1}",
            re.IGNORECASE,
        )
        blocks: list[dict[str, str]] = []
        cur = {"title": "", "guid": ""}
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if lower.startswith("profile:"):
                if cur["title"] or cur["guid"]:
                    blocks.append(cur)
                cur = {"title": "", "guid": ""}
                continue
            if lower.startswith("title:"):
                cur["title"] = lower.split(":", 1)[1].strip()
            elif lower.startswith("guid:"):
                m = guid_re.search(stripped)
                if m:
                    cur["guid"] = m.group(1)
        if cur["title"] or cur["guid"]:
            blocks.append(cur)
        for block in blocks:
            if block["title"] == target and block["guid"]:
                return block["guid"]
        return ""

    def is_alive(self) -> bool:
        # `wt.exe ls` 还能找到 uuid → alive
        cp = _run_probe(["wt.exe", "ls"], timeout=_PROBE_TIMEOUT)
        if cp.returncode != 0:
            return False
        return self.tab_uuid in cp.stdout

    def kill(self, *, grace_seconds: float = _DEFAULT_GRACE_SECONDS) -> None:
        # WT 无 native kill(tab → cmd 进程由 WT 启动但 cmd 也可独立);
        # 若有 pid 则走 SIGTERM-equivalent(Windows 用 taskkill)
        if self.pid is not None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(self.pid), "/T"],
                    capture_output=True, timeout=_PROBE_TIMEOUT, check=False,
                )
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                pass
        # close-tab(uuid)
        _run_probe(
            ["wt.exe", "close-tab", "--tab", self.tab_uuid],
            timeout=_PROBE_TIMEOUT,
        )

    def title(self, new_title: str) -> None:
        if not self.tab_uuid:
            return
        # wt.exe 不直接改 title;rename 等价 — 关闭再开
        # 简化:只更新 tab_name 占位(wt.exe 1.20+ 不支持,fallback no-op)
        # 真实场景走 PowerShell COM;这里 no-op + log warn
        log.debug("WindowsTerminalBackend.title(%r) 未实现完整改名", new_title)


# ---------------------------------------------------------------------------
# CoroutineBackend
# ---------------------------------------------------------------------------


@dataclass
class CoroutineBackend:
    """In-process asyncio.Task 后端 — 永远可用。

    `spawn()`:在 `asyncio.get_running_loop()` 上 `create_task(
    self._loop.run(), name=<task-name>)`;`kill()` = `task.cancel()`
    即时;`is_alive()` = `not task.done()`;`title()` no-op。
    """

    member_name: str
    team_name: str
    command: list[str]
    backend_type: BackendType = "coroutine"
    pid: int | None = None
    _task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    @classmethod
    def available(cls) -> bool:
        """永远 True — asyncio 总是 in-process。"""
        return True

    async def spawn(self) -> BackendHandle:
        """异步 spawn — 在当前 event loop 建 Task。"""
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(
            self._run(), name=f"member-{self.team_name}-{self.member_name}"
        )
        log.info(
            "coroutine backend 已派生: %s/%s task=%s",
            self.team_name, self.member_name, self._task.get_name(),
        )
        return self

    async def _run(self) -> None:
        """Coroutine 后端的占位 run;真实 MemberMainLoop 在 Phase 5 接管。"""
        try:
            # command 通常是 `[sys.executable, "-m", "baozicode", "member", "run", ...]`
            # Phase 5 MemberMainLoop 自己 process group;此处只占位 keep-alive
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            log.info("coroutine backend cancelled: %s/%s", self.team_name, self.member_name)
            raise

    def is_alive(self) -> bool:
        return self._task is not None and not self._task.done()

    def kill(self, *, grace_seconds: float = _DEFAULT_GRACE_SECONDS) -> None:
        """Coroutine 无 grace — 即时 cancel。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            log.info("coroutine backend cancelled: %s/%s", self.team_name, self.member_name)

    def title(self, new_title: str) -> None:
        # coroutine 没有 pane 概念;no-op
        log.debug("CoroutineBackend.title(%r) no-op", new_title)


# ---------------------------------------------------------------------------
# WorktreeCoroutineBackend
# ---------------------------------------------------------------------------


@dataclass
class WorktreeCoroutineBackend(CoroutineBackend):
    """Coroutine 之上 + git worktree 隔离。

    `spawn()`:`os.chdir(workdir)` + workdir 不存在则
    `WorktreeManager.create(name)` 初始化;后调 super().spawn() 起
    Task。Phase 5 MemberMainLoop 在该 workdir 跑 Member Agent。
    """

    workdir: str = ".worktrees/"
    setup_dir: str = ""
    project_root: str = ""

    def __init__(
        self,
        member_name: str,
        team_name: str,
        command: list[str],
        *,
        workdir: str,
        setup_dir: str = "",
        project_root: str = "",
    ) -> None:
        super().__init__(member_name=member_name, team_name=team_name, command=command)
        self.workdir = workdir
        self.setup_dir = setup_dir or project_root or os.getcwd()
        self.project_root = project_root or self.setup_dir
        self.backend_type = "worktree-coroutine"

    async def spawn(self) -> BackendHandle:
        """确保 workdir 存在 → chdir → super().spawn() 起 Task。"""
        target_dir = self._resolve_workdir()
        # 先确保 workdir 存在(顺序:worktree init → mkdir fallback)
        if not target_dir.exists():
            try:
                from baozicode.worktree import WorktreeManager
                mgr = WorktreeManager(setup_dir=Path(self.setup_dir))
                spec = await mgr.create(self.member_name)
                self.workdir = str(spec.path)
                target_dir = self._resolve_workdir()
            except Exception as e:  # noqa: BLE001
                # setup_dir 非 git repo → 退到 mkdir(exploration-only)
                log.warning(
                    "WorktreeManager.create 失败(%s),退到 mkdir:%s",
                    e, target_dir,
                )
                target_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(target_dir)
        return await super().spawn()

    def _resolve_workdir(self) -> Path:
        """workdir 可能是相对路径(`.worktrees/alice/`)或绝对。"""
        p = Path(self.workdir)
        if p.is_absolute():
            return p
        return Path(self.setup_dir) / p


__all__ = [
    "BackendHandle",
    "CoroutineBackend",
    "DEFAULT_TMUX_SESSION_PREFIX",
    "PaneITerm2Backend",
    "PaneTmuxBackend",
    "PaneWindowsTerminalBackend",
    "WorktreeCoroutineBackend",
    "tmux_session_name",
    "tmux_window_target",
]


# 延迟 import(Path 在 _resolve_workdir 用;顶上导入避免运行时 module not found)
from pathlib import Path  # noqa: E402
