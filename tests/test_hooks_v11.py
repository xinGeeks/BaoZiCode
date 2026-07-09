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
from baozicode.llm.base import LLMClient
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


# ---------- execute_action: http (mocked) ----------

def test_execute_http_deny_via_parse_expr(monkeypatch):
    """http 200 + parse_expr 设 res.deny=True → 拒,reason 来自 body。"""
    from baozicode.hooks.schema import _HttpAction
    import sys

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"risk": 0.9, "label": "dangerous"}

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, **k):
            return _Resp()

        def post(self, url, **k):
            return _Resp()

    class _FakeAiohttp:
        ClientSession = _Session

    # 关键:executor 内 `import aiohttp` 走 sys.modules,需要替换 sys.modules
    # 同时也设置模块 namespace(raise=False),让 `import aiohttp` 走 module 查找
    monkeypatch.setitem(sys.modules, "aiohttp", _FakeAiohttp)
    from baozicode.hooks import executor as exec_mod
    monkeypatch.setattr(exec_mod, "aiohttp", _FakeAiohttp, raising=False)

    action = _HttpAction.model_validate({
        "action": "http",
        "url": "https://example.com/check",
        "method": "GET",
        "parse_expr": (
            "res.deny = body['risk'] > 0.8\n"
            "res.deny_reason = body['label']"
        ),
    })
    ctx = HookContext(event="tool.pre", hook_id="http-deny", agent=None)
    result = asyncio.run(execute_action(action, ctx))
    assert result.deny is True
    assert result.reason == "dangerous"


def test_execute_http_allow_when_parse_expr_keeps_allow(monkeypatch):
    """http 200 + parse_expr 不设 deny → 放行。"""
    from baozicode.hooks.schema import _HttpAction
    import sys

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"risk": 0.1, "label": "safe"}

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, **k):
            return _Resp()

        def post(self, url, **k):
            return _Resp()

    class _FakeAiohttp:
        ClientSession = _Session

    monkeypatch.setitem(sys.modules, "aiohttp", _FakeAiohttp)
    from baozicode.hooks import executor as exec_mod
    monkeypatch.setattr(exec_mod, "aiohttp", _FakeAiohttp, raising=False)

    action = _HttpAction.model_validate({
        "action": "http",
        "url": "https://example.com/check",
        "parse_expr": "res.deny = body['risk'] > 0.8",
    })
    ctx = HookContext(event="tool.pre", hook_id="http-allow", agent=None)
    result = asyncio.run(execute_action(action, ctx))
    assert result.deny is False


def test_execute_http_4xx_does_not_auto_deny(monkeypatch):
    """http 4xx/5xx → 不主动 deny(spec:接口错 ≠ 主动拦),只记 error。"""
    from baozicode.hooks.schema import _HttpAction
    import sys

    class _Resp:
        status = 503

        async def json(self, content_type=None):
            return {"error": "service unavailable"}

        async def text(self):
            return "service unavailable"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, **k):
            return _Resp()

        def post(self, url, **k):
            return _Resp()

    class _FakeAiohttp:
        ClientSession = _Session

    monkeypatch.setitem(sys.modules, "aiohttp", _FakeAiohttp)
    from baozicode.hooks import executor as exec_mod
    monkeypatch.setattr(exec_mod, "aiohttp", _FakeAiohttp, raising=False)

    action = _HttpAction.model_validate({
        "action": "http",
        "url": "https://example.com/check",
        "parse_expr": "res.deny = status == 200 and body.get('blocked', False)",
    })
    ctx = HookContext(event="tool.pre", hook_id="http-503", agent=None)
    result = asyncio.run(execute_action(action, ctx))
    assert result.deny is False  # 4xx/5xx 不自动拒


def test_execute_http_connection_error_returns_no_deny(monkeypatch):
    """http 连接异常 → deny=False,error 字段有值(不阻断主流程)。"""
    from baozicode.hooks.schema import _HttpAction
    import sys

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, **k):
            raise ConnectionError("network unreachable")

        def post(self, url, **k):
            raise ConnectionError("network unreachable")

    class _FakeAiohttp:
        ClientSession = _Session

    monkeypatch.setitem(sys.modules, "aiohttp", _FakeAiohttp)
    from baozicode.hooks import executor as exec_mod
    monkeypatch.setattr(exec_mod, "aiohttp", _FakeAiohttp, raising=False)

    # 无 parse_expr(连接错时根本不走 parse_expr),但为了让 mock 路径到达外层 except,
    # 不给 parse_expr。
    action = _HttpAction.model_validate({
        "action": "http",
        "url": "https://example.com/check",
    })
    ctx = HookContext(event="tool.pre", hook_id="http-conn", agent=None)
    result = asyncio.run(execute_action(action, ctx))
    assert result.deny is False
    assert result.error and "network unreachable" in result.error


