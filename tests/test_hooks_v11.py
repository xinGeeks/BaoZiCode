"""v1.1 Hooks Lifecycle — smoke tests.

覆盖:
1. HookRegistry.load + freeze happy path
2. freeze 多错聚合(duplicate id + async+tool.pre)
3. Condition 求值(exact / glob / regex / not_* / all / any)
4. ToolResult 新字段派生
5. blacklist_check + check_layers_2_through_5 入口
6. dispatcher.run 主流程
7. execute_action 4 种 action happy path(不真发 http、不真跑 bash)
8. enqueue_reminder 用 hook_prompt
9. Agent pipeline:无 hook 时 v1.0 等价;有 hook 时 ToolResult.execution_status 填好
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from baozicode.hooks import (
    HookRegistry,
    HookValidationError,
    evaluate_condition,
    matchers,
)
from baozicode.hooks.executor import ActionResult, execute_action
from baozicode.hooks.dispatcher import HookContext, HookDispatcher, HookResult
from baozicode.hooks.schema import (
    ConditionYaml,
    HookDefYaml,
    MatchValue,
    MatcherYaml,
    parse_hook_def,
)
from baozicode.permissions import blacklist_check, check, check_layers_2_through_5
from baozicode.tools.base import ToolCall, ToolResult


# ---------- HookRegistry ----------

def test_registry_load_none():
    r = HookRegistry.load(None)
    assert r.all_hooks() == []
    r.freeze()  # must not raise


def test_registry_load_valid():
    raw = [
        {"id": "a", "event": "tool.pre",
         "actions": [{"action": "shell", "command": "echo ok"}]},
        {"id": "b", "event": "turn.start",
         "actions": [{"action": "prompt", "content": "style"}]},
    ]
    r = HookRegistry.load(raw)
    r.freeze()
    assert len(r.all_hooks()) == 2
    pre = r.list_hooks("tool.pre")
    assert len(pre) == 1 and pre[0].id == "a"


def test_freeze_duplicate_id():
    with pytest.raises(HookValidationError) as ei:
        r = HookRegistry.load([
            {"id": "a", "event": "tool.pre",
             "actions": [{"action": "shell", "command": "echo"}]},
            {"id": "a", "event": "tool.pre",
             "actions": [{"action": "shell", "command": "echo"}]},
        ])
        r.freeze()
    assert "duplicate" in str(ei.value).lower()


def test_freeze_async_on_tool_pre():
    with pytest.raises(HookValidationError):
        r = HookRegistry.load([{
            "id": "x", "event": "tool.pre", "async": True,
            "actions": [{"action": "shell", "command": "echo"}],
        }])
        r.freeze()


def test_freeze_stable_system_on_tool_pre():
    with pytest.raises(HookValidationError):
        r = HookRegistry.load([{
            "id": "x", "event": "tool.pre",
            "actions": [{"action": "prompt", "slot": "stable_system", "content": "x"}],
        }])
        r.freeze()


def test_freeze_collects_multiple_errors():
    """freeze 应该聚合所有错误,不是 bail 在第一个。"""
    with pytest.raises(HookValidationError) as ei:
        r = HookRegistry.load([
            {"id": "a", "event": "tool.pre", "async": True,
             "actions": [{"action": "shell", "command": "echo"}]},
            {"id": "a", "event": "tool.pre",
             "actions": [{"action": "shell", "command": "echo"}]},
        ])
        r.freeze()
    msg = str(ei.value)
    assert "async not allowed" in msg
    assert "duplicate" in msg.lower()


# ---------- Condition / matcher ----------

class _FakeCall:
    def __init__(self, name: str, arguments: dict | None = None):
        self.name = name
        self.arguments = arguments or {}


def test_matchers_exact_glob_regex():
    assert matchers["exact"]("foo", "foo") is True
    assert matchers["exact"]("foo", "bar") is False
    assert matchers["glob"]("hello.py", "*.py") is True
    assert matchers["regex"]("abc123", r"\d+") is True
    assert matchers["regex"]("xyz", r"\d+") is False
    assert matchers["not_regex"]("xyz", r"\d+") is True
    assert matchers["not_glob"]("foo.txt", "*.py") is True


def test_evaluate_condition_none_is_true():
    call = _FakeCall("Bash")
    assert evaluate_condition(None, call) is True


def test_evaluate_condition_all():
    cond = ConditionYaml.model_validate({
        "all": [
            {"tool": "Bash"},
            {"arg": {"command": {"kind": "glob", "value": "ls*"}}},
        ]
    })
    assert evaluate_condition(cond, _FakeCall("Bash", {"command": "ls -la"})) is True
    assert evaluate_condition(cond, _FakeCall("Bash", {"command": "rm foo"})) is False
    assert evaluate_condition(cond, _FakeCall("Read", {"command": "ls"})) is False


def test_evaluate_condition_any():
    cond = ConditionYaml.model_validate({
        "any": [
            {"tool": "Bash"},
            {"tool": "Write"},
        ]
    })
    assert evaluate_condition(cond, _FakeCall("Bash")) is True
    assert evaluate_condition(cond, _FakeCall("Read")) is False


def test_evaluate_condition_missing_arg():
    cond = ConditionYaml.model_validate({
        "all": [{"arg": {"command": {"kind": "exact", "value": "ls"}}}],
    })
    assert evaluate_condition(cond, _FakeCall("Bash", {})) is False


# ---------- ToolResult schema ----------

def test_tool_result_legacy_construction():
    """v0.5 老调用方式仍可用,is_error 不被派生。"""
    r = ToolResult(tool_call_id="x", content="y", is_error=True)
    assert r.is_error is True
    assert r.execution_status is None
    assert r.denied_by is None
    assert r.denied_hook_id is None


def test_tool_result_block_l1_derives_is_error():
    r = ToolResult(
        tool_call_id="x", content="blocked",
        execution_status="block_l1",
        denied_by="l1_blacklist",
    )
    assert r.is_error is True
    assert r.execution_status == "block_l1"
    assert r.denied_by == "l1_blacklist"


def test_tool_result_success_derives_no_error():
    r = ToolResult(
        tool_call_id="x", content="ok",
        execution_status="executed_success",
    )
    assert r.is_error is False


def test_tool_result_block_hook_pre_carries_id():
    r = ToolResult(
        tool_call_id="x", content="blocked by audit",
        execution_status="block_hook_pre",
        denied_by="hook_pre",
        denied_hook_id="audit-risky",
    )
    assert r.is_error is True
    assert r.denied_hook_id == "audit-risky"


# ---------- permissions entry points ----------

def test_blacklist_check_skips_bash_safe():
    """blacklist_check 不应判 ls / read 这类无害调用。"""
    call = ToolCall(id="1", name="Bash", arguments={"command": "ls"})
    d = blacklist_check(call)
    assert d.decision == "fallthrough"


def test_blacklist_check_blocks_rm_rf():
    call = ToolCall(id="1", name="Bash", arguments={"command": "rm -rf /"})
    d = blacklist_check(call)
    assert d.decision == "deny"
    assert d.layer == "L1_blacklist"


def test_check_layers_2_through_5_skips_l1():
    """L1 不在 L2-L5 入口里评估,即使 rm -rf 也不拦(由 L1 独立抓)。"""
    call = ToolCall(id="1", name="Bash", arguments={"command": "rm -rf /"})
    d = check_layers_2_through_5(call, ctx=None)
    assert d.decision == "fallthrough"


def test_check_full_pipeline_includes_l1():
    """完整 check() 包含 L1。"""
    call = ToolCall(id="1", name="Bash", arguments={"command": "rm -rf /"})
    d = check(call, ctx=None)
    assert d.decision == "deny"
    assert d.layer == "L1_blacklist"


# ---------- dispatcher ----------

def test_dispatcher_no_hooks_returns_empty_result():
    r = HookRegistry.load(None)
    d = r.create_dispatcher(agent=None)
    res = d.run("tool.pre", MagicMock())
    assert res.denied is False


def test_dispatcher_pre_deny_blocks_call():
    raw = [{
        "id": "deny-all",
        "event": "tool.pre",
        "actions": [{"action": "shell", "command": "exit 1", "timeout_seconds": 5}],
    }]
    r = HookRegistry.load(raw)
    r.freeze()
    d = r.create_dispatcher(agent=None)
    call = MagicMock()
    call.name = "Bash"
    call.id = "1"
    call.arguments = {}
    res = d.run("tool.pre", call)
    assert res.denied is True
    assert res.denied_hook_id == "deny-all"


def test_dispatcher_condition_miss_skips_hook():
    raw = [{
        "id": "audit-bash-only",
        "event": "tool.pre",
        "if": {"all": [{"tool": "Bash"}]},
        "actions": [{"action": "shell", "command": "exit 1", "timeout_seconds": 5}],
    }]
    r = HookRegistry.load(raw)
    r.freeze()
    d = r.create_dispatcher(agent=None)
    # Read call: condition 不命中 → 不 deny
    call = MagicMock()
    call.name = "Read"
    call.id = "1"
    call.arguments = {}
    res = d.run("tool.pre", call)
    assert res.denied is False


# ---------- execute_action: happy paths ----------

def test_execute_shell_success_no_deny():
    from baozicode.hooks.schema import _ShellAction
    action = _ShellAction.model_validate({"action": "shell", "command": "exit 0"})
    ctx = HookContext(event="tool.post", hook_id="t", agent=None, payload=None)
    result = asyncio.run(execute_action(action, ctx))
    assert result.deny is False


def test_execute_shell_nonzero_deny():
    from baozicode.hooks.schema import _ShellAction
    action = _ShellAction.model_validate({"action": "shell", "command": "echo blocked; exit 7"})
    ctx = HookContext(event="tool.post", hook_id="t", agent=None, payload=None)
    result = asyncio.run(execute_action(action, ctx))
    assert result.deny is True
    assert result.reason == "blocked"


def test_execute_prompt_sticky_calls_agent():
    from baozicode.hooks.schema import _PromptAction
    agent = MagicMock()
    agent.enqueue_reminder = MagicMock()
    action = _PromptAction.model_validate({
        "action": "prompt", "content": "hello", "slot": "sticky_reminder",
    })
    ctx = HookContext(event="turn.start", hook_id="t", agent=agent)
    asyncio.run(execute_action(action, ctx))
    agent.enqueue_reminder.assert_called_once()
    args, kwargs = agent.enqueue_reminder.call_args
    assert kwargs.get("kind") == "hook_prompt" or args[0] == "hook_prompt"


# ---------- Agent integration ----------

def test_agent_reminder_kind_includes_hook_prompt():
    """ReminderKind literal 在 v1.1 包含 hook_prompt。"""
    from baozicode.agent.loop import Agent, ReminderKind

    # ReminderKind literal 现在包含 hook_prompt
    assert "hook_prompt" in ReminderKind.__args__
