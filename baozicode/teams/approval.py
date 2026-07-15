"""v1.4 Team Tools — Approval Protocol helpers。

公开 API:

- `ApprovalProtocol.parse_plan(body) -> tuple[str, str] | None`
- `ApprovalProtocol.parse_approval(body) -> tuple[str, str, str | None] | None`
- `ApprovalProtocol.send_approval(inbox_dir, plan_id, action, reason=None) -> None`
- `ApprovalProtocol.is_task_complete(body) -> tuple[str, str] | None`
- `ApprovalProtocol.is_task_failed(body) -> tuple[str, str] | None`

设计要点:

- 所有协议走 plain text + `---XXX-<id>---` 分隔符;Mailbox / Message
  schema 不变(无新字段)
- PLAN 块定界:`---PLAN-<id>---` 开 + `---END---` 闭,中间是 plan body
- APPROVED / REJECTED 单行:`APPROVED: <id>` 或 `REJECTED: <id> <reason>`
- `send_approval` 走 `Mailbox.append_message + touch_wake`,跟普通消息
  路径一致
- TASK-COMPLETE / TASK-FAILED 与 PLAN 同一分隔符风格(块式),
  MailboxNotifier 扫描时识别
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from .mailbox import Mailbox
from .schema import Message

log = logging.getLogger(__name__)

# 类型字面量
ApprovalAction = Literal["approve", "reject"]


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

# ---PLAN-<id>---\n...body...\n---END---
_PLAN_HEADER_RE = re.compile(r"^---PLAN-([a-f0-9]{8})---\s*$", re.MULTILINE)
_PLAN_END_RE = re.compile(r"^---END---\s*$", re.MULTILINE)
# APPROVED: <id>  / REJECTED: <id> <reason>
_APPROVAL_RE = re.compile(
    r"^(APPROVED|REJECTED):\s+([a-f0-9]{8})(?:\s+(.+?))?\s*$",
    re.MULTILINE,
)
# ---TASK-COMPLETE-<id>---  /  ---TASK-FAILED-<id>---
_TASK_COMPLETE_RE = re.compile(
    r"^---TASK-COMPLETE-([a-z0-9-]+)---\s*$", re.MULTILINE
)
_TASK_FAILED_RE = re.compile(
    r"^---TASK-FAILED-([a-z0-9-]+)---\s*$", re.MULTILINE
)


class ApprovalProtocol:
    """PLAN / APPROVED / REJECTED 解析与发送 + task 完成 / 失败识别。"""

    # ------------------------------------------------------------------
    # PLAN 解析
    # ------------------------------------------------------------------

    @staticmethod
    def parse_plan(body: str) -> tuple[str, str] | None:
        """从 body 找 `---PLAN-<id>--- ... ---END---` 块。

        Args:
            body: 完整消息文本(可能含多段)

        Returns:
            `(plan_id, plan_body) | None` — 第一个匹配的 plan;
            无 plan block → None
        """
        if not body:
            return None
        m = _PLAN_HEADER_RE.search(body)
        if not m:
            return None
        plan_id = m.group(1)
        start = m.end()
        # 找 ---END---(必须在 header 之后)
        end_m = _PLAN_END_RE.search(body, pos=start)
        if not end_m:
            return None
        plan_body = body[start:end_m.start()].strip("\r\n")
        return (plan_id, plan_body)

    # ------------------------------------------------------------------
    # APPROVED / REJECTED 解析
    # ------------------------------------------------------------------

    @staticmethod
    def parse_approval(
        body: str,
    ) -> tuple[str, ApprovalAction, str | None] | None:
        """从 body 找 `APPROVED: <id>` 或 `REJECTED: <id> <reason?>` 行。

        Returns:
            `(plan_id, action, reason | None) | None` — 第一个匹配;
            无 → None
        """
        if not body:
            return None
        m = _APPROVAL_RE.search(body)
        if not m:
            return None
        verb = m.group(1)
        plan_id = m.group(2)
        action: ApprovalAction = "approve" if verb == "APPROVED" else "reject"
        reason = m.group(3).strip() if m.group(3) else None
        return (plan_id, action, reason)

    # ------------------------------------------------------------------
    # TASK-COMPLETE / TASK-FAILED 识别(给 MailboxNotifier 用)
    # ------------------------------------------------------------------

    @staticmethod
    def is_task_complete(body: str) -> tuple[str, str] | None:
        """从 body 找 `---TASK-COMPLETE-<task_id>--- ... ---END---` 块。

        Returns:
            `(task_id, summary) | None` — summary 是 block 内容(空 ok)
        """
        if not body:
            return None
        m = _TASK_COMPLETE_RE.search(body)
        if not m:
            return None
        task_id = m.group(1)
        start = m.end()
        end_m = _PLAN_END_RE.search(body, pos=start)
        if end_m:
            summary = body[start:end_m.start()].strip("\r\n")
        else:
            summary = body[start:].strip("\r\n")
        return (task_id, summary)

    @staticmethod
    def is_task_failed(body: str) -> tuple[str, str] | None:
        """从 body 找 `---TASK-FAILED-<task_id>--- ... ---END---` 块。

        Returns:
            `(task_id, error_text) | None`
        """
        if not body:
            return None
        m = _TASK_FAILED_RE.search(body)
        if not m:
            return None
        task_id = m.group(1)
        start = m.end()
        end_m = _PLAN_END_RE.search(body, pos=start)
        if end_m:
            error = body[start:end_m.start()].strip("\r\n")
        else:
            error = body[start:].strip("\r\n")
        return (task_id, error)

    # ------------------------------------------------------------------
    # 发送 approval / reject
    # ------------------------------------------------------------------

    @staticmethod
    def send_approval(
        inbox_dir: Path,
        plan_id: str,
        action: ApprovalAction,
        reason: str | None = None,
    ) -> None:
        """构造 approval/reject 消息写到 inbox_dir + touch wake。

        Args:
            inbox_dir: member 目录(`<teams>/<team>/<member>/`)
            plan_id: 8 字符 hex plan id
            action: `"approve"` 或 `"reject"`
            reason: reject 原因(可选;approve 时 None)
        """
        if action == "approve":
            body = f"APPROVED: {plan_id}"
        else:
            if not reason or not reason.strip():
                raise ValueError("REJECTED 必须有 reason")
            body = f"REJECTED: {plan_id} {reason.strip()}"

        msg = Message(sender="lead", body=body)
        Mailbox.append_message(inbox_dir, "inbox", msg)
        Mailbox.touch_wake(inbox_dir)


__all__ = [
    "ApprovalAction",
    "ApprovalProtocol",
]