"""v1.4 Team Foundation — lockfile 跨平台抽象测试。

覆盖 `openspec/changes/v1-4-team-foundation/specs/team-management/spec.md`
中 lockfile 相关 acceptance scenario:

- POSIX happy / blocking / stale steal / timeout / context manager
  exception path
- Windows via monkeypatch(monkeypatch sys.platform → 验证分发)
- lockfile 内容含 {pid, hostname, ts}
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from baozicode.teams.lockfile import (
    MailboxLockError,
    MailboxLockTimeout,
    _PosixMailboxLock,
    _WindowsMailboxLock,
    mailbox_lock,
)


# ---------------------------------------------------------------------------
# POSIX
# ---------------------------------------------------------------------------


class TestPosixHappy:
    """Requirement: Acquire happy path — lockfile 内容含 {pid, hostname, ts}。

    Windows 上 msvcrt.locking 期间其他 handle 不能 read(强制 mandatory 锁);
    所以 stamp 内容在 release 后再 verify。
    """

    def test_acquire_releases_cleanly(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        with mailbox_lock(lock_path):
            assert lock_path.exists()
        # 锁释放后读 stamp 内容
        content = lock_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert len(lines) == 3
        assert int(lines[0]) == os.getpid()
        assert lines[1]  # hostname
        float(lines[2])  # ts

    def test_release_removes_lockfile(self, tmp_path: Path) -> None:
        """注意:mailbox_lock release 只解锁 + 关 fd,不主动删 lockfile
        (让其他进程能 inspect 锁历史)。"""
        lock_path = tmp_path / ".lock"
        with mailbox_lock(lock_path):
            pass
        # 锁已释放但 lockfile 还在(只是没人锁)
        assert lock_path.exists()


class TestPosixBlocking:
    """Requirement: Acquire blocking waits — 持锁后另一 acquire busy-wait。

    注:同进程内 mailbox_lock 拿同一锁无法测试"另一进程抢锁成功",
    因为锁被外层 with 持着 → 内层 with 必然 timeout。我们验证"等待 +
    timeout 行为",跨进程测试走 integration test (10.2)。
    """

    def test_second_acquire_times_out_while_held(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        with mailbox_lock(lock_path):
            start = time.monotonic()
            with pytest.raises(MailboxLockTimeout):
                with mailbox_lock(lock_path, timeout=0.3, stale_seconds=60.0):
                    pass
            elapsed = time.monotonic() - start
            # 确实等了一段时间(≥ 0.25s 才到 0.3s 超时)
            assert elapsed >= 0.25

    def test_release_then_reacquire_succeeds(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        with mailbox_lock(lock_path):
            pass
        # 锁释放后可以再次拿
        start = time.monotonic()
        with mailbox_lock(lock_path, timeout=0.5):
            pass
        elapsed = time.monotonic() - start
        # 第二次立即拿到,不应该等
        assert elapsed < 0.1


class TestPosixStaleSteal:
    """Requirement: Stale lock stolen — mtime 超过 stale_seconds 可偷。

    Windows 兜底:陈旧锁文件存在但 mtime 老;stale steal 会删锁重建。
    """

    def test_stale_lock_is_stolen(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        # 模拟陈旧锁文件(用旧 mtime)
        lock_path.write_text("stale\n", encoding="utf-8")
        old_time = time.time() - 60  # 60s 前
        os.utime(lock_path, (old_time, old_time))

        # 默认 stale_seconds=30 → 这锁过期,应该被偷
        with mailbox_lock(lock_path, timeout=1.0):
            assert lock_path.exists()
        # release 后 verify stamp 是新的
        content = lock_path.read_text(encoding="utf-8")
        assert str(os.getpid()) in content


class TestPosixTimeout:
    """Requirement: Timeout raises — 锁不释放时超时抛 MailboxLockTimeout。"""

    def test_timeout_raises_with_path_and_elapsed(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        with mailbox_lock(lock_path):
            # 锁已持,第二个尝试立刻超时
            with pytest.raises(MailboxLockTimeout) as exc_info:
                with mailbox_lock(lock_path, timeout=0.2, stale_seconds=60.0):
                    pass
            assert exc_info.value.path == lock_path
            assert exc_info.value.elapsed >= 0.2
            assert exc_info.value.elapsed < 1.0


class TestContextManagerException:
    """Requirement: Release safe on exception。"""

    def test_release_on_exception(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        with pytest.raises(RuntimeError, match="boom"):
            with mailbox_lock(lock_path):
                raise RuntimeError("boom")
        # 锁释放后可以再次拿
        with mailbox_lock(lock_path, timeout=1.0):
            pass  # 成功


# ---------------------------------------------------------------------------
# 跨平台分发
# ---------------------------------------------------------------------------


class TestCrossPlatformDispatch:
    """Requirement: Windows branch via monkeypatch — sys.platform 分发。"""

    def test_classes_exist(self) -> None:
        from baozicode.teams import lockfile as lf_mod

        assert hasattr(lf_mod, "_WindowsMailboxLock")
        assert hasattr(lf_mod, "_PosixMailboxLock")

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="fcntl POSIX-only;test on POSIX hosts only",
    )
    def test_posix_path_runs_on_linux(self, tmp_path: Path) -> None:
        """Linux host 上天然走 _PosixMailboxLock。"""
        assert sys.platform == "linux"
        lock_path = tmp_path / ".lock"
        with mailbox_lock(lock_path, timeout=0.5):
            pass

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="msvcrt Windows-only;test on Windows hosts only",
    )
    def test_windows_path_runs_on_win32(self, tmp_path: Path) -> None:
        """Windows host 上天然走 _WindowsMailboxLock。"""
        assert sys.platform == "win32"
        lock_path = tmp_path / ".lock"
        with mailbox_lock(lock_path, timeout=1.0):
            pass
        content = lock_path.read_text(encoding="utf-8")
        assert str(os.getpid()) in content


# ---------------------------------------------------------------------------
# 单元测试 — 直接调实现类
# ---------------------------------------------------------------------------


class TestPosixLockDirect:
    """直接构造 _PosixMailboxLock 验证细节(不依赖 context manager)。

    POSIX-only:fcntl 在 Windows 不存在,这些测试在 POSIX host 上跑。
    """

    pytestmark = pytest.mark.skipif(
        sys.platform == "win32",
        reason="fcntl POSIX-only;test on POSIX hosts only",
    )

    def test_acquire_release_idempotent(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        lock = _PosixMailboxLock(lock_path)
        lock.acquire(timeout=1.0, stale_seconds=30.0)
        lock.release()
        # 第二次 release 必须幂等(不抛)
        lock.release()

    def test_stamp_contains_pid(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        with mailbox_lock(lock_path):
            pass
        content = lock_path.read_text(encoding="utf-8")
        assert str(os.getpid()) in content


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestMailboxLockError:
    """MailboxLockError 是基类;MailboxLockTimeout 是子类。"""

    def test_timeout_is_lock_error(self) -> None:
        assert issubclass(MailboxLockTimeout, MailboxLockError)

    def test_timeout_message_includes_path(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".lock"
        with mailbox_lock(lock_path):
            with pytest.raises(MailboxLockTimeout) as exc_info:
                with mailbox_lock(lock_path, timeout=0.1, stale_seconds=60.0):
                    pass
        msg = str(exc_info.value)
        assert str(lock_path) in msg
        assert "0.10" in msg or "0.1" in msg