# ---------- Agent integration ----------

def test_agent_reminder_kind_includes_hook_prompt():
    """ReminderKind literal 在 v1.1 包含 hook_prompt。"""
    from baozicode.agent.loop import Agent, ReminderKind

    # ReminderKind literal 现在包含 hook_prompt
    assert "hook_prompt" in ReminderKind.__args__


# ---------- v1.1.1:run_once 运行时强制 ----------

def test_dispatcher_run_once_fires_then_skips():
    """run_once=True 的 hook 全 session 只跑一次 —— 第二次 run() 跳过整条。"""
    raw = [{
        "id": "session-bootstrap",
        "event": "turn.start",
        "run_once": True,
        "actions": [{"action": "shell", "command": "exit 1", "timeout_seconds": 5}],
    }]
    r = HookRegistry.load(raw)
    r.freeze()
    d = r.create_dispatcher(agent=None)
    # 第一次:触发(应当 deny 因为 exit 1)
    res1 = d.run("turn.start", None)
    assert res1.denied is True
    assert res1.denied_hook_id == "session-bootstrap"
    # 第二次:run_once 已 fire,跳过整条 → 不 deny
    res2 = d.run("turn.start", None)
    assert res2.denied is False


def test_dispatcher_run_once_false_fires_every_time():
    """run_once 默认 False → 每次 run() 都跑。"""
    raw = [{
        "id": "always-fire",
        "event": "turn.start",
        "actions": [{"action": "shell", "command": "exit 1", "timeout_seconds": 5}],
    }]
    r = HookRegistry.load(raw)
    r.freeze()
    d = r.create_dispatcher(agent=None)
    for _ in range(3):
        res = d.run("turn.start", None)
        assert res.denied is True
        assert res.denied_hook_id == "always-fire"


# ---------- v1.1.1:HookAuditLog 接线 ----------

def test_dispatcher_audit_log_writes_invocation(tmp_path):
    """dispatcher 跑完一条 hook,audit_log 应当落一条 HookInvocation JSONL。"""
    from baozicode.hooks.audit import HookAuditLog, HookInvocation

    log_path = tmp_path / "hooks.audit.jsonl"
    audit_log = HookAuditLog(log_path)
    raw = [{
        "id": "audit-bash",
        "event": "tool.pre",
        "if": {"all": [{"tool": "Bash"}]},
        "actions": [{"action": "shell", "command": "exit 0", "timeout_seconds": 5}],
    }]
    r = HookRegistry.load(raw)
    r.freeze()
    d = r.create_dispatcher(agent=None, audit_log=audit_log)
    call = MagicMock()
    call.name = "Bash"
    call.id = "call-1"
    call.arguments = {"command": "ls"}
    d.run("tool.pre", call)
    # 同步路径已写 → 应当有一行
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8").strip()
    assert content, "audit log 应至少有一行"
    # 解析 JSON 验证字段
    import json as _json
    line = _json.loads(content)
    assert line["hook_id"] == "audit-bash"
    assert line["event"] == "tool.pre"
    assert line["tool_name"] == "Bash"
    assert line["tool_call_id"] == "call-1"
    assert line["deny"] is False
    assert "duration_ms" in line


def test_dispatcher_audit_log_records_deny(tmp_path):
    """hook 拒 → audit 记录 deny=True + reason。"""
    from baozicode.hooks.audit import HookAuditLog

    log_path = tmp_path / "hooks.audit.jsonl"
    audit_log = HookAuditLog(log_path)
    raw = [{
        "id": "deny-bad",
        "event": "tool.pre",
        "actions": [{"action": "shell", "command": "echo blocked; exit 1", "timeout_seconds": 5}],
    }]
    r = HookRegistry.load(raw)
    r.freeze()
    d = r.create_dispatcher(agent=None, audit_log=audit_log)
    call = MagicMock()
    call.name = "Bash"
    call.id = "call-2"
    call.arguments = {}
    res = d.run("tool.pre", call)
    assert res.denied is True
    import json as _json
    line = _json.loads(log_path.read_text(encoding="utf-8").strip())
    assert line["deny"] is True
    assert line["reason"] == "blocked"
    assert line["hook_id"] == "deny-bad"


