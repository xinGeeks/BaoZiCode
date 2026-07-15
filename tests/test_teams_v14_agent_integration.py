"""v1.4 Team Tools — Agent Loop MailboxNotifier 集成测试。

覆盖 Agent 接收 mailbox_notifier 后,`_inject_reminders` 每轮把
member outbox 摘要转 `<system-reminder type="team_mailbox">` 注入。

- mailbox_notifier=None → 不触发(members / subagents 默认路径)
- mailbox_notifier + role='lead' → 每轮调 build_reminder
- mailbox_notifier + role='member' → 不触发(只 lead)
- 失败容错:notifier 抛异常 → Agent 不挂,log warning 继续
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from baozicode.agent.events import StopReason
from baozicode.agent.loop import Agent
from baozicode.config.schema import AppConfig, BackendConfig
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import LLMClient, Message
from baozicode.teams import MailboxNotifier, TeamsRegistry


class _StubLLMClient(LLMClient):
    """最小 LLMClient stub — stream() 不 yield 任何 delta → Agent.run 检测
    到 turn.tool_calls 为空 → COMPLETED。
    """

    async def stream(self, messages, system, tools, *, cache_breakpoints=None):
        return
        yield  # type: ignore[unreachable]

    async def complete(self, messages, system, tools=None):
        return Message(role="assistant", content="ok")


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        backend="minimax",
        anthropic=BackendConfig(api_key="test-key", model="claude-test"),
        openai=BackendConfig(api_key="test-key", model="gpt-test"),
        minimax=BackendConfig(api_key="test-key", model="minimax-test"),
        deepseek=BackendConfig(api_key="test-key", model="deepseek-test"),
    )


@pytest.fixture
def teams_root(tmp_path: Path) -> Path:
    return tmp_path / "teams"


@pytest.fixture
def registry(teams_root: Path) -> TeamsRegistry:
    return TeamsRegistry(teams_root)


def _make_agent(
    *,
    registry: TeamsRegistry,
    config: AppConfig,
    role: str = "subagent",
    notifier: MailboxNotifier | None = None,
) -> Agent:
    """构造一个最小 Agent — 只跑 Agent.run() 直到 COMPLETED。"""
    from baozicode.tools.base import ToolDefinition

    tools: list[ToolDefinition] = []
    llm = _StubLLMClient()
    conv = ConversationManager()
    return Agent(
        llm_client=llm,
        tools=tools,
        conversation=conv,
        permissions=MagicMock(),
        config=config,
        role=role,
        mailbox_notifier=notifier,
    )


def _drain_run(agent: Agent, prompt: str) -> list:
    import asyncio

    async def _go() -> list:
        events: list = []
        async for ev in agent.run(prompt):
            events.append(ev)
        return events

    return asyncio.run(_go())


def _last_done_reason(events: list) -> StopReason | None:
    done = [e for e in events if getattr(e, "type", None) == "done"]
    if not done:
        return None
    return done[-1].payload


class TestAgentMailboxIntegration:
    def test_no_notifier_no_reminder_block(
        self, registry, config
    ) -> None:
        """mailbox_notifier=None → Agent.run 顺利 COMPLETED,不挂。"""
        agent = _make_agent(registry=registry, config=config, role="lead")
        events = _drain_run(agent, "hi")
        assert _last_done_reason(events) == StopReason.COMPLETED

    def test_notifier_injected_when_role_lead(
        self, registry, config
    ) -> None:
        """role='lead' + notifier 设了 → 不报错(返 None 也 ok)。"""
        notifier = MailboxNotifier(registry, "devops")
        agent = _make_agent(
            registry=registry, config=config,
            role="lead", notifier=notifier,
        )
        events = _drain_run(agent, "hi")
        assert _last_done_reason(events) == StopReason.COMPLETED

    def test_notifier_skipped_when_role_member(
        self, registry, config
    ) -> None:
        """role='member' + notifier 设了 → 不调 build_reminder(mock 验证)。"""
        notifier = MagicMock(spec=MailboxNotifier)
        notifier.build_reminder.return_value = None
        agent = _make_agent(
            registry=registry, config=config,
            role="member", notifier=notifier,
        )
        events = _drain_run(agent, "hi")
        assert _last_done_reason(events) == StopReason.COMPLETED
        notifier.build_reminder.assert_not_called()

    def test_notifier_exception_swallowed(
        self, registry, config
    ) -> None:
        """notifier 抛异常 → Agent 不挂,继续跑完 COMPLETED。"""
        notifier = MagicMock(spec=MailboxNotifier)
        notifier.build_reminder.side_effect = RuntimeError("boom")
        agent = _make_agent(
            registry=registry, config=config,
            role="lead", notifier=notifier,
        )
        events = _drain_run(agent, "hi")
        assert _last_done_reason(events) == StopReason.COMPLETED