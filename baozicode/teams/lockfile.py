"""v1.4 Team Foundation — 跨平台 mailbox lockfile 抽象。

公开 API:

- `mailbox_lock(path, *, timeout=5.0, stale_seconds=30.0)` —— context
  manager,按 `sys.platform` 分发到 POSIX / Windows 实现。
- `MailboxLockTimeout` —— 拿锁超时抛。
- `MailboxLockStolen` —— stale 锁被偷(目前仅内部用,可作 debug)。

跨平台差异:

- **POSIX** 用 `fcntl.flock(fd, LOCK_EX | LOCK_NB)` — advisory 锁,跨
  进程可见,失败抛 `BlockingIOError`。50ms 退避重试,stale 偷锁。
- **Windows** 用 `msvcrt.locking(fd, LK_NBLCK, 1)` — mandatory 锁,锁 1
  字节。失败抛 `OSError`。同样的 stale 偷锁。
- 两种实现都用 `os.O_BINARY` 打开(Windows 上避免 newline 转换)。

Lockfile 内容:`{pid}\\n{hostname}\\n{ts}\\n` — 落盘后便于 debug 谁持
锁,不影响协议(锁本身由 fcntl/msvcrt 管,内容仅是元信息)。

设计取舍:

- **stale 默认 30s**:够长覆盖正常 IO 卡顿,够短覆盖进程崩死。
- **timeout 默认 5s**:够长覆盖轮询 IO 重试,够短让 LLM 等不到时不
  至于 hang。
- 拿不到锁 + 已 stale → 删 lockfile + 重建 + 重试一次;仍拿不到 →
  超时抛。
- 写 lockfile 内容时 `os.fsync` 落盘 — 确保后续 `stat().st_mtime` 可信。
"""

from __future__ import annotations

import os
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol


# ---------------------------------------------------------------------------
# 错误枚举
# ---------------------------------------------------------------------------


class MailboxLockError(Exception):
    """lockfile 操作失败基类。"""


class MailboxLockTimeout(MailboxLockError):
    """`mailbox_lock` 在 `timeout` 内没拿到锁。"""

    def __init__(self, path: Path, elapsed: float, message: str = "") -> None:
        self.path = path
        self.elapsed = elapsed
        super().__init__(
            f"mailbox lock 超时 ({elapsed:.2f}s) at {path}: {message}".rstrip(": ")
        )


class MailboxLockStolen(MailboxLockError):
    """stale lock 被偷(目前仅内部 retry 路径用)。"""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class MailboxLock(Protocol):
    """mailbox lock 抽象 — POSIX / Windows 各自实现。

    `acquire` 阻塞直到拿锁 / 超时 / 抛异常;`release` 必须幂等。
    """

    def acquire(
        self, *, timeout: float, stale_seconds: float
    ) -> None: ...

    def release(self) -> None: ...


# ---------------------------------------------------------------------------
# POSIX 实现
# ---------------------------------------------------------------------------