# ---------- Agent.run 完整 lifecycle E2E ----------

def test_agent_run_lifecycle_events_fire_in_order():
    """Agent.run 11 个事件按序 fire(text-only 1 轮 + tool-call 1 轮 + 收尾 1 轮)。

    验证:
    - message.received → session.start → turn.start(1) → ... → turn.end(1) →
      message.sent(1) → turn.start(2) → session.end
    - session.start 必在 turn.start 之前
    - session.end 必在最后(finally 兜底)
    - turn.end / message.sent 配对出现
    """
    from baozicode.agent.events import StopReason, UsageStats
    from baozicode.agent.loop import Agent
    from baozicode.conversation.manager import ConversationManager
    from baozicode.llm.base import ContentDelta
    from baozicode.tools.registry import get_all_tools
    from collections.abc import AsyncIterator
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from _agent_helpers import make_minimal_config

    fired: list[tuple[str, Any]] = []

    class _MiniDispatcher:
        def run(self, event: str, payload: Any):
            fired.append((event, payload))
            return None

    # 第 1 轮:text + tool_use(Read)+ usage;第 2 轮:text + usage
    class _TwoTurnLLM(LLMClient):
        def __init__(self):
            self.call_count = 0

        async def stream(
            self, messages, system, tools, *, cache_breakpoints=None
        ) -> AsyncIterator[ContentDelta]:
            self.call_count += 1
            if self.call_count == 1:
                yield ContentDelta(type="text", text="let me read")
                yield ContentDelta(
                    type="tool_use",
                    text=ToolCall(
                        id="t1", name="Read",
                        arguments={"file_path": "/dev/null"},
                    ),
                )
                yield ContentDelta(type="usage", text=UsageStats(input_tokens=10, output_tokens=5))
            else:
                yield ContentDelta(type="text", text="done")
                yield ContentDelta(type="usage", text=UsageStats(input_tokens=5, output_tokens=3))

    config = make_minimal_config()
    from baozicode.agent.loop import Agent
    agent = Agent(
        llm_client=_TwoTurnLLM(),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=None,
        config=config,
        hook_dispatcher=_MiniDispatcher(),  # type: ignore[arg-type]
    )

    events: list[AgentEvent] = []

    async def _collect():
        async for ev in agent.run("hi"):
            events.append(ev)

    asyncio.run(_collect())

    names = [e for e, _ in fired]
    # 起点 + session.end 收尾
    assert names[0] == "message.received"
    assert names[-1] == "session.end"
    # session.start 在 turn.start 之前
    assert names.index("session.start") < names.index("turn.start")
    # turn.end 出现 ≥ 1 次(第 1 轮配 tool_call 有 turn.end/message.sent)
    assert "turn.end" in names
    assert "message.sent" in names
    # turn.end 在 message.sent 之前(同轮配对顺序)
    assert names.index("turn.end") < names.index("message.sent")
    # 收尾是 STREAM_ERROR 之外的成功路径(COMPLETED)
    done = [e for e in events if e.type == "done"]
    assert done and done[0].payload == StopReason.COMPLETED


def test_agent_run_session_end_fires_even_on_exception():
    """Agent.run 主循环内未处理异常 → session.end 仍 fire(finally 兜底)。"""
    from baozicode.agent.events import UsageStats
    from baozicode.agent.loop import Agent
    from baozicode.conversation.manager import ConversationManager
    from baozicode.llm.base import ContentDelta
    from baozicode.tools.registry import get_all_tools
    from collections.abc import AsyncIterator
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from _agent_helpers import make_minimal_config

    fired: list[str] = []

    class _MiniDispatcher:
        def run(self, event: str, payload: Any):
            fired.append(event)
            return None

    class _TextLLM(LLMClient):
        async def stream(
            self, messages, system, tools, *, cache_breakpoints=None
        ) -> AsyncIterator[ContentDelta]:
            yield ContentDelta(type="text", text="ok")
            yield ContentDelta(type="usage", text=UsageStats(input_tokens=1, output_tokens=1))

    config = make_minimal_config()
    from baozicode.agent.loop import Agent
    agent = Agent(
        llm_client=_TextLLM(),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=None,
        config=config,
        hook_dispatcher=_MiniDispatcher(),  # type: ignore[arg-type]
    )

    # patch turn.start 抛错
    original = agent._fire_lifecycle_safe

    def _boom(event: str, payload: Any) -> None:
        if event == "turn.start":
            raise RuntimeError("boom")
        original(event, payload)

    agent._fire_lifecycle_safe = _boom  # type: ignore[method-assign]

    async def _run():
        async for _ in agent.run("hi"):
            pass

    asyncio.run(_run())
    # session.end 必须在最后(finally 块)
    assert fired[-1] == "session.end"

