"""v0.5 5 层防御 — Agent.run() 端到端集成测试。

覆盖:
- L1 黑名单 deny → is_error 喂回 LLM,loop 继续
- L3 allow 规则 → 直接执行
- L4 strict mode → fallthrough 全部 deny
- L5 user decision(Modal)→ 用 permission_callback 模拟用户选择
- 重试 previously denied call → permission_callback 第二次仍能拦
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import AgentEvent, StopReason
from baozicode.agent.loop import Agent
from baozicode.config.schema import Permissions
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.permissions.engine import RuleEngine
from baozicode.permissions.types import MergedPermissions, PermissionRule
from baozicode.tools.base import ToolCall
from baozicode.tools.registry import get_all_tools

sys.path.insert(0, str(Path(__file__).parent))
from _agent_helpers import make_minimal_config


# ---- mock LLM ----

class _ScriptedLLM(LLMClient):
    """v0.5 集成测试专用 — 按调用次数索引的脚本。

    每条 response 是一系列 ContentDelta;支持 text + tool_use + usage。
    """

    def __init__(self, responses: list[list[ContentDelta]]):
        self.responses = responses
        self.call_count = 0

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        script = self.responses[self.call_count - 1]
        for d in script:
            yield d


# ---- mock permission callback ----

class _FakeCallback:
    """模拟 PermissionModal — 记录每次调用 + 按预设 choice 返回。"""

    def __init__(self, choices: list[bool]) -> None:
        # 每次 await 时弹一个 choice
        self.choices = list(choices)
        self.calls: list[ToolCall] = []

    async def __call__(self, call: ToolCall) -> bool:
        self.calls.append(call)
        if not self.choices:
            return False  # 默认拒
        return self.choices.pop(0)


class _V2Perms:
    """v0.2 兼容 Permissions duck-type。"""

    def __init__(
        self,
        deny: list[str] | None = None,
        auto_allow: list[str] | None = None,
    ) -> None:
        self.deny = deny or []
        self.auto_allow = auto_allow or []
        self.batch_confirm = False
        self.bash_locked_cwd = False


def _make_merged(tmp_path: Path, mode: str = "default") -> MergedPermissions:
    """构造一个 MergedPermissions 用作 Agent 的 5 层防御状态。"""
    merged = MergedPermissions()
    merged.mode = mode  # type: ignore[assignment]
    merged.real_root = tmp_path.resolve()
    return merged


def _text(s: str) -> ContentDelta:
    return ContentDelta(type="text", text=s)


def _bash_use(call_id: str, command: str) -> ContentDelta:
    return ContentDelta(
        type="tool_use",
        text=ToolCall(id=call_id, name="Bash", arguments={"command": command}),
    )


def _read_use(call_id: str, file_path: str) -> ContentDelta:
    return ContentDelta(
        type="tool_use",
        text=ToolCall(id=call_id, name="Read", arguments={"file_path": file_path}),
    )


def _usage(inp: int = 10, out: int = 5) -> ContentDelta:
    from baozicode.agent.events import UsageStats
    return ContentDelta(type="usage", text=UsageStats(
        input_tokens=inp, output_tokens=out,
    ))


async def _drain(agent: Agent, text: str) -> list[AgentEvent]:
    out: list[AgentEvent] = []
    async for ev in agent.run(text):
        out.append(ev)
    return out


# ---- L1 deny → is_error ----

async def test_l1_blacklist_deny_returns_is_error(tmp_path: Path) -> None:
    """L1 拦截 `rm -rf /` → tool_result.is_error=True,Agent 不终止。

    重点:L1 deny 的 call 不应触发 L5 Modal(callback 不应被该 call 调)。
    LLM 后续 retry 走合法命令时,L5 可能被调(那是另一回事)。
    """
    merged = _make_merged(tmp_path)
    engine = RuleEngine(merged=merged)
    callback = _FakeCallback([])  # 默认拒(L1 拦的不会到这里)

    llm = _ScriptedLLM([
        # 第 1 轮:发个"我来做点事" + 调 rm -rf / (L1 拦)
        [_text("attempting "), _bash_use("c1", "rm -rf /"), _usage(50, 20)],
        # 第 2 轮(被 L1 拒后 LLM 改主意):直接完成
        [_text("ok fine"), _usage(10, 5)],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_V2Perms(),  # v0.2 兼容字段,v0.5 路径走 merged
        config=make_minimal_config(),
        permission_callback=callback.__call__,
        merged_permissions=merged,
        permissions_engine=engine,
    )

    events = await _drain(agent, "do something dangerous")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    # 至少 1 个 tool_result:c1 被 L1 拒(is_error)
    assert len(tool_results) >= 1
    first = tool_results[0]
    assert first.is_error, "L1 deny 必须 is_error=True"
    assert first.tool_call_id == "c1"
    assert "L1_blacklist" in first.content or "拒绝" in first.content, (
        f"deny 文案应提 L1: {first.content}"
    )

    # L5 callback 没被调(因为 L1 已 short-circuit,根本没到 L5)
    assert callback.calls == [], (
        f"L5 不应被 L1 拦到的调用触发,实际: {callback.calls}"
    )


# ---- L3 allow → 直接执行 ----

async def test_l3_rule_allow_executes(tmp_path: Path) -> None:
    """L3 规则 `Bash(git *)` → git 命令放行(无 Modal)。"""
    merged = _make_merged(tmp_path)
    merged.rules.append(PermissionRule(
        tool="Bash", pattern="git *", decision="allow", source="user_global",
    ))
    engine = RuleEngine(merged=merged)
    callback = _FakeCallback([])  # 应当不被调

    llm = _ScriptedLLM([
        [_bash_use("c1", "git status"), _usage(20, 10)],
        [_text("git done"), _usage(10, 5)],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_V2Perms(),
        config=make_minimal_config(),
        permission_callback=callback.__call__,
        merged_permissions=merged,
        permissions_engine=engine,
    )

    events = await _drain(agent, "check git")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    # git status 应该被执行(L3 allow 路径)→ is_error=False
    assert len(tool_results) >= 1
    git_result = next((r for r in tool_results if r.tool_call_id == "c1"), None)
    assert git_result is not None
    assert git_result.is_error is False, f"git status 应被放行: {git_result.content}"

    # L5 callback 没被调
    assert callback.calls == []


# ---- L4 mode strict → fallthrough 全部拒 ----

async def test_l4_strict_denies_fallthrough(tmp_path: Path) -> None:
    """strict mode + 无规则 → safe command 也被拒。"""
    merged = _make_merged(tmp_path, mode="strict")
    engine = RuleEngine(merged=merged)
    callback = _FakeCallback([])  # 不应被调

    llm = _ScriptedLLM([
        [_bash_use("c1", "echo hi"), _usage(20, 10)],
        [_text("ok"), _usage(10, 5)],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_V2Perms(),
        config=make_minimal_config(),
        permission_callback=callback.__call__,
        merged_permissions=merged,
        permissions_engine=engine,
    )

    events = await _drain(agent, "say hi")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    assert len(tool_results) >= 1
    first = tool_results[0]
    assert first.is_error, "strict mode + 无规则 → 应被 L4 拒"
    assert "L4_mode" in first.content, f"应标记 L4: {first.content}"


# ---- L5 user 决策:Modal ----

async def test_l5_user_deny_returns_is_error(tmp_path: Path) -> None:
    """default mode + 无规则 + 用户选 N → tool_result is_error。"""
    merged = _make_merged(tmp_path, mode="default")
    engine = RuleEngine(merged=merged)
    callback = _FakeCallback([False])  # 第一次:用户拒

    llm = _ScriptedLLM([
        [_bash_use("c1", "curl example.com"), _usage(20, 10)],
        [_text("ok"), _usage(10, 5)],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_V2Perms(),
        config=make_minimal_config(),
        permission_callback=callback.__call__,
        merged_permissions=merged,
        permissions_engine=engine,
    )

    events = await _drain(agent, "fetch a url")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    # L5 拒 → is_error
    assert len(tool_results) >= 1
    first = tool_results[0]
    assert first.is_error
    # callback 被调了 1 次
    assert len(callback.calls) == 1
    assert callback.calls[0].id == "c1"


async def test_l5_user_allow_executes(tmp_path: Path) -> None:
    """default mode + 无规则 + 用户选 Y → 放行执行。"""
    merged = _make_merged(tmp_path, mode="default")
    engine = RuleEngine(merged=merged)
    callback = _FakeCallback([True])  # 用户放行

    llm = _ScriptedLLM([
        [_bash_use("c1", "ls"), _usage(20, 10)],
        [_text("ok"), _usage(10, 5)],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_V2Perms(),
        config=make_minimal_config(),
        permission_callback=callback.__call__,
        merged_permissions=merged,
        permissions_engine=engine,
    )

    events = await _drain(agent, "list")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    assert len(tool_results) >= 1
    first = tool_results[0]
    assert not first.is_error, f"用户放行 → 应执行: {first.content}"
    assert len(callback.calls) == 1


# ---- SESSION 放行累积 ----

async def test_session_rule_persists_across_calls(tmp_path: Path) -> None:
    """用户点 A 本会话 → 第 2 次同/相近命令不再弹 Modal(L3 session allow 命中)。

    用 "git status" / "git status -sb":derive_glob_pattern 返回 "git status *",
    两条命令都命中。
    """
    merged = _make_merged(tmp_path, mode="default")
    engine = RuleEngine(merged=merged)
    callback = _FakeCallback([True])  # 只在第 1 次调

    # LLM 脚本:第 1 轮 git status,第 2 轮 git status -sb,第 3 轮收尾
    llm = _ScriptedLLM([
        [_bash_use("c1", "git status"), _usage(20, 10)],
        [_bash_use("c2", "git status -sb"), _usage(20, 10)],
        [_text("done"), _usage(10, 5)],
    ])

    # 模拟 Modal 在用户选 True 后调用 engine.add_session_rule
    from baozicode.tui.permission_modal import derive_glob_pattern

    async def callback_with_session_rule(call: ToolCall) -> bool:
        callback.calls.append(call)
        if not callback.choices:
            return False
        result = callback.choices.pop(0)
        if result:
            # 模拟 SESSION 行为:加一条 session rule
            pattern = derive_glob_pattern(call)
            engine.add_session_rule(PermissionRule(
                tool=call.name, pattern=pattern, decision="allow",
            ))
        return result

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_V2Perms(),
        config=make_minimal_config(),
        permission_callback=callback_with_session_rule,
        merged_permissions=merged,
        permissions_engine=engine,
    )

    events = await _drain(agent, "git status twice")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    # 两条命令都应执行(is_error=False)
    assert len(tool_results) >= 2
    c1_result = next(r for r in tool_results if r.tool_call_id == "c1")
    c2_result = next(r for r in tool_results if r.tool_call_id == "c2")
    assert not c1_result.is_error
    assert not c2_result.is_error, (
        f"第 2 次 git status 应被 session rule 放行,实际: {c2_result.content}"
    )
    # callback 只被调 1 次(第 1 次走 L5,第 2 次走 L3 session allow 短路)
    assert len(callback.calls) == 1


# ---- 重试 previously denied → Modal 重新弹出 ----

async def test_retry_after_deny_still_invokes_callback(tmp_path: Path) -> None:
    """L1 拒过的命令,LLM 改下参数重试 → L1 不一定再拒(参数变了),但 default mode 下
    弹 Modal(模拟"用户再决定一次")。

    这里测的是"参数变了所以 L1 不拦,但 L4 default 弹 Modal"。
    """
    merged = _make_merged(tmp_path, mode="default")
    engine = RuleEngine(merged=merged)
    # 第 1 次 echo 拒,第 2 次 echo(参数不同)放
    callback = _FakeCallback([False, True])

    llm = _ScriptedLLM([
        [_bash_use("c1", "echo dangerous_word"), _usage(20, 10)],
        [_bash_use("c2", "echo safe_word"), _usage(20, 10)],
        [_text("done"), _usage(10, 5)],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_V2Perms(),
        config=make_minimal_config(),
        permission_callback=callback.__call__,
        merged_permissions=merged,
        permissions_engine=engine,
    )

    events = await _drain(agent, "echo stuff")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    c1 = next(r for r in tool_results if r.tool_call_id == "c1")
    c2 = next(r for r in tool_results if r.tool_call_id == "c2")
    assert c1.is_error, "第 1 次应被用户拒"
    assert not c2.is_error, "第 2 次不同参数应被用户放行"
    assert len(callback.calls) == 2


# ---- permissive mode → fallthrough 全部放行(无 Modal) ----

async def test_permissive_mode_skips_modal(tmp_path: Path) -> None:
    """permissive mode → L4 把 fallthrough 转成 allow,L5 不弹。"""
    merged = _make_merged(tmp_path, mode="permissive")
    engine = RuleEngine(merged=merged)
    callback = _FakeCallback([])  # 不应被调

    llm = _ScriptedLLM([
        [_bash_use("c1", "ls /tmp"), _usage(20, 10)],
        [_text("done"), _usage(10, 5)],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_V2Perms(),
        config=make_minimal_config(),
        permission_callback=callback.__call__,
        merged_permissions=merged,
        permissions_engine=engine,
    )

    events = await _drain(agent, "list /tmp")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    assert len(tool_results) >= 1
    first = tool_results[0]
    # L4 permissive 放行,但 Bash 命令本身可能因沙箱被拒(看 sandbox 行为)
    # 这里"ls /tmp"在 project_root 内应该 OK(L2 通过)
    assert "L4_mode" not in first.content, (
        f"permissive 不应走到 L4 deny: {first.content}"
    )
    # callback 确实没被调
    assert callback.calls == []


if __name__ == "__main__":
    # 直接运行入口
    import pytest
    pytest.main([__file__, "-v"])
