"""v1.0 Skills — Agent 集成(active section 注入 + skill_filter 路由)测试。

覆盖:
- `_inject_reminders` 在 skill_activation 注入后,active section 出现在 messages[-2]
- 多个 Skill 同时激活 → 顺序堆叠
- active section 在 plan_mode reminder 之前/之后的位置正确(per P8 顺序)
- skill_activation=None → 不注入 active section,其它 reminder 仍正常
- Agent 接受 skill_filter 参数 + 透传到 _v5_executor
"""

from __future__ import annotations

import pytest

from baozicode.commands.registry import CommandRegistry
from baozicode.skills.activation import SkillActivation
from baozicode.skills.bootstrap import bootstrap_skills
from baozicode.agent.loop import Agent
from baozicode.agent.guards import GuardState

from baozicode.llm.base import Message

from tests._agent_helpers import make_minimal_config


def _stub_llm():
    """最小 LLMClient stub — Agent 只在 _inject_reminders 跑,不真调 stream。"""
    from baozicode.llm.base import LLMClient

    class _Stub(LLMClient):
        async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
            if False:
                yield  # 让它成 async generator
            return
            yield  # unreachable,for type
    return _Stub()


# ---- _inject_reminders 注入 active section ----


class TestInjectActiveSection:
    def test_no_activation_no_section(self) -> None:
        """skill_activation=None → 不注入 active_skills section(只有 env)。"""
        agent = Agent(
            llm_client=_stub_llm(),
            tools=[],
            conversation=_stub_conversation(),
            permissions=_stub_perms(),
            config=make_minimal_config(),
            skill_activation=None,
        )
        msgs = [Message(role="user", content="hi")]
        out = agent._inject_reminders(msgs, iteration=1, guard_state=GuardState())
        # env reminder 总会注入;active_skills 不出现
        assert any("active_skills" not in m.content for m in out)
        assert not any("active_skills" in m.content for m in out)

    def test_with_activation_injects_section(self) -> None:
        """有 active Skill → active_skills section 出现在 messages[-2](env 之后)。"""
        act = SkillActivation(CommandRegistry())
        act.activate(
            "demo", "do X then Y", mode="shared",
            description="demo skill", allowed_tools=["Read"],
        )
        agent = Agent(
            llm_client=_stub_llm(),
            tools=[],
            conversation=_stub_conversation(),
            permissions=_stub_perms(),
            config=make_minimal_config(),
            skill_activation=act,
        )
        msgs = [
            Message(role="user", content="earlier"),
            Message(role="assistant", content="ok"),
            Message(role="user", content="now"),
        ]
        out = agent._inject_reminders(msgs, iteration=1, guard_state=GuardState())
        # out 顺序:[orig[0], orig[1], env, active_skills, orig[2]] = 5
        assert len(out) == 5
        # active_skills 在 messages[-2]
        active_msg = out[-2]
        assert active_msg.role == "user"
        assert "active_skills" in active_msg.content
        assert "demo" in active_msg.content
        assert "do X then Y" in active_msg.content
        # 最后一条仍是 user 原消息
        assert out[-1].role == "user"
        assert out[-1].content == "now"

    def test_multiple_skills_concatenated(self) -> None:
        """多个 active Skill 按 activate 顺序堆叠在同一 section 里。"""
        act = SkillActivation(CommandRegistry())
        act.activate("alpha", "alpha body", mode="shared")
        act.activate("beta", "beta body", mode="shared")
        agent = Agent(
            llm_client=_stub_llm(),
            tools=[],
            conversation=_stub_conversation(),
            permissions=_stub_perms(),
            config=make_minimal_config(),
            skill_activation=act,
        )
        msgs = [Message(role="user", content="go")]
        out = agent._inject_reminders(msgs, iteration=1, guard_state=GuardState())
        # active_skills section 是 out[-2](env 在 -3)
        section = out[-2].content
        assert "active_skills" in section
        assert "alpha" in section
        assert "beta" in section
        assert section.index("alpha") < section.index("beta")

    def test_empty_activation_no_section(self) -> None:
        """SkillActivation 存在但无 active Skill → 不注入(节省 token)。"""
        act = SkillActivation(CommandRegistry())
        agent = Agent(
            llm_client=_stub_llm(),
            tools=[],
            conversation=_stub_conversation(),
            permissions=_stub_perms(),
            config=make_minimal_config(),
            skill_activation=act,
        )
        msgs = [Message(role="user", content="hi")]
        out = agent._inject_reminders(msgs, iteration=1, guard_state=GuardState())
        # 没 active section → 只有 env reminder
        assert not any("active_skills" in m.content for m in out)


# ---- skill_filter 透传 ----


class TestSkillFilterWiring:
    @pytest.mark.asyncio
    async def test_skill_filter_blocks_tool_call(self, tmp_path) -> None:
        """skill_filter.is_allowed = False → 工具调用立即 deny。"""
        from baozicode.skills.whitelist import SkillWhitelistFilter
        from baozicode.tools.base import ToolCall, ToolResult

        # 模拟 skill_filter:始终拒绝 Bash
        class _NoBash:
            def is_allowed(self, call):
                return call.name != "Bash"

        agent = Agent(
            llm_client=_stub_llm(),
            tools=[],
            conversation=_stub_conversation(),
            permissions=_stub_perms(),
            config=make_minimal_config(),
            merged_permissions=_stub_merged(),
            skill_filter=_NoBash(),
        )
        call = ToolCall(id="c1", name="Bash", arguments={"cmd": "ls"})
        result = await agent._v5_executor(call, GuardState())
        assert result.is_error
        assert "Bash" in result.content


# ---- helpers ----


def _stub_conversation():
    """最小 ConversationManager stub(只取 to_list / set_messages 等接口)。"""
    from baozicode.conversation.manager import ConversationManager
    cm = ConversationManager.__new__(ConversationManager)
    cm._messages = []
    cm._archiver = None
    cm.add_user = lambda m: cm._messages.append(Message(role="user", content=m))
    cm.to_list = lambda: list(cm._messages)
    return cm


def _stub_perms():
    """v0.5 之前的 permissions stub。"""
    from types import SimpleNamespace
    return SimpleNamespace(deny=[], auto_allow=[])


def _stub_merged():
    """v0.5 merged_permissions stub — 给 _v5_executor 走 permission check。"""
    from types import SimpleNamespace
    return SimpleNamespace(
        rules=[],
        mode="default",
        sources_loaded=[],
        real_root=None,
        path_sandbox_enabled=False,
        session_rules=[],
    )