def test_agent_run_fires_system_error_on_outer_exception():
    """Agent.run 主循环内未处理异常时,system.error 事件 + AgentEvent.error 应触发。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))

    from baozicode.agent.events import AgentEvent, StopReason
    from baozicode.agent.loop import Agent
    from baozicode.conversation.manager import ConversationManager
    from baozicode.llm.base import ContentDelta, LLMClient
    from baozicode.tools.registry import get_all_tools
    from _agent_helpers import make_minimal_config

    # 收集 lifecycle 事件的 mini dispatcher(不真跑 hook)
    fired: list[tuple[str, Any]] = []

    class _MiniDispatcher:
        def run(self, event: str, payload: Any):
            fired.append((event, payload))
            return None  # 真实 dispatcher 返回 HookResult

    # LLM 第一次正常返回(一段 text),第二轮才抛 — 这样能进 while 循环,
    # 在第二轮的 LLM stream 抛异常。但内层 try/except (line 463-482) 会接住
    # 转成 STREAM_ERROR,不走外层 except。
    # 真正测试外层 except:patch turn.start 的 fire 让其抛 RuntimeError,
    # 这样 raise 在外层 try 块内(turn.start 调用点在 while 顶部),
    # 且不被任何内层 try/except 包 → 走外层 except → fire system.error。
    class _TextOnlyLLM(LLMClient):
        def __init__(self):
            self.call_count = 0

        async def stream(self, messages, system, tools, *, cache_breakpoints=None):
            self.call_count += 1
            yield ContentDelta(type="text", text="ok")
            yield ContentDelta(type="usage", text=ContentDelta.make_usage(1, 1))

    config = make_minimal_config()
    agent = Agent(
        llm_client=_TextOnlyLLM(),
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=None,
        config=config,
        hook_dispatcher=_MiniDispatcher(),  # type: ignore[arg-type]
    )

    # patch _fire_lifecycle_safe:turn.start 时抛,其他事件原样
    original_fire = agent._fire_lifecycle_safe

    def _boom_fire(event: str, payload: Any) -> None:
        if event == "turn.start":
            raise RuntimeError("simulated hook runtime error")
        original_fire(event, payload)

    agent._fire_lifecycle_safe = _boom_fire  # type: ignore[method-assign]

    # 跑 Agent.run,收集事件
    events: list[AgentEvent] = []

    async def _collect():
        async for ev in agent.run("hi"):
            events.append(ev)

    asyncio.run(_collect())

    # system.error 应被 fire(payload 是 RuntimeError)
    assert any(e == "system.error" for e, _ in fired), (
        f"system.error 未触发,实际 fire: {[e for e, _ in fired]}"
    )
    # session.end 仍 fire(finally 块保证)
    assert any(e == "session.end" for e, _ in fired)
    # AgentEvent.error 应 yield
    error_events = [e for e in events if e.type == "error"]
    assert error_events, "应有 error 事件 yield"
    # done 事件,terminate_reason=STREAM_ERROR(复用 "something failed" 语义)
    done_events = [e for e in events if e.type == "done"]
    assert done_events and done_events[0].payload == StopReason.STREAM_ERROR


# ---------- v1.1:条件 matchers 边界(not_exact)----------

def test_matchers_not_exact():
    """not_exact 是反向精确匹配 —— v1.0 权限规则已有,v1.1 条件语法复用。"""
    assert matchers["not_exact"]("foo", "foo") is False
    assert matchers["not_exact"]("foo", "bar") is True
    # 在 condition 表达式里使用
    cond = ConditionYaml.model_validate({
        "all": [{"arg": {"path": {"kind": "not_exact", "value": "/etc"}}}],
    })
    assert evaluate_condition(cond, _FakeCall("Read", {"path": "/tmp"})) is True
    assert evaluate_condition(cond, _FakeCall("Read", {"path": "/etc"})) is False
