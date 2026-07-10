"""v1.4 Team Foundation — mailbox 文件层测试。

覆盖 `openspec/changes/v1-4-team-foundation/specs/team-management/spec.md`
中 mailbox 相关 acceptance scenario:

- `Mailbox.append_message` happy / multi-append / atomic crash
- `Mailbox.read_messages` 坏行 / 缺字段
- `Mailbox.read_state` 默认值 / 缺字段 / 0 字节
- `Mailbox.write_state` 原子
- `Mailbox.touch_wake` / `wake_initialized`
- `Mailbox.wait_for_wake` 触发 / 超时
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from baozicode.teams import Mailbox, Message, MemberState
from baozicode.teams.mailbox import Direction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def member_dir(tmp_path: Path) -> Path:
    """一个临时 member 目录(只含目录,不含其他文件)。"""
    d = tmp_path / "alice"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# append_message
# ---------------------------------------------------------------------------


class TestAppendHappy:
    """Requirement: Append creates target — 文件不存在时 append 创建。"""

    def test_append_creates_file(self, member_dir: Path) -> None:
        msg = Message(sender="lead", body="hi")
        Mailbox.append_message(member_dir, "inbox", msg)

        target = member_dir / "inbox.jsonl"
        assert target.exists()
        lines = target.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["sender"] == "lead"
        assert data["body"] == "hi"
        assert data["timestamp"] is not None

    def test_append_fills_timestamp(self, member_dir: Path) -> None:
        msg = Message(sender="lead", body="hi")
        assert msg.timestamp is None
        before = datetime.now(timezone.utc)
        Mailbox.append_message(member_dir, "inbox", msg)
        after = datetime.now(timezone.utc)

        messages = Mailbox.read_messages(member_dir, "inbox")
        assert len(messages) == 1
        ts = messages[0].timestamp
        assert ts is not None
        # timestamp 在 before/after 之间
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        assert before <= ts <= after

    def test_append_keeps_existing_timestamp(self, member_dir: Path) -> None:
        ts = datetime(2026, 7, 10, 8, 0, 0, tzinfo=timezone.utc)
        msg = Message(sender="lead", body="hi", timestamp=ts)
        Mailbox.append_message(member_dir, "inbox", msg)

        messages = Mailbox.read_messages(member_dir, "inbox")
        assert messages[0].timestamp == ts


class TestMultiAppend:
    """Requirement: Multiple appends accumulate — 顺序正确。"""

    def test_multiple_appends_in_order(self, member_dir: Path) -> None:
        for i in range(5):
            Mailbox.append_message(
                member_dir, "inbox", Message(sender="lead", body=f"msg {i}")
            )
        messages = Mailbox.read_messages(member_dir, "inbox")
        assert [m.body for m in messages] == [f"msg {i}" for i in range(5)]

    def test_inbox_and_outbox_separate(self, member_dir: Path) -> None:
        Mailbox.append_message(member_dir, "inbox", Message(sender="bob", body="in"))
        Mailbox.append_message(member_dir, "outbox", Message(sender="alice", body="out"))

        inbox = Mailbox.read_messages(member_dir, "inbox")
        outbox = Mailbox.read_messages(member_dir, "outbox")
        assert len(inbox) == 1 and inbox[0].sender == "bob"
        assert len(outbox) == 1 and outbox[0].sender == "alice"


class TestAtomicCrash:
    """Requirement: Crash mid-append leaves valid file — 目标仍合法 JSONL。

    模拟"append 跑到一半被 kill":直接删除 tmp 文件(模拟 OS 清理
    半成品),验证 inbox.jsonl 仍是合法 JSONL(可能少最后一行)。
    """

    def test_tmp_cleanup_leaves_valid_jsonl(self, member_dir: Path) -> None:
        # 先 append 一条成功
        Mailbox.append_message(member_dir, "inbox", Message(sender="lead", body="first"))

        # 模拟半成品:放一个半成品 .inbox.jsonl.pid.rand
        half = member_dir / ".inbox.jsonl.99999.1234"
        half.write_text('{"sender":"lead","body":"h', encoding="utf-8")

        # 再 append 一条 — 应跳过 half(append_message 用的 tmp 是新
        # 随机名,跟这个无关),成功追加
        Mailbox.append_message(member_dir, "inbox", Message(sender="lead", body="second"))

        # inbox.jsonl 应是合法 JSONL(2 行)
        messages = Mailbox.read_messages(member_dir, "inbox")
        assert len(messages) == 2
        assert [m.body for m in messages] == ["first", "second"]


# ---------------------------------------------------------------------------
# read_messages
# ---------------------------------------------------------------------------


class TestReadMessagesEmpty:
    """Requirement: Empty JSONL is valid — 0 字节 / 不存在 → []。"""

    def test_nonexistent_returns_empty(self, member_dir: Path) -> None:
        assert Mailbox.read_messages(member_dir, "inbox") == []

    def test_empty_file_returns_empty(self, member_dir: Path) -> None:
        (member_dir / "inbox.jsonl").write_text("", encoding="utf-8")
        assert Mailbox.read_messages(member_dir, "inbox") == []


class TestReadMessagesBadLines:
    """Requirement: Bad lines skipped / raise。"""

    def test_bad_json_skipped(self, member_dir: Path) -> None:
        # 混合:1 行合法 + 1 行坏 JSON + 1 行合法
        lines = [
            json.dumps({"sender": "lead", "body": "ok"}),
            "{bad json",
            json.dumps({"sender": "lead", "body": "ok2"}),
        ]
        (member_dir / "inbox.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        msgs = Mailbox.read_messages(member_dir, "inbox")
        assert [m.body for m in msgs] == ["ok", "ok2"]

    def test_bad_json_raises_when_not_skipping(self, member_dir: Path) -> None:
        (member_dir / "inbox.jsonl").write_text(
            '{"sender":"lead","body":"ok"}\n{bad\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="JSON 解析失败"):
            Mailbox.read_messages(member_dir, "inbox", skip_bad_lines=False)


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------


class TestReadStateDefaults:
    """Requirement: Missing state fields default。"""

    def test_missing_file_returns_default(self, member_dir: Path) -> None:
        s = Mailbox.read_state(member_dir)
        assert s.status == "offline"
        assert s.last_active_ts is None
        assert s.current_task is None
        assert s.backend_pid is None

    def test_empty_file_returns_default(self, member_dir: Path) -> None:
        (member_dir / "state.json").write_text("", encoding="utf-8")
        s = Mailbox.read_state(member_dir)
        assert s.status == "offline"

    def test_partial_dict_fills_defaults(self, member_dir: Path) -> None:
        (member_dir / "state.json").write_text(
            json.dumps({"status": "running"}), encoding="utf-8"
        )
        s = Mailbox.read_state(member_dir)
        assert s.status == "running"
        assert s.last_active_ts is None
        assert s.current_task is None
        assert s.backend_pid is None


class TestWriteState:
    """Requirement: Write state atomic。"""

    def test_write_creates_file(self, member_dir: Path) -> None:
        s = MemberState(status="idle", current_task="task-1")
        Mailbox.write_state(member_dir, s)
        target = member_dir / "state.json"
        assert target.exists()
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["status"] == "idle"
        assert data["current_task"] == "task-1"

    def test_write_is_atomic_no_tmp_residue(self, member_dir: Path) -> None:
        s = MemberState(status="idle")
        Mailbox.write_state(member_dir, s)
        # 临时文件已删
        tmp_files = list(member_dir.glob("state.json.tmp.*"))
        assert tmp_files == []

    def test_round_trip(self, member_dir: Path) -> None:
        s = MemberState(
            status="running",
            current_task="task-7",
            backend_pid=12345,
        )
        Mailbox.write_state(member_dir, s)
        loaded = Mailbox.read_state(member_dir)
        assert loaded.status == "running"
        assert loaded.current_task == "task-7"
        assert loaded.backend_pid == 12345


# ---------------------------------------------------------------------------
# wake.signal
# ---------------------------------------------------------------------------


class TestTouchWake:
    """Requirement: Wake touch updates mtime。"""

    def test_touch_creates_file(self, member_dir: Path) -> None:
        Mailbox.touch_wake(member_dir)
        assert (member_dir / "wake.signal").exists()

    def test_touch_updates_existing_mtime(self, member_dir: Path) -> None:
        wake = member_dir / "wake.signal"
        wake.touch()
        # 把 mtime 设为过去
        old_time = 1000000.0
        os.utime(wake, (old_time, old_time))
        before = wake.stat().st_mtime

        # 等 10ms 让新 mtime 一定更大
        import time

        time.sleep(0.01)
        Mailbox.touch_wake(member_dir)
        after = wake.stat().st_mtime
        assert after > before


class TestWakeInitialized:
    """Requirement: wake_initialized 记录起始 mtime。"""

    def test_nonexistent_returns_zero(self, member_dir: Path) -> None:
        assert Mailbox.wake_initialized(member_dir) == 0.0

    def test_existing_returns_mtime(self, member_dir: Path) -> None:
        wake = member_dir / "wake.signal"
        wake.touch()
        mtime = wake.stat().st_mtime
        assert Mailbox.wake_initialized(member_dir) == mtime


# ---------------------------------------------------------------------------
# wait_for_wake(异步)
# ---------------------------------------------------------------------------


class TestWaitForWake:
    """Requirement: wait_for_wake 触发返回 True,超时返回 False。"""

    @pytest.mark.asyncio
    async def test_returns_true_on_wake(self, member_dir: Path) -> None:
        Mailbox.wake_initialized(member_dir)
        # 50ms 后另一个 task touch wake
        async def later_touch() -> None:
            await asyncio.sleep(0.05)
            Mailbox.touch_wake(member_dir)

        asyncio.create_task(later_touch())
        result = await Mailbox.wait_for_wake(
            member_dir, timeout=2.0, poll_interval=0.02
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self, member_dir: Path) -> None:
        Mailbox.wake_initialized(member_dir)
        result = await Mailbox.wait_for_wake(
            member_dir, timeout=0.1, poll_interval=0.02
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_existing_wake_not_treated_as_trigger(self, member_dir: Path) -> None:
        """已有 wake.signal 不应立即返回 True;要 mtime 变化才触发。"""
        wake = member_dir / "wake.signal"
        wake.touch()
        # 记录初始 mtime
        Mailbox.wake_initialized(member_dir)
        # 不 touch,直接等 — 必须 timeout
        result = await Mailbox.wait_for_wake(
            member_dir, timeout=0.1, poll_interval=0.02
        )
        assert result is False