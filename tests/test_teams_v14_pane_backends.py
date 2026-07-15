"""v1.4 Pane Backend — 5 BackendType 实现 + BackendHandle Protocol 测试。

覆盖 `openspec/changes/v1-4-team-pane-backend/specs/team-management/spec.md`
中 BackendHandle Protocol + 5 实现相关 Requirement:

- `available()` 探测(probe 成功 / 失败 → True/False)
- `spawn()` 命令构造(参数 + 调用顺序)+ pane_id / pid 捕获
- `kill()` grace 链(SIGTERM → grace → SIGKILL + kill-window)
- `title()` 命令
- 探测失败 graceful degrade(返回 False 不抛)
- Protocol runtime_checkable:`isinstance(backend, BackendHandle)` True
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from baozicode.teams.pane import (
    BackendHandle,
    CoroutineBackend,
    PaneITerm2Backend,
    PaneTmuxBackend,
    PaneWindowsTerminalBackend,
    WorktreeCoroutineBackend,
    _pid_alive,
    _run_probe,
    _safe_kill,
    tmux_session_name,
    tmux_window_target,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    """生成 monkeypatch 用的 fake subprocess.run。"""

    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0] if args else [],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    return fake


@pytest.fixture
def fake_subprocess(monkeypatch):
    """monkeypatch 的 subprocess.run;默认无输出 + returncode=0。"""
    calls: list[Any] = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args[0] if args else [],
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake)
    return calls


# ---------------------------------------------------------------------------
# Helpers (tmux_session_name / tmux_window_target)
# ---------------------------------------------------------------------------


class TestTmuxHelpers:
    """Requirement: tmux session 名格式 `<prefix>-<team>`。"""

    def test_session_name_default_prefix(self) -> None:
        assert tmux_session_name("devops") == "baozicode-team-devops"

    def test_session_name_custom_prefix(self) -> None:
        assert tmux_session_name("acme", prefix="my-team") == "my-team-acme"

    def test_window_target_format(self) -> None:
        target = tmux_window_target("devops", "alice")
        assert target == "baozicode-team-devops:alice"


# ---------------------------------------------------------------------------
# _run_probe / _safe_kill / _pid_alive
# ---------------------------------------------------------------------------


class TestRunProbe:
    """`_run_probe` 把异常转换成 `CompletedProcess(returncode=-1)`。"""

    def test_timeout_returns_nonzero(self, monkeypatch) -> None:
        def boom(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0] if args else [], timeout=2)

        monkeypatch.setattr(subprocess, "run", boom)
        cp = _run_probe(["tmux", "-V"], timeout=1.0)
        assert cp.returncode == -1
        assert "timed out" in cp.stderr.lower()

    def test_filenotfound_returns_nonzero(self, monkeypatch) -> None:
        def boom(*args, **kwargs):
            raise FileNotFoundError("not on PATH")

        monkeypatch.setattr(subprocess, "run", boom)
        cp = _run_probe(["tmux", "-V"])
        assert cp.returncode == -1

    def test_success_returns_stdout(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", _fake_run(stdout="tmux 3.4", returncode=0)
        )
        cp = _run_probe(["tmux", "-V"])
        assert cp.returncode == 0
        assert cp.stdout == "tmux 3.4"


class TestSafeKill:
    """`_safe_kill` 不抛 ProcessLookupError。"""

    def test_returns_true_on_success(self, monkeypatch) -> None:
        called = []

        def fake_kill(pid, sig):
            called.append((pid, sig))

        monkeypatch.setattr(os, "kill", fake_kill)
        assert _safe_kill(123, 15) is True
        assert called == [(123, 15)]

    def test_returns_false_on_lookup_error(self, monkeypatch) -> None:
        def fake_kill(pid, sig):
            raise ProcessLookupError("no such pid")

        monkeypatch.setattr(os, "kill", fake_kill)
        assert _safe_kill(99999, 15) is False


# ---------------------------------------------------------------------------
# PaneTmuxBackend
# ---------------------------------------------------------------------------


class TestPaneTmuxBackend:
    """Requirement: tmux backend spawn / kill / title + available() probe。"""

    def test_available_true_on_tmux_present(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", _fake_run(stdout="tmux 3.4\n", returncode=0)
        )
        assert PaneTmuxBackend.available() is True

    def test_available_false_when_tmux_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1))
        assert PaneTmuxBackend.available() is False

    def test_available_false_on_exception(self, monkeypatch) -> None:
        def boom(*args, **kwargs):
            raise FileNotFoundError("no tmux")

        monkeypatch.setattr(subprocess, "run", boom)
        assert PaneTmuxBackend.available() is False

    def test_spawn_uses_placeholder_session_then_new_window(
        self, monkeypatch
    ) -> None:
        """session 不存在时占位 → 起 window → list-panes → select-window。"""
        calls: list[Any] = []

        def fake(*args, **kwargs):
            cmd = args[0] if args else []
            calls.append(cmd)
            if "has-session" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr=""
                )
            if "new-session" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr=""
                )
            if "new-window" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr=""
                )
            if "list-panes" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout="%0 12345\n", stderr=""
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake)
        backend = PaneTmuxBackend(
            member_name="alice",
            team_name="devops",
            command=["python", "-c", "pass"],
        )
        backend.spawn()
        assert backend.pane_id == "%0"
        assert backend.pid == 12345
        # 至少有 4 个调用(has / new-session / new-window / list-panes)
        assert any("has-session" in c for c in calls)
        assert any("new-session" in c for c in calls)
        assert any("new-window" in c for c in calls)
        assert any("list-panes" in c for c in calls)

    def test_spawn_session_exists_skips_placeholder(self, monkeypatch) -> None:
        calls: list[Any] = []

        def fake(*args, **kwargs):
            cmd = args[0] if args else []
            calls.append(cmd)
            if "has-session" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr=""
                )
            if "list-panes" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout="%1 99999\n", stderr=""
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake)
        backend = PaneTmuxBackend(
            member_name="bob", team_name="devops",
            command=["sleep", "1"],
        )
        backend.spawn()
        # 没有 new-session 调用
        assert not any("new-session" in c for c in calls)

    def test_spawn_new_window_failure_raises(self, monkeypatch) -> None:
        def fake(*args, **kwargs):
            cmd = args[0] if args else []
            if "has-session" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr=""
                )
            if "new-session" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr=""
                )
            if "new-window" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1,
                    stdout="", stderr="can't create window",
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake)
        backend = PaneTmuxBackend(
            member_name="alice", team_name="devops",
            command=["echo"],
        )
        with pytest.raises(RuntimeError, match="new-window"):
            backend.spawn()

    def test_kill_sends_sigterm_then_kill_window(self, monkeypatch) -> None:
        calls: list[Any] = []

        def fake(*args, **kwargs):
            cmd = args[0] if args else []
            calls.append(cmd)
            if "list-panes" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="%0 7777\n", stderr=""
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake)
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        monkeypatch.setattr(
            "baozicode.teams.pane._pid_alive",
            lambda pid: False,  # grace 内立即退出
        )
        backend = PaneTmuxBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.spawn()
        backend.kill(grace_seconds=0.1)
        cmds = [c for c in calls if c]
        assert any("kill-window" in c for c in cmds)

    def test_title_calls_select_pane(self, monkeypatch, fake_subprocess) -> None:
        backend = PaneTmuxBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.pane_id = "%0"
        backend.title("new-name")
        # 至少一个调用含 select-pane;args tuple 是 c[0][0]
        args_seen = [c[0][0] for c in fake_subprocess if c]
        assert any("select-pane" in cmd for cmd in args_seen)

    def test_is_protocol_implementation(self) -> None:
        backend = PaneTmuxBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        assert isinstance(backend, BackendHandle)


# ---------------------------------------------------------------------------
# PaneITerm2Backend
# ---------------------------------------------------------------------------


class TestPaneITerm2Backend:
    """Requirement: iTerm2 backend spawn / kill / title + available() probe。"""

    def test_available_true_when_iterm2_present(self, monkeypatch) -> None:
        monkeypatch.setattr(
            subprocess, "run", _fake_run(stdout="3.5.0", returncode=0)
        )
        assert PaneITerm2Backend.available() is True

    def test_available_false_when_no_iterm2(self, monkeypatch) -> None:
        def boom(*args, **kwargs):
            raise FileNotFoundError("osascript")

        monkeypatch.setattr(subprocess, "run", boom)
        assert PaneITerm2Backend.available() is False

    def test_spawn_creates_window_via_osascript(self, monkeypatch) -> None:
        calls: list[Any] = []

        def fake(*args, **kwargs):
            cmd = args[0] if args else []
            calls.append(cmd)
            script = cmd[2] if len(cmd) > 2 else ""
            if "create window" in script:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="42\n", stderr=""
                )
            if "unix id" in script:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="5555\n", stderr=""
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake)
        backend = PaneITerm2Backend(
            member_name="alice", team_name="devops",
            command=["python", "main.py"],
        )
        backend.spawn()
        assert backend.window_id == "42"
        assert backend.pid == 5555
        # 至少调了一次 osascript
        assert any("osascript" in c for c in calls)

    def test_spawn_failure_raises(self, monkeypatch) -> None:
        def fake(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0] if args else [],
                returncode=1, stdout="", stderr="not authorized",
            )
        monkeypatch.setattr(subprocess, "run", fake)
        backend = PaneITerm2Backend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        with pytest.raises(RuntimeError, match="create window"):
            backend.spawn()

    def test_is_alive_via_window_count(self, monkeypatch) -> None:
        def fake(*args, **kwargs):
            script = args[0][2] if len(args[0]) > 2 else ""
            if "count of window id" in script:
                return subprocess.CompletedProcess(
                    args=args[0], returncode=0, stdout="1", stderr=""
                )
            return subprocess.CompletedProcess(
                args=args[0], returncode=0, stdout="", stderr=""
            )
        monkeypatch.setattr(subprocess, "run", fake)
        backend = PaneITerm2Backend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.window_id = "42"
        assert backend.is_alive() is True

    def test_kill_closes_window(self, monkeypatch) -> None:
        calls: list[Any] = []

        def fake(*args, **kwargs):
            cmd = args[0] if args else []
            calls.append(cmd)
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        monkeypatch.setattr(subprocess, "run", fake)
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        monkeypatch.setattr(
            "baozicode.teams.pane._pid_alive", lambda pid: False
        )
        backend = PaneITerm2Backend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.window_id = "42"
        backend.pid = 9999
        backend.kill(grace_seconds=0.1)
        # 应有 close window 调用
        assert any(
            "close" in cmd[2] if len(cmd) > 2 else False for cmd in calls
        )

    def test_title_calls_set_name(self, monkeypatch, fake_subprocess) -> None:
        backend = PaneITerm2Backend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.window_id = "42"
        backend.title("new-title")
        args_seen = [c[0][0] for c in fake_subprocess if c]
        assert any(
            "set name" in cmd[2] if len(cmd) > 2 else False for cmd in args_seen
        )

    def test_is_protocol_implementation(self) -> None:
        backend = PaneITerm2Backend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        assert isinstance(backend, BackendHandle)


# ---------------------------------------------------------------------------
# PaneWindowsTerminalBackend
# ---------------------------------------------------------------------------


class TestPaneWindowsTerminalBackend:
    """Requirement: Windows Terminal backend spawn / kill + available() probe。"""

    def test_available_false_on_posix(self, monkeypatch) -> None:
        # 模拟 POSIX — platform.system 返 Linux
        import platform as _platform
        monkeypatch.setattr(_platform, "system", lambda: "Linux")
        assert PaneWindowsTerminalBackend.available() is False

    def test_available_true_on_windows_with_wt(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import platform as _platform
        monkeypatch.setattr(_platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            subprocess, "run",
            _fake_run(stdout=str(tmp_path / "wt.exe"), returncode=0),
        )
        assert PaneWindowsTerminalBackend.available() is True

    def test_available_false_when_wt_not_found(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import platform as _platform
        monkeypatch.setattr(_platform, "system", lambda: "Windows")
        monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1))
        assert PaneWindowsTerminalBackend.available() is False

    def test_spawn_creates_tab_and_parses_uuid(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import platform as _platform
        monkeypatch.setattr(_platform, "system", lambda: "Windows")
        uuid = "abc12345-6789-0abc-def0-123456789abc"
        ls_out = (
            f"Profile Name: Ubuntu\n"
            f"GUID: {uuid}\n"
            f"Title: baozicode-team-devops/alice\n"
        )

        def fake(*args, **kwargs):
            cmd = args[0] if args else []
            if "new-tab" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr=""
                )
            if cmd and "ls" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=ls_out, stderr=""
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        monkeypatch.setattr(subprocess, "run", fake)
        backend = PaneWindowsTerminalBackend(
            member_name="alice", team_name="devops",
            command=["python", "main.py"],
        )
        backend.spawn()
        assert backend.tab_uuid == uuid

    def test_parse_uuid_from_ls(self) -> None:
        uuid = "00112233-4455-6677-8899-aabbccddeeff"
        out = (
            f"Profile: PowerShell\n"
            f"GUID: {uuid}\n"
            f"Title: baozicode-team-devops/alice\n"
        )
        got = PaneWindowsTerminalBackend._parse_uuid_from_ls(
            out, "baozicode-team-devops/alice"
        )
        assert got == uuid

    def test_parse_uuid_with_braces(self) -> None:
        """Windows Terminal `wt.exe ls` 输出带花括号。"""
        uuid = "deadbeef-0000-0000-0000-000000000000"
        out = (
            f"Profile: Ubuntu\n"
            f"GUID: {{{uuid}}}\n"
            f"Title: baozicode-team-devops/alice\n"
        )
        got = PaneWindowsTerminalBackend._parse_uuid_from_ls(
            out, "baozicode-team-devops/alice"
        )
        assert got == uuid

    def test_is_alive_uuid_in_ls(self, monkeypatch, tmp_path: Path) -> None:
        uuid = "deadbeef-0000-0000-0000-000000000000"
        monkeypatch.setattr(
            subprocess, "run",
            _fake_run(stdout=f"GUID: {uuid}\nTitle: foo\n", returncode=0),
        )
        backend = PaneWindowsTerminalBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.tab_uuid = uuid
        assert backend.is_alive() is True

    def test_kill_close_tab(self, monkeypatch, fake_subprocess) -> None:
        backend = PaneWindowsTerminalBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.tab_uuid = "deadbeef-0000-0000-0000-000000000000"
        backend.kill(grace_seconds=0.1)
        args_seen = [c[0][0] for c in fake_subprocess if c]
        assert any("close-tab" in cmd for cmd in args_seen)

    def test_title_noop(self, monkeypatch, fake_subprocess) -> None:
        backend = PaneWindowsTerminalBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.tab_uuid = "deadbeef-0000-0000-0000-000000000000"
        backend.title("new-title")  # no-op (WT 无 native rename)
        # fake_subprocess 不应被调用(title no-op)
        assert len(fake_subprocess) == 0

    def test_is_protocol_implementation(self) -> None:
        backend = PaneWindowsTerminalBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        assert isinstance(backend, BackendHandle)


# ---------------------------------------------------------------------------
# CoroutineBackend
# ---------------------------------------------------------------------------


class TestCoroutineBackend:
    """Requirement: coroutine backend 永远可用 + spawn 起 Task + 即时 cancel。"""

    def test_available_always_true(self) -> None:
        assert CoroutineBackend.available() is True

    def test_spawn_creates_task(self) -> None:
        async def run() -> None:
            backend = CoroutineBackend(
                member_name="alice", team_name="devops", command=["echo"],
            )
            handle = await backend.spawn()
            assert handle is backend
            assert backend._task is not None
            assert not backend._task.done()
            # 取消并让 event loop 处理 cancellation
            backend.kill()
            # 等一个 cycle 让 task 收到 cancel
            with pytest.raises(asyncio.CancelledError):
                await backend._task
            assert backend._task.cancelled() or backend._task.done()
        asyncio.run(run())

    def test_is_alive_true_then_false(self) -> None:
        async def run() -> None:
            backend = CoroutineBackend(
                member_name="alice", team_name="devops", command=["echo"],
            )
            assert backend.is_alive() is False  # 还没 spawn
            await backend.spawn()
            assert backend.is_alive() is True
            backend.kill()
            with pytest.raises(asyncio.CancelledError):
                await backend._task
            # 取消后 done
            assert backend.is_alive() is False
        asyncio.run(run())

    def test_kill_noop_when_no_task(self) -> None:
        """没起 task 时 kill 不挂。"""
        backend = CoroutineBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.kill()  # 不抛

    def test_pid_is_none(self) -> None:
        backend = CoroutineBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        assert backend.pid is None

    def test_title_noop(self) -> None:
        backend = CoroutineBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        backend.title("anything")  # 不抛

    def test_is_protocol_implementation(self) -> None:
        backend = CoroutineBackend(
            member_name="alice", team_name="devops", command=["echo"],
        )
        assert isinstance(backend, BackendHandle)


# ---------------------------------------------------------------------------
# WorktreeCoroutineBackend
# ---------------------------------------------------------------------------


class TestWorktreeCoroutineBackend:
    """Requirement: chdir 到 workdir + WorktreeManager.create() 初始化 + spawn。"""

    def test_resolve_workdir_relative(self, tmp_path: Path) -> None:
        backend = WorktreeCoroutineBackend(
            member_name="alice", team_name="devops",
            command=["echo"],
            workdir=".worktrees/alice",
            setup_dir=str(tmp_path),
        )
        assert backend._resolve_workdir() == tmp_path / ".worktrees/alice"

    def test_resolve_workdir_absolute(self, tmp_path: Path) -> None:
        backend = WorktreeCoroutineBackend(
            member_name="alice", team_name="devops",
            command=["echo"],
            workdir=str(tmp_path),
        )
        assert backend._resolve_workdir() == tmp_path

    def test_spawn_chdir_and_mkdir_fallback(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """setup_dir 非 git repo → WorktreeManager.create 失败 → mkdir。"""
        # 让 WorktreeManager.create 抛(不在 git repo)
        from baozicode.worktree import WorktreeNotInRepoError

        class FakeMgr:
            def __init__(self, *, setup_dir: Path) -> None:
                raise WorktreeNotInRepoError(str(setup_dir))

        monkeypatch.setattr(
            "baozicode.worktree.WorktreeManager", FakeMgr
        )
        # 替 _run 占位 — 改用更快 sleep
        target_dir = tmp_path / ".worktrees" / "alice"
        backend = WorktreeCoroutineBackend(
            member_name="alice", team_name="devops",
            command=["echo"],
            workdir=str(target_dir),
            setup_dir=str(tmp_path),
        )
        # _resolve_workdir 应当返回 target_dir

        async def run() -> None:
            await backend.spawn()
        asyncio.run(run())
        # 应当 mkdir 落到 target_dir(因为非 git repo)
        assert target_dir.exists()
        # chdir 到 target_dir
        assert Path(os.getcwd()) == target_dir

    def test_is_protocol_implementation(self) -> None:
        backend = WorktreeCoroutineBackend(
            member_name="alice", team_name="devops",
            command=["echo"], workdir=".worktrees/alice",
        )
        assert isinstance(backend, BackendHandle)

    def test_backend_type_override(self) -> None:
        backend = WorktreeCoroutineBackend(
            member_name="alice", team_name="devops",
            command=["echo"], workdir=".worktrees/alice",
        )
        assert backend.backend_type == "worktree-coroutine"


# ---------------------------------------------------------------------------
# BackendHandle Protocol runtime_checkable
# ---------------------------------------------------------------------------


class TestBackendHandleProtocol:
    """Protocol runtime_checkable — 所有 5 个 backend 都通过 isinstance。"""

    @pytest.mark.parametrize(
        "backend_cls",
        [
            PaneTmuxBackend,
            PaneITerm2Backend,
            PaneWindowsTerminalBackend,
            CoroutineBackend,
            WorktreeCoroutineBackend,
        ],
    )
    def test_satisfies_protocol(self, backend_cls) -> None:
        # WorktreeCoroutineBackend 构造签名不同 — 适配
        if backend_cls is WorktreeCoroutineBackend:
            b = backend_cls(
                member_name="alice", team_name="devops",
                command=["echo"], workdir=".worktrees/alice",
            )
        else:
            b = backend_cls(
                member_name="alice", team_name="devops", command=["echo"],
            )
        assert isinstance(b, BackendHandle)