class _PosixMailboxLock:
    """POSIX `fcntl.flock` 实现。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self, *, timeout: float, stale_seconds: float) -> None:
        flags = os.O_CREAT | os.O_RDWR
        # POSIX flock 在 Linux 上 lockfile 创建是 advisory,但文件必须存在。
        # mode 0o644 给个标准读权限,container 里非 root 也读得到。
        fd = os.open(str(self.path), flags, 0o644)
        try:
            deadline = time.monotonic() + timeout
            attempts = 0
            while True:
                try:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    self._stamp_lockfile()
                    return
                except (BlockingIOError, OSError) as e:
                    # fcntl flock 失败抛 BlockingIOError;OSError 兜底
                    if not _is_lock_busy_error(e):
                        os.close(fd)
                        raise MailboxLockError(
                            f"unexpected flock error at {self.path}: {e}"
                        ) from e
                    # 拿不到 → 检查 stale
                    attempts += 1
                    if attempts == 1 and _is_lockfile_stale(
                        self.path, stale_seconds
                    ):
                        # stale:删锁 + 重试一次
                        try:
                            os.unlink(self.path)
                        except FileNotFoundError:
                            pass
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        fd = os.open(str(self.path), flags, 0o644)
                        continue
                    if time.monotonic() >= deadline:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        raise MailboxLockTimeout(
                            self.path,
                            elapsed=timeout,
                            message=str(e),
                        ) from e
                    # 退避 50ms
                    time.sleep(0.05)
        except Exception:
            try:
                os.close(fd)
            except (OSError, NameError):
                pass
            raise

    def _stamp_lockfile(self) -> None:
        """写 {pid}\\n{hostname}\\n{ts}\\n 到 lockfile + fsync。

        注意:`self._fd` 必须在 acquire 成功后才有值。"""
        assert self._fd is not None
        payload = (
            f"{os.getpid()}\n{socket.gethostname()}\n{time.time()}\n".encode("utf-8")
        )
        # 把文件截到 0 再写(避免上次内容残留)
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, payload)
        os.fsync(self._fd)

    def release(self) -> None:
        if self._fd is not None:
            try:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except (OSError, ImportError):
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


# ---------------------------------------------------------------------------
# Windows 实现
# ---------------------------------------------------------------------------


class _WindowsMailboxLock:
    """Windows `msvcrt.locking` 实现。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self, *, timeout: float, stale_seconds: float) -> None:
        # O_BINARY 避免 newline 转换(Windows 必需)
        flags = os.O_CREAT | os.O_RDWR | os.O_BINARY
        fd = os.open(str(self.path), flags, 0o644)
        try:
            import msvcrt

            deadline = time.monotonic() + timeout
            attempts = 0
            while True:
                try:
                    # msvcrt.locking 锁 1 字节(从当前位置)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    self._fd = fd
                    self._stamp_lockfile()
                    return
                except OSError as e:
                    # msvcrt.locking 拿不到锁抛 OSError(WinError 33 / 158)
                    if not _is_lock_busy_error(e):
                        os.close(fd)
                        raise MailboxLockError(
                            f"unexpected msvcrt locking error at {self.path}: {e}"
                        ) from e
                    attempts += 1
                    if attempts == 1 and _is_lockfile_stale(
                        self.path, stale_seconds
                    ):
                        try:
                            os.unlink(self.path)
                        except FileNotFoundError:
                            pass
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        fd = os.open(str(self.path), flags, 0o644)
                        continue
                    if time.monotonic() >= deadline:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        raise MailboxLockTimeout(
                            self.path,
                            elapsed=timeout,
                            message=str(e),
                        ) from e
                    time.sleep(0.05)
        except Exception:
            try:
                os.close(fd)
            except (OSError, NameError):
                pass
            raise

    def _stamp_lockfile(self) -> None:
        assert self._fd is not None
        payload = (
            f"{os.getpid()}\n{socket.gethostname()}\n{time.time()}\n".encode("utf-8")
        )
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.ftruncate(self._fd, 0)
            os.write(self._fd, payload)
            os.fsync(self._fd)
        except OSError:
            # Windows 上 fsync 可能抛(noop);吞掉
            pass

    def release(self) -> None:
        if self._fd is not None:
            try:
                import msvcrt

                # 解锁同一字节(LK_UNLCK + 1)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            except (OSError, ImportError):
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


# ---------------------------------------------------------------------------
# 共享 helper
# ---------------------------------------------------------------------------


def _is_lock_busy_error(err: BaseException) -> bool:
    """判断异常是"锁被占"还是别的 IO 错。

    Windows msvcrt.locking 锁冲突时返回的 errno 不固定(13 / 33 / 158 都
    可能,取决于 Windows 版本 + 调用栈上下文);errno 13 在 msvcrt.locking
    场景下几乎都是 lock conflict(ACL Permission denied 不会到这里)。
    """
    # POSIX BlockingIOError → 锁被占
    if isinstance(err, BlockingIOError):
        return True
    if isinstance(err, OSError):
        # Windows: errno 33 = lock conflict, 158 = ERROR_LOCK_VIOLATION,
        # 13 = Permission denied(msvcrt.locking 语境下也是锁冲突)
        if hasattr(err, "winerror") and err.winerror in (13, 33, 158):
            return True
        # 兜底:errno 11 (EAGAIN) / 35 (EDEADLK on Linux)
        if err.errno in (11, 13, 33, 35, 158):
            return True
    return False


def _is_lockfile_stale(path: Path, stale_seconds: float) -> bool:
    """lockfile mtime < now - stale_seconds → 视为过期可偷。"""
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    age = time.time() - st.st_mtime
    return age > stale_seconds


# ---------------------------------------------------------------------------
# 公开 context manager
# ---------------------------------------------------------------------------


@contextmanager
def mailbox_lock(
    path: Path, *, timeout: float = 5.0, stale_seconds: float = 30.0
) -> Iterator[None]:
    """跨平台 mailbox lockfile context manager。

    Args:
        path: lockfile 路径(通常是 `<dir>/.lock`)
        timeout: 拿锁超时(秒),默认 5s
        stale_seconds: stale 锁判定阈值(秒),默认 30s

    Yields:
        None — 拿到锁后执行,异常路径自动 release。

    Raises:
        MailboxLockTimeout: 超时仍拿不到锁
        MailboxLockError: 其他 lockfile 操作失败
    """
    if sys.platform == "win32":
        lock: MailboxLock = _WindowsMailboxLock(path)
    else:
        lock = _PosixMailboxLock(path)

    acquired = False
    try:
        lock.acquire(timeout=timeout, stale_seconds=stale_seconds)
        acquired = True
        yield
    finally:
        if acquired:
            lock.release()


__all__ = [
    "MailboxLock",
    "MailboxLockError",
    "MailboxLockStolen",
    "MailboxLockTimeout",
    "mailbox_lock",
]