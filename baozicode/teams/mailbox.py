"""v1.4 Team Foundation — mailbox 文件层。

公开 API:

- `Mailbox.append_message(dir, direction, msg)` — 原子追加一条消息到
  `<dir>/{inbox,outbox}.jsonl`
- `Mailbox.read_messages(dir, direction)` — 读整个 JSONL,坏行跳过,
  返回 list[Message]
- `Mailbox.read_state(dir)` — 读 `<dir>/state.json`,缺字段填默认
- `Mailbox.write_state(dir, state)` — 原子写 state.json(write-then-rename)
- `Mailbox.touch_wake(dir)` — touch `<dir>/wake.signal`(mtime 更新)
- `Mailbox.wait_for_wake(dir, *, timeout=30.0, poll_interval=0.2)` —
  异步 poll wake.signal mtime,返回 bool 是否触发;基础实现,
  `v1-4-team-pane-backend` proposal 替换 pane 端实现

设计要点:

- 每个 mailbox 文件操作都通过 `mailbox_lock(dir / ".lock")` 拿锁,
  保证跨进程并发安全。
- JSONL 追加走"写临时文件 + fsync + copyfileobj + fsync + 删临时"
  5 步,任意一步崩溃目标文件仍是合法 JSONL。
- wake.signal 是空文件,Lead touch 改 mtime;coroutine 后端也用它,
  由 Mailbox.wait_for_wake 抽象。
- read_messages 跳过坏行(JSON 解析失败),这是工程妥协 ——
  崩溃一致性已经尽力,真崩了最多丢最后一行;坏行被跳过保证读
  不挂。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import time
from pathlib import Path
from typing import Literal

from .lockfile import mailbox_lock
from .schema import (
    MemberState,
    Message,
    default_member_state,
    fill_message_timestamp,
)


Direction = Literal["inbox", "outbox"]

# 默认参数
_DEFAULT_LOCK_TIMEOUT = 5.0
_DEFAULT_LOCK_STALE = 30.0


class Mailbox:
    """mailbox 文件层 — 静态方法集合,无状态。

    所有方法都是无状态函数,接收 `<member>/` 目录路径。
    """

    # ------------------------------------------------------------------
    # JSONL append / read
    # ------------------------------------------------------------------

    @staticmethod
    def append_message(
        dir: Path,
        direction: Direction,
        msg: Message,
        *,
        lock_timeout: float = _DEFAULT_LOCK_TIMEOUT,
        lock_stale_seconds: float = _DEFAULT_LOCK_STALE,
    ) -> None:
        """原子追加一条消息到 `<dir>/{direction}.jsonl`。

        Args:
            dir: member 目录(必须是已存在的目录)
            direction: `"inbox"` / `"outbox"`
            msg: Message 实例;`timestamp=None` 时自动补 UTC now
            lock_timeout: mailbox_lock 超时(秒),默认 5s
            lock_stale_seconds: stale 锁判定(秒),默认 30s

        Steps:
            1. timestamp 自动补(None → now(UTC))
            2. 拿 `mailbox_lock(dir / ".lock")`
            3. 写临时 `.{direction}.jsonl.{pid}.{rand}` + flush + fsync
            4. shutil.copyfileobj 把临时追加到目标 + flush + fsync
            5. 删临时
            6. release 锁
        """
        # Step 1: timestamp 自动补
        msg = fill_message_timestamp(msg)

        target = dir / f"{direction}.jsonl"
        lock_path = dir / ".lock"
        line = msg.to_json_line() + "\n"

        with mailbox_lock(lock_path, timeout=lock_timeout, stale_seconds=lock_stale_seconds):
            # Step 3: 写临时文件
            rand = random.randint(0, 9999)
            tmp = dir / f".{direction}.jsonl.{os.getpid()}.{rand}"
            try:
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())

                # Step 4: append 临时 → 目标
                # 用追加模式 + copyfileobj(整批一次性追加)
                with open(target, "a", encoding="utf-8", newline="\n") as dst, open(
                    tmp, "r", encoding="utf-8", newline="\n"
                ) as src:
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
            finally:
                # Step 5: 删临时(无论前面是否异常)
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def read_messages(
        dir: Path,
        direction: Direction,
        *,
        skip_bad_lines: bool = True,
    ) -> list[Message]:
        """读 `<dir>/{direction}.jsonl` 所有消息,返回 list。

        Args:
            dir: member 目录
            direction: `"inbox"` / `"outbox"`
            skip_bad_lines: True(默认)— 坏行(JSON 解析失败)静默跳过;
                False — 抛 ValueError

        Returns:
            list[Message],顺序为 JSONL 行顺序(append 时序);文件不存在
            或 0 字节 → `[]`
        """
        target = dir / f"{direction}.jsonl"
        if not target.exists() or target.stat().st_size == 0:
            return []
        result: list[Message] = []
        with open(target, "r", encoding="utf-8", newline="\n") as f:
            for line_no, raw in enumerate(f, start=1):
                raw = raw.rstrip("\r\n")
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as e:
                    if skip_bad_lines:
                        continue
                    raise ValueError(
                        f"{target}:{line_no} JSON 解析失败: {e}"
                    ) from e
                if not isinstance(data, dict):
                    if skip_bad_lines:
                        continue
                    raise ValueError(
                        f"{target}:{line_no} 不是 JSON object: {type(data).__name__}"
                    )
                try:
                    result.append(Message.from_dict(data))
                except (ValueError, KeyError):
                    if skip_bad_lines:
                        continue
                    raise
        return result

    # ------------------------------------------------------------------
    # state.json 读写
    # ------------------------------------------------------------------

    @staticmethod
    def read_state(dir: Path) -> MemberState:
        """读 `<dir>/state.json`,缺字段填默认。

        文件不存在 / 0 字节 / 非 JSON → `default_member_state()`
        (`status="offline"`,其他 None)。
        """
        target = dir / "state.json"
        if not target.exists() or target.stat().st_size == 0:
            return default_member_state()
        try:
            text = target.read_text(encoding="utf-8")
        except OSError:
            return default_member_state()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return default_member_state()
        return MemberState.from_dict(data)

    @staticmethod
    def write_state(
        dir: Path,
        state: MemberState,
        *,
        lock_timeout: float = _DEFAULT_LOCK_TIMEOUT,
        lock_stale_seconds: float = _DEFAULT_LOCK_STALE,
    ) -> None:
        """原子写 `<dir>/state.json`(write-then-rename)。

        Args:
            dir: member 目录
            state: MemberState 实例
            lock_timeout: mailbox_lock 超时(秒),默认 5s
            lock_stale_seconds: stale 锁判定(秒),默认 30s

        Steps:
            1. 拿 mailbox_lock
            2. 写临时 `state.json.tmp.{pid}.{rand}`
            3. os.replace → state.json
            4. release 锁
        """
        lock_path = dir / ".lock"
        target = dir / "state.json"
        rand = random.randint(0, 9999)
        tmp = dir / f"state.json.tmp.{os.getpid()}.{rand}"

        with mailbox_lock(lock_path, timeout=lock_timeout, stale_seconds=lock_stale_seconds):
            try:
                tmp.write_text(
                    json.dumps(
                        state.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                # os.replace 原子(同卷)
                os.replace(tmp, target)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass

    # ------------------------------------------------------------------
    # wake.signal
    # ------------------------------------------------------------------

    @staticmethod
    def touch_wake(dir: Path) -> None:
        """touch `<dir>/wake.signal`(空文件,更新 mtime)。

        Lead 调用以通知队员"有新消息,resume 一下"。
        """
        target = dir / "wake.signal"
        # exist_ok=True:文件不存在则创建;已存在则只更新 mtime
        target.touch(exist_ok=True)

    @staticmethod
    async def wait_for_wake(
        dir: Path,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.2,
    ) -> bool:
        """异步 poll `wake.signal` mtime,触发返回 True,超时返回 False。

        基础实现 — 等 pane 的 watchdog 协程读这同一个文件。v1-4-
        team-pane-backend proposal 替换为 pane-specific 实现
        (tmux / iTerm2 / Windows Terminal 各自管 pane 唤醒)。

        Args:
            dir: member 目录
            timeout: 总等待超时(秒),默认 30s
            poll_interval: poll 间隔(秒),默认 0.2s

        Returns:
            True — wake.signal mtime 在等待期间更新
            False — 超时无更新
        """
        target = dir / "wake.signal"
        # 起始 mtime:文件不存在 → mtime=0;存在 → 当前 mtime
        if target.exists():
            initial_mtime = target.stat().st_mtime
        else:
            initial_mtime = 0.0

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if target.exists():
                current_mtime = target.stat().st_mtime
                if current_mtime > initial_mtime:
                    return True
            await asyncio.sleep(poll_interval)
        return False

    @staticmethod
    def wake_initialized(dir: Path) -> float:
        """记录 wake.signal 当前 mtime,作为 wait_for_wake 的起点。

        用于"开始监听 wake"前置:`Mailbox.wake_initialized(dir)` →
        写新消息 → `Mailbox.touch_wake(dir)` → `await
        Mailbox.wait_for_wake(dir)` 应该立即返回 True。

        Returns:
            当前 wake.signal 的 mtime(浮点秒);文件不存在返回 0.0
        """
        target = dir / "wake.signal"
        if target.exists():
            return target.stat().st_mtime
        return 0.0


__all__ = [
    "Direction",
    "Mailbox",
]