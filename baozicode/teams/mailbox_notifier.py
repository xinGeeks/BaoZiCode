"""v1.4 Team Tools — MailboxNotifier 钩子。

公开 API:

- `MailboxNotifier(teams_registry, team_name)` — 构造
- `notifier.build_reminder() -> str | None` — 扫所有 member outbox,
  找未读的 TASK-COMPLETE / TASK-FAILED / PLAN / 普通消息,
  组装 `<system-reminder type="team_mailbox">` 块
- `notifier.mark_task_complete(task_id, member_name)` — 标 task done +
  member state idle
- `notifier.mark_task_failed(task_id, member_name)` — 标 task failed +
  member state idle + error 填到 task

设计要点:

- dedup:`_seen_hashes: set[str]` 跨迭代持久,每条 outbox 消息 hash(body+ts)
  作为 key,避免重复注入
- 主动副作用:TASK-COMPLETE / TASK-FAILED 触发 `mark_*`,更新 tasks.jsonl
  和 member state.json;其他类型只扫描不改
- 输出格式:`<system-reminder type="team_mailbox">...</system-reminder>`,
  Agent._inject_reminders 直接追加
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .approval import ApprovalProtocol
from .mailbox import Mailbox, default_member_state
from .registry import TeamsRegistry
from .schema import MemberState
from .tasks import Tasks

log = logging.getLogger(__name__)


class MailboxNotifier:
    """Agent Loop 钩子 — 每轮 Agent 决策前扫所有 member outbox 摘要
    转 system-reminder。
    """

    def __init__(
        self,
        teams_registry: TeamsRegistry,
        team_name: str,
    ) -> None:
        self._registry = teams_registry
        self._team_name = team_name
        self._seen_hashes: set[str] = set()
        # 给 build_reminder 的 last_seen_ts(可选,用作过滤未读边界;
        # 默认 None = 不按时间过滤,只看 seen set)
        self._last_seen_ts: datetime | None = None

    # ------------------------------------------------------------------
    # 内部 hash / dedup
    # ------------------------------------------------------------------

    @staticmethod
    def _msg_hash(msg_body: str, ts: datetime | None) -> str:
        """唯一指纹 — body + ts 决定。ts=None 时退化为 body-only hash。"""
        h = hashlib.sha1()
        h.update(msg_body.encode("utf-8", errors="replace"))
        if ts is not None:
            h.update(ts.isoformat().encode("utf-8"))
        return h.hexdigest()

    def _is_new(self, body: str, ts: datetime | None) -> bool:
        """True = 未在 seen 集合,需要注入。"""
        h = self._msg_hash(body, ts)
        if h in self._seen_hashes:
            return False
        self._seen_hashes.add(h)
        return True

    # ------------------------------------------------------------------
    # 副作用:TASK-COMPLETE / TASK-FAILED → tasks.jsonl + state.json
    # ------------------------------------------------------------------

    def mark_task_complete(self, task_id: str, member_name: str) -> None:
        """标 task done + member idle。

        Args:
            task_id: 来自 outbox 的 `---TASK-COMPLETE-<id>---` 里的 id
            member_name: 完成的 member 名
        """
        team_dir = self._registry.teams_dir / self._team_name
        try:
            Tasks.update_status(team_dir, task_id, "done")
        except Exception as e:  # noqa: BLE001
            log.warning(
                "MailboxNotifier.mark_task_complete: 标 task %s done 失败: %s",
                task_id,
                e,
            )
        member_dir = team_dir / member_name
        try:
            Mailbox.write_state(
                member_dir,
                MemberState(status="idle", current_task=None),
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "MailboxNotifier.mark_task_complete: 改 %s state idle 失败: %s",
                member_name,
                e,
            )

    def mark_task_failed(
        self, task_id: str, member_name: str, error: str
    ) -> None:
        """标 task failed + member idle + error。"""
        team_dir = self._registry.teams_dir / self._team_name
        try:
            Tasks.update_status(
                team_dir, task_id, "failed", error=error
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "MailboxNotifier.mark_task_failed: 标 task %s failed 失败: %s",
                task_id,
                e,
            )
        member_dir = team_dir / member_name
        try:
            Mailbox.write_state(
                member_dir,
                MemberState(status="idle", current_task=None),
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "MailboxNotifier.mark_task_failed: 改 %s state idle 失败: %s",
                member_name,
                e,
            )

    # ------------------------------------------------------------------
    # 主体:build_reminder
    # ------------------------------------------------------------------

    def build_reminder(self) -> str | None:
        """扫所有 member outbox,组装 reminder 块。

        Returns:
            str — `<system-reminder type="team_mailbox">...</system-reminder>`
            块内容;无新消息 → None
        """
        team_dir = self._registry.teams_dir / self._team_name
        if not team_dir.exists():
            return None

        store = self._registry.get(self._team_name)
        if store is None:
            return None
        team = store.show()

        sections: list[str] = []
        for member_name in sorted(team.members.keys()):
            member_dir = team_dir / member_name
            outbox_path = member_dir / "outbox.jsonl"
            if not outbox_path.exists():
                continue
            try:
                messages = Mailbox.read_messages(member_dir, "outbox")
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "MailboxNotifier: 读 %s outbox 失败: %s",
                    member_name,
                    e,
                )
                continue

            member_section = self._process_member_messages(
                member_name, member_dir, messages
            )
            if member_section:
                sections.append(member_section)

        if not sections:
            return None

        body = "\n".join(sections)
        return (
            '<system-reminder type="team_mailbox">\n'
            f"{body}\n"
            "</system-reminder>"
        )

    def _process_member_messages(
        self,
        member_name: str,
        member_dir: Path,
        messages: list[Any],
    ) -> str | None:
        """处理单个 member 的 outbox messages,返本 member 的 reminder section。

        Returns:
            str — 多行文本;无新消息 → None
        """
        lines: list[str] = []
        state = Mailbox.read_state(member_dir)
        for msg in messages:
            if not self._is_new(msg.body, msg.timestamp):
                continue
            body = msg.body
            # 优先匹配 TASK-COMPLETE / TASK-FAILED(走副作用)
            completed = ApprovalProtocol.is_task_complete(body)
            if completed:
                task_id, summary = completed
                self.mark_task_complete(task_id, member_name)
                lines.append(
                    f"- {member_name} ({state.status}) completed "
                    f"task={task_id} (已自动 mark done): {summary}"
                )
                continue
            failed = ApprovalProtocol.is_task_failed(body)
            if failed:
                task_id, error = failed
                self.mark_task_failed(task_id, member_name, error)
                lines.append(
                    f"- {member_name} ({state.status}) failed "
                    f"task={task_id} (已自动 mark failed): {error}"
                )
                continue
            # PLAN 块
            plan = ApprovalProtocol.parse_plan(body)
            if plan:
                plan_id, plan_body = plan
                preview = plan_body[:200]
                lines.append(
                    f"- {member_name} ({state.status}) submitted plan "
                    f"{plan_id}:\n  {preview}\n  回复: APPROVED: {plan_id} "
                    f"或 REJECTED: {plan_id} <reason>"
                )
                continue
            # 普通消息 — 截 200 字符
            preview = body[:200]
            lines.append(
                f"- {member_name} ({state.status}) sent: {preview}"
            )

        if not lines:
            return None
        return "\n".join(lines)


__all__ = ["MailboxNotifier"]