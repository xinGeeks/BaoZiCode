"""v0.4 Phase 2 — Agent × PromptBuilder integration tests.

8 tests:
1. Agent._prompt populated at __init__
2. stable_system identical across two runs (cache key)
3. augmented_tools has rules-injected prefixes
4. plan_mode=true filters to side_effect=False only
5. _inject_reminders places env reminder at len-2
6. _inject_reminders disabled → unchanged
7. Plan mode reminder cadence
8. BuiltPrompt.cache_breakpoints shape
"""

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import UsageStats
from baozicode.agent.loop import Agent
from baozicode.config.schema import AgentConfig, AppConfig, BackendConfig, RulesConfig
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.prompt.types import BuiltPrompt, CacheBreakpoint
from baozicode.tools.registry import get_all_tools
from baozicode.tools.base import ToolCall

sys.path.insert(0, str(Path(__file__).parent))
from _agent_helpers import make_minimal_config


class _FakeLLM(LLMClient):
    """记录每次 stream() 调用的 kwargs(system / tools / messages),供测试断言。"""

    def __init__(self) -> None:
        self.call_kwargs: list[dict] = []

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        # 记录 kwargs(传引用不影响外面,但 messages 长度和内容会被之后断言)
        self.call_kwargs.append(
            {
                "messages": list(messages),
                "system": system,
                "tools": list(tools) if tools else [],
            }
        )
        # 第二轮吐纯文本让 Agent 走 COMPLETED 停止路径
        if len(self.call_kwargs) >= 2:
            yield ContentDelta(type="text", text="done")
            yield ContentDelta(type="usage", text=UsageStats())
        else:
            # 第一轮:什么都不吐 → 走 agent 的 text-only COMPLETED 路径
            yield ContentDelta(type="text", text="hi")


class _NoopLLM(LLMClient):
    """不记录任何东西,只让 Agent 跑得动。"""

    def __init__(self) -> None:
        self.kwargs_list: list[dict] = []

    async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None) -> AsyncIterator[ContentDelta]:
        self.kwargs_list.append(
            {"messages": list(messages), "system": system, "tools": list(tools) if tools else []}
        )
        yield ContentDelta(type="text", text="ok")


def _perm():
    class _P:
        deny: list[str] = []
        auto_allow: list[str] = []

    return _P()


# ---- 1: __init__ runs PromptBuilder.build once ----


def test_agent_init_populates_prompt() -> None:
    """Agent(config=...)._prompt is a non-null BuiltPrompt after __init__."""
    llm = _FakeLLM()
    cfg = make_minimal_config()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=cfg,
    )
    assert isinstance(a._prompt, BuiltPrompt)
    assert a._prompt is not None
    assert len(a._prompt.stable_system) > 0
    print("[OK] Agent._prompt populated at __init__")


# ---- 2: stable_system identical across two runs ----


def test_stable_system_is_stable_across_runs() -> None:
    """同一 Agent 实例的 stable_system 在多次 run(...) 之间不变化(可作为 cache key)。"""
    llm = _FakeLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=make_minimal_config(),
    )

    async def drain() -> None:
        async for _ in a.run("first"):
            pass

    asyncio.run(drain())
    # 一次 run 后,sys 值应该和 initial state 一致(没被 mutation 破坏)
    s1 = a._prompt.stable_system

    async def drain2() -> None:
        async for _ in a.run("second"):
            pass

    asyncio.run(drain2())
    s2 = a._prompt.stable_system

    # 注意:第二次 run 时 first user msg 仍在 conversation 里,但 stable_system
    # 只跟 config / tools 有关 — 应保持一致 → 可作为 cache control 的 breakpoint key
    assert s1 == s2
    assert len(s1) > 0
    # 进一步:实际传给 llm.stream 的 system 也应等于 stable_system
    first_system = llm.call_kwargs[0]["system"]
    assert first_system == s1
    print("[OK] stable_system identical across runs (cache key stable)")


# ---- 3: augmented_tools has rule-injected prefixes ----


def test_augmented_tools_have_rule_prefixes_when_rule_enabled() -> None:
    """edit_requires_read=True → Read tool description 含『【必读】』前缀(Bash 等工具不受影响)。

    注:Read 不在 edit_requires_read 的 applies_to(后者是 Edit / Write)。
    所以选 Read 工具验证 prefer_specialized_tools rule。
    prefer_specialized_tools=True → Read description 应以『【优先】』开头。
    """
    cfg = make_minimal_config()  # rules all default True
    llm = _FakeLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=cfg,
    )
    tools = a._prompt.augmented_tools
    by_name = {t.name: t for t in tools}
    assert "Read" in by_name, "Read 工具应存在"
    assert "【优先】" in by_name["Read"].description, (
        "prefer_specialized_tools=True → Read description 应以『【优先】』开头"
    )
    # Bash 应含『【必传 timeout】』
    assert "【必传 timeout】" in by_name["Bash"].description
    print("[OK] augmented_tools: rule prefixes present when rules=True")


