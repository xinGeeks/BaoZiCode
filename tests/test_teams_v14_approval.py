"""v1.4 Team Tools — ApprovalProtocol 解析与发送测试。

覆盖 `openspec/changes/v1-4-team-tools/specs/team-management/spec.md`
中 Approval protocol 解析部分:

- parse_plan 找到 / 没找到 / 多段只取第一个
- parse_approval approve / reject / 无 reason
- send_approval 写 inbox + touch wake + reject 无 reason 抛错
- is_task_complete / is_task_failed 块识别
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from baozicode.teams import (
    ApprovalProtocol,
    Mailbox,
    Message,
    TeamStore,
    TeamsRegistry,
)
from baozicode.teams.schema import Member


# ---------------------------------------------------------------------------
# parse_plan
# ---------------------------------------------------------------------------


class TestParsePlan:
    def test_basic_plan(self) -> None:
        body = "---PLAN-3f4a7c01---\n我会先改 X\n---END---"
        assert ApprovalProtocol.parse_plan(body) == ("3f4a7c01", "我会先改 X")

    def test_no_plan_returns_none(self) -> None:
        assert ApprovalProtocol.parse_plan("plain text only") is None

    def test_empty_body_returns_none(self) -> None:
        assert ApprovalProtocol.parse_plan("") is None

    def test_multiline_body(self) -> None:
        body = (
            "---PLAN-3f4a7c01---\n"
            "step 1: foo\n"
            "step 2: bar\n"
            "step 3: baz\n"
            "---END---"
        )
        result = ApprovalProtocol.parse_plan(body)
        assert result is not None
        plan_id, plan_body = result
        assert plan_id == "3f4a7c01"
        assert "step 1: foo" in plan_body
        assert "step 3: baz" in plan_body

    def test_picks_first_plan(self) -> None:
        body = (
            "---PLAN-aaaaaaaa---\nfirst\n---END---\n"
            "---PLAN-bbbbbbbb---\nsecond\n---END---"
        )
        result = ApprovalProtocol.parse_plan(body)
        assert result is not None
        assert result[0] == "aaaaaaaa"


# ---------------------------------------------------------------------------
# parse_approval
# ---------------------------------------------------------------------------


class TestParseApproval:
    def test_approve_no_reason(self) -> None:
        assert ApprovalProtocol.parse_approval("APPROVED: 3f4a7c01") == (
            "3f4a7c01", "approve", None,
        )

    def test_reject_with_reason(self) -> None:
        assert ApprovalProtocol.parse_approval(
            "REJECTED: 3f4a7c01 不要改 X"
        ) == ("3f4a7c01", "reject", "不要改 X")

    def test_no_approval_returns_none(self) -> None:
        assert ApprovalProtocol.parse_approval("plain text") is None

    def test_empty_body_returns_none(self) -> None:
        assert ApprovalProtocol.parse_approval("") is None

    def test_bad_id_returns_none(self) -> None:
        # id 不是 8 字符 hex
        assert ApprovalProtocol.parse_approval("APPROVED: not-hex") is None


# ---------------------------------------------------------------------------
# TASK-COMPLETE / TASK-FAILED 识别
# ---------------------------------------------------------------------------


class TestTaskCompleteAndFailed:
    def test_complete_block(self) -> None:
        body = (
            "---TASK-COMPLETE-t-001---\n"
            "done: 写了 3 个测试\n"
            "---END---"
        )
        result = ApprovalProtocol.is_task_complete(body)
        assert result == ("t-001", "done: 写了 3 个测试")

    def test_failed_block(self) -> None:
        body = (
            "---TASK-FAILED-t-001---\n"
            "error: timeout\n"
            "---END---"
        )
        result = ApprovalProtocol.is_task_failed(body)
        assert result == ("t-001", "error: timeout")

    def test_no_complete_returns_none(self) -> None:
        assert ApprovalProtocol.is_task_complete("plain text") is None

    def test_no_failed_returns_none(self) -> None:
        assert ApprovalProtocol.is_task_failed("plain text") is None


# ---------------------------------------------------------------------------
# send_approval
# ---------------------------------------------------------------------------


@pytest.fixture
def member_inbox(tmp_path: Path) -> Path:
    """一个已建好的 member inbox 目录。"""
    d = tmp_path / "alice"
    d.mkdir()
    (d / "inbox.jsonl").touch()
    (d / "wake.signal").touch()
    return d


class TestSendApproval:
    def test_send_approve_writes_inbox(self, member_inbox: Path) -> None:
        ApprovalProtocol.send_approval(member_inbox, "3f4a7c01", "approve")
        messages = Mailbox.read_messages(member_inbox, "inbox")
        assert len(messages) == 1
        assert messages[0].body == "APPROVED: 3f4a7c01"
        assert messages[0].sender == "lead"

    def test_send_reject_writes_inbox(self, member_inbox: Path) -> None:
        ApprovalProtocol.send_approval(
            member_inbox, "3f4a7c01", "reject", reason="too risky"
        )
        messages = Mailbox.read_messages(member_inbox, "inbox")
        assert len(messages) == 1
        assert messages[0].body == "REJECTED: 3f4a7c01 too risky"

    def test_send_reject_requires_reason(self, member_inbox: Path) -> None:
        with pytest.raises(ValueError, match="REJECTED"):
            ApprovalProtocol.send_approval(
                member_inbox, "3f4a7c01", "reject", reason=None
            )

    def test_send_touches_wake(self, member_inbox: Path) -> None:
        wake = member_inbox / "wake.signal"
        wake.touch()
        import os, time
        old_mtime = wake.stat().st_mtime
        time.sleep(0.01)
        os.utime(wake, (old_mtime, old_mtime))

        ApprovalProtocol.send_approval(member_inbox, "3f4a7c01", "approve")
        assert wake.stat().st_mtime > old_mtime