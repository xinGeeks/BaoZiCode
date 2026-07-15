"""v1.4 Pane Backend — `build_member_agent` Member Agent 工厂。

公开 API:

- `build_member_agent(teams_registry, team_name, member, *, llm_client,
  config, permissions, project_root=None, skill_*) -> Agent` ——
  构造 `role='member'` 的 Agent,7 builtin 工具 + `load_skill`
  (tool_type='internal' 不受角色过滤)+ fresh `ConversationManager`
- `MailboxLayer(teams_registry, team_name, member_name)` ——
  framework 层 hook,提供 `read_inbox_unread() / write_outbox(msg) /
  mark_inbox_read()`;不暴露为 ToolDefinition,MemberMainLoop 直接
  持有 reference 调

设计要点:

- `build_member_agent` 是函数而非类,MemberMainLoop 每轮 wake 后
  调一次拿 fresh Agent(避免 Agent state 跨 turn 串台)
- `MailboxLayer` 是状态化对象(持有 mailbox dir + 已读消息 hash 集),
  与 member 进程生命周期对齐
- Member Agent **不** 接 `mailbox_notifier`(那是 Lead 的 helper);
  Member 只 listen 自己 inbox
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from baozicode.agent.loop import Agent
from baozicode.config.schema import AppConfig
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import LLMClient
from baozicode.permissions.types import MergedPermissions
from baozicode.skills.activation import SkillActivation
from baozicode.skills.registry import SkillRegistry
from baozicode.skills.whitelist import SkillWhitelistFilter
from baozicode.tools.registry import get_default_tool_registry

from .mailbox import Mailbox
from .registry import TeamsRegistry
from .schema import Member, Message

log = logging.getLogger(__name__)


class MailboxLayer:
    """Member 进程的 mailbox hook — 不暴露为 ToolDefinition。

    仅 MemberMainLoop 调。LLM 不直接调 mailbox(避免工具膨胀)。

    字段:
      `_registry / _team / _member_name / _member_dir` — 派生定位
      `_seen_bodies: set[str]` — 已读消息 body hash 去重(防同 body
        反复处理)
    """

    def __init__(
        self,
        teams_registry: TeamsRegistry,
        team_name: str,
        member_name: str,
    ) -> None:
        self._registry = teams_registry
        self._team_name = team_name
        self._member_name = member_name
        self._member_dir = teams_registry.teams_dir / team_name / member_name
        self._seen_bodies: set[str] = set()

    @property
    def member_dir(self) -> Path:
        return self._member_dir

    def read_inbox_unread(self) -> list[Message]:
        """读未读 inbox 消息(seen_bodies 去重)。"""
        all_msgs = Mailbox.read_messages(self._member_dir, "inbox")
        result: list[Message] = []
        for m in all_msgs:
            # 用 (sender, body[:80]) 当去重 key(同 sender 同 body 视为重复)
            key = f"{m.sender}|{m.body[:80]}"
            if key in self._seen_bodies:
                continue
            self._seen_bodies.add(key)
            result.append(m)
        return result

    def write_outbox(self, body: str, sender: str | None = None) -> None:
        """追加消息到 outbox。"""
        msg = Message(sender=sender or self._member_name, body=body)
        Mailbox.append_message(self._member_dir, "outbox", msg)

    def mark_inbox_read(self, messages: list[Message]) -> None:
        """实际不打 read flag(Mailbox 暂未实现 read 字段)— 仅从
        `_seen_bodies` 移除避免下次重复。"""
        for m in messages:
            key = f"{m.sender}|{m.body[:80]}"
            self._seen_bodies.discard(key)

    def clear_seen(self) -> None:
        """重置去重集合 — Member 进程重启时调。"""
        self._seen_bodies.clear()


def build_member_agent(
    teams_registry: TeamsRegistry,
    team_name: str,
    member: Member,
    *,
    llm_client: LLMClient,
    config: AppConfig,
    permissions: MergedPermissions,
    project_root: Path | None = None,
    skill_registry: SkillRegistry | None = None,
    skill_activation: SkillActivation | None = None,
    skill_filter: SkillWhitelistFilter | None = None,
    permissions_engine: Any | None = None,
    session_mode: Any | None = None,
) -> Agent:
    """构造 `role='member'` 的 Agent。

    工具子集:`tool_registry.get_all_tools(role="member")` → 7 builtin
    + `load_skill`(`tool_type='internal'` 不受角色过滤,始终可见)。
    Member Agent **不** 见 `team_*` Lead 工具。

    Conversation fresh(`ConversationManager()`,不 resume session
    存档)— member 进程是 headless 长期跑,不需要 session 持久。

    Returns:
        Agent 实例,role='member',已绑定 7 builtin + load_skill
    """
    tool_registry = get_default_tool_registry()
    tools = tool_registry.get_all_tools(role="member")
    conversation = ConversationManager()

    agent = Agent(
        llm_client=llm_client,
        tools=tools,
        conversation=conversation,
        permissions=permissions,
        config=config,
        role="member",
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        skill_activation=skill_activation,
        skill_filter=skill_filter,
        permissions_engine=permissions_engine,
        session_mode=session_mode,
        project_root=project_root,
        # mailbox_notifier=None — Member 不需要 Lead 那种 outbox 监控
    )
    log.info(
        "build_member_agent: %s/%s role=member tools=%d",
        team_name, member.name, len(tools),
    )
    return agent


__all__ = ["MailboxLayer", "build_member_agent"]