def test_augmented_tools_no_prefix_when_rule_disabled() -> None:
    """disabling prefer_specialized_tools → Read description 不含『【优先】』前缀。

    验证规则关掉后注入消失。Read 工具还会被其他多个规则加固,所以我们关掉
    prefer_specialized_tools 后只需验证『【优先】』前缀消失。
    """
    # 只关掉 prefer_specialized_tools,保持其他规则不变以做"局部消失"的对比
    cfg = make_minimal_config(
        agent=AgentConfig(
            rules=RulesConfig(prefer_specialized_tools=False)
        )
    )
    llm = _FakeLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=cfg,
    )
    tools = a._prompt.augmented_tools
    by_name = {t.name: t for t in tools}
    assert "【优先】" not in by_name["Read"].description, (
        "prefer_specialized_tools=False → Read 不应再有『【优先】』前缀"
    )
    # 其他规则仍然 True,Read 仍然有其他前缀(parallel_limit / absolute_paths)
    # 关键:『【必传 timeout】』只出现在 Bash 工具,验证 Bash 仍然有这个前缀
    assert "【必传 timeout】" in by_name["Bash"].description
    print("[OK] augmented_tools: rule prefix removed when that rule disabled")


# ---- 4: plan mode filters tools to read-only ----


def test_plan_mode_filters_tools_to_side_effect_false() -> None:
    """Agent(plan_mode=True) → augmented_tools 只含 Read/Grep/Glob/WebFetch。"""
    cfg = make_minimal_config()
    llm = _FakeLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=cfg,
        plan_mode=True,
    )
    names = {t.name for t in a._prompt.augmented_tools}
    assert names == {"Read", "Grep", "Glob", "WebFetch"}
    # 显式排除写类工具
    assert "Bash" not in names
    assert "Write" not in names
    assert "Edit" not in names
    print("[OK] plan_mode=True: augmented_tools = {Read, Grep, Glob, WebFetch}")


# ---- 5: _inject_reminders places env at len-2 ----


def test_inject_reminders_places_env_block_at_len_minus_2() -> None:
    """env <system-reminder> 应塞到 messages[-2],user 仍保持最后位置。"""
    cfg = make_minimal_config()
    llm = _FakeLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=cfg,
    )
    messages = [
        Message(role="user", content="prior question"),
        Message(role="assistant", content="prior answer"),
        Message(role="user", content="current question"),
    ]
    out = a._inject_reminders(messages, iteration=1)
    # 第一条 + 第二条仍是原消息
    assert out[0].content == "prior question"
    assert out[1].content == "prior answer"
    # index -1 是 user 消息
    assert out[-1].content == "current question"
    # index -2 应是 env reminder(含 <system-reminder type="env">)
    assert 'type="env"' in str(out[-2].content)
    assert 'system-reminder' in str(out[-2].content)
    print("[OK] env reminder placed at index len-2 (user stays last)")


# ---- 6: enable_system_reminders=False → no injection ----


def test_inject_reminders_disabled_returns_unchanged() -> None:
    """enable_system_reminders=False 时 _inject_reminders 返回原列表(新 list,内容相同)。"""
    cfg = make_minimal_config(
        agent=AgentConfig(enable_system_reminders=False)
    )
    llm = _FakeLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=cfg,
    )
    messages = [
        Message(role="user", content="q"),
        Message(role="assistant", content="a"),
        Message(role="user", content="q2"),
    ]
    out = a._inject_reminders(messages, iteration=1)
    # 长度一致,内容一致
    assert len(out) == len(messages)
    for x, y in zip(out, messages):
        assert x.content == y.content
    print("[OK] enable_system_reminders=False → no reminder injection")


# ---- 7: plan mode reminder cadence ----


def test_plan_mode_reminder_cadence_interval_5() -> None:
    """plan_mode=True + interval=5 → iteration=1 有,2 没有,2 也没有,6 有。"""
    cfg = make_minimal_config()  # default plan_reminder_interval=5
    llm = _FakeLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=cfg,
        plan_mode=True,
    )
    base = [Message(role="user", content="x")]
    out1 = a._inject_reminders(base, iteration=1)
    out2 = a._inject_reminders(base, iteration=2)
    out6 = a._inject_reminders(base, iteration=6)

    def has_plan(messages: list[Message]) -> bool:
        return any('type="plan_mode"' in str(m.content) for m in messages)

    # iteration=1:有
    assert has_plan(out1)
    # iteration=2:无(不在 interval=5 的重发点)
    assert not has_plan(out2)
    # iteration=6:有(1,6,11...)
    assert has_plan(out6)
    print("[OK] plan_mode reminder cadence: 1→emit, 2→silent, 6→emit")


# ---- 8: cache_breakpoints shape ----


def test_cache_breakpoints_have_required_locations() -> None:
    """BuiltPrompt.cache_breakpoints 是 CacheBreakpoint 列表,含 system_start + after_tools。"""
    cfg = make_minimal_config()
    llm = _FakeLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=cfg,
    )
    breakpoints = a._prompt.cache_breakpoints
    assert isinstance(breakpoints, list)
    assert len(breakpoints) >= 2
    for bp in breakpoints:
        assert isinstance(bp, CacheBreakpoint)
    locations = {bp.location for bp in breakpoints}
    assert "system_start" in locations
    assert "after_tools" in locations
    print("[OK] cache_breakpoints has system_start + after_tools (>=2 entries)")


def main() -> None:
    test_agent_init_populates_prompt()
    test_stable_system_is_stable_across_runs()
    test_augmented_tools_have_rule_prefixes_when_rule_enabled()
    test_augmented_tools_no_prefix_when_rule_disabled()
    test_plan_mode_filters_tools_to_side_effect_false()
    test_inject_reminders_places_env_block_at_len_minus_2()
    test_inject_reminders_disabled_returns_unchanged()
    test_plan_mode_reminder_cadence_interval_5()
    test_cache_breakpoints_have_required_locations()
    print("\nAll agent_with_prompt tests passed.")


if __name__ == "__main__":
    main()
