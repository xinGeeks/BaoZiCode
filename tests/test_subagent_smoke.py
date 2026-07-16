"""SubAgent 单轮委托 smoke test — 不打真实 LLM。

走完整 v1.2 SubAgent 路径:
  AgentRegistry.scan → SubAgentRuntime → SubAgentManager.dispatch(
    type="definition", role="<name>") → 等 task.done

v1.5 起:summarizer 也可走(tools=[] 显式空 = ToolFilter 放行)。
FakeLLM 只 yield text + usage,流自然结束 → Agent 看到无 tool_use → COMPLETED。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import AgentEvent, ProgressPhase, StopReason, UsageStats
from baozicode.agents.manager import SubAgentManager
from baozicode.agents.registry import AgentRegistry
from baozicode.agents.runtime import SubAgentRuntime
from baozicode.config.schema import BackendConfig
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
import pytest
from tests._agent_helpers import make_minimal_config

REPO = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Fake LLM — 返回固定 text + done,不调任何工具
# ---------------------------------------------------------------------------


class FakeLLM(LLMClient):
    """最简 LLM:每次 stream 都 yield 一段 text,然后 yield done。"""

    def __init__(self, reply: str = "fake-summary-output") -> None:
        self._reply = reply
        self.stream_calls: list[tuple[int, list[Message]]] = []

    async def stream(
        self,
        messages: list[Message],
        system: str,
        tools,
        *,
        cache_breakpoints=None,
    ):
        # 记录调用,便于断言 "sub-Agent 真去打了 LLM"
        self.stream_calls.append((len(self.stream_calls), list(messages)))
        # 文本 chunk
        yield ContentDelta(type="text", text=self._reply)
        # 用量
        yield ContentDelta(
            type="usage",
            text=UsageStats(
                input_tokens=10, output_tokens=5,
                cache_read_tokens=0, cache_write_tokens=0,
            ),
        )
        # 流自然结束 → Agent 看到无 tool_use → COMPLETED


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


async def run_test() -> None:
    print("=== SubAgent smoke test ===\n")

    # 1) Config
    config = make_minimal_config(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="fake-model"),
        system_prompt="主 Agent 默认 prompt",
    )

    # 2) Registry — 只扫 builtin(无 user/project override)
    builtin_dir = REPO / "baozicode" / "agents" / "builtin"
    registry = AgentRegistry.scan(builtin_dir=builtin_dir)
    visible = registry.list_visible()
    print(f"[1] registry loaded: {[n for n, _, _ in visible]}")
    assert any(name == "explorer" for name, _, _ in visible), \
        "explorer role 必须能 load"
    role_def = registry.lookup("explorer")
    assert role_def is not None
    print(f"    explorer frontmatter.tools={role_def.frontmatter.tools}")
    print(f"    explorer frontmatter.tools_deny={role_def.frontmatter.tools_deny}")
    print(f"    explorer model={role_def.frontmatter.model}")

    # 3) Tool registry
    from baozicode.tools.registry import get_default_tool_registry
    tool_registry = get_default_tool_registry()

    # 4) Fake LLM
    llm = FakeLLM(reply="[explorer] 已扫描项目结构。")

    # 5) Runtime + Manager
    runtime = SubAgentRuntime(
        llm=llm,
        hooks=None,
        tool_registry=tool_registry,
        project_root=REPO,
        config=config,
        registry=registry,
    )
    main_conv = ConversationManager(archiver=None)
    manager = SubAgentManager(
        runtime=runtime,
        main_conversation=main_conv,
        max_concurrent=2,
        task_retention_minutes=1,
    )

    # 6) Dispatch — definition / explorer / async=True 后台跑 + 轮询
    # (注:_dispatch_sync_blocking 在 asyncio.run 上下文里有 bug,改走 async 路径)
    print("\n[2] dispatch type=definition role=explorer async_=True ...")
    task_id = await manager.dispatch(
        type="definition",
        role="explorer",
        prompt="请概览项目结构。",
        async_=True,
    )
    assert isinstance(task_id, str), f"async 路径应返回 task_id,得到 {task_id!r}"
    print(f"    dispatch() returned task_id={task_id}")

    # 轮询等完成(每 100ms 一次,上限 10s)
    import time
    deadline = time.time() + 10.0
    task = manager.get_task(task_id)
    while task is not None and task.state not in (
        "done", "failed", "canceled", "timeout",
    ):
        if time.time() > deadline:
            raise AssertionError(f"task {task_id} 10s 内未完成")
        await asyncio.sleep(0.1)
    print(f"    task 完成,state={task.state}")

    # 7) 断言:状态正确 + LLM 真被打过 + 只打过 1 次(sub-Agent 一次性)
    tasks = manager.list_tasks()
    assert len(tasks) == 1, f"应该 1 个 task,实际 {len(tasks)}"
    task = tasks[0]
    print(f"\n[3] task state: {task.state}")
    print(f"    task role: {task.role}")
    print(f"    task type: {task.type}")
    print(f"    task result: {task.result!r}")
    print(f"    task.usage.input_tokens={task.usage.input_tokens}")
    assert task.state == "done", f"期望 done,实际 {task.state}"
    assert task.role == "explorer"
    assert task.type == "definition"
    assert "explorer" in (task.result or ""), "result 应包含 fake LLM 输出"
    assert len(llm.stream_calls) == 1, \
        f"sub-Agent 应该只调 1 次 LLM,实际 {len(llm.stream_calls)}"

    # 8) idle 路径:结果应该已塞进 main_conv(manager._on_subagent_done 走 idle 分支)
    msgs = main_conv.to_list()
    print(f"\n[4] main_conv 消息数: {len(msgs)}")
    assert len(msgs) >= 1, "idle 时结果应塞进主对话"
    last = msgs[-1]
    print(f"    最后一条 role={last.role}, content 前 80 字: {str(last.content)[:80]!r}")
    assert last.role == "user", "sub-Agent idle 结果应作 user message"

    print("\n=== ALL ASSERTIONS PASSED ===")


# ---------------------------------------------------------------------------
# v1.5: summarizer 端到端(tools=[] 显式空 → filter 放行)
# ---------------------------------------------------------------------------


async def _run_role(role: str, reply: str) -> None:
    """helper:跑一个 definition 派发 + 轮询完成 + 断言。

    summarizer 走和 explorer 一样的路径,只是 tool 集合不同。
    """
    config = make_minimal_config(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="fake-model"),
        system_prompt="主 Agent 默认 prompt",
    )
    builtin_dir = REPO / "baozicode" / "agents" / "builtin"
    registry = AgentRegistry.scan(builtin_dir=builtin_dir)
    role_def = registry.lookup(role)
    assert role_def is not None, f"role {role!r} 必须能 load"

    from baozicode.tools.registry import get_default_tool_registry
    tool_registry = get_default_tool_registry()
    llm = FakeLLM(reply=reply)
    runtime = SubAgentRuntime(
        llm=llm,
        hooks=None,
        tool_registry=tool_registry,
        project_root=REPO,
        config=config,
        registry=registry,
    )
    main_conv = ConversationManager(archiver=None)
    manager = SubAgentManager(
        runtime=runtime,
        main_conversation=main_conv,
        max_concurrent=2,
        task_retention_minutes=1,
    )

    task_id = await manager.dispatch(
        type="definition", role=role, prompt="x", async_=True,
    )
    assert isinstance(task_id, str)

    import time
    deadline = time.time() + 10.0
    task = manager.get_task(task_id)
    while task is not None and task.state not in (
        "done", "failed", "canceled", "timeout",
    ):
        if time.time() > deadline:
            raise AssertionError(f"task {task_id} 10s 内未完成")
        await asyncio.sleep(0.1)

    assert task.state == "done", f"{role}: 期望 done,实际 {task.state}"
    assert task.role == role
    assert task.result is not None and reply in task.result


async def test_summarizer_end_to_end() -> None:
    """v1.5: summarizer(tools=[])可派发且走完 sub-Agent 主路径。"""
    await _run_role("summarizer", "[summarizer] 摘要:3 段。")


# ---------------------------------------------------------------------------
# v1.5: async_=False 抛 NotImplementedError(同步路径已删除)
# ---------------------------------------------------------------------------


async def test_dispatch_async_false_raises() -> None:
    """v1.5:dispatch(async_=False) 必须抛 NotImplementedError,不静默 fallback。"""
    from baozicode.agents.manager import SubAgentManager
    from baozicode.agents.registry import AgentRegistry
    from baozicode.agents.runtime import SubAgentRuntime
    from baozicode.config.schema import BackendConfig
    from baozicode.conversation.manager import ConversationManager
    from baozicode.tools.registry import get_default_tool_registry

    config = make_minimal_config(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
    )
    builtin_dir = REPO / "baozicode" / "agents" / "builtin"
    registry = AgentRegistry.scan(builtin_dir=builtin_dir)
    tool_registry = get_default_tool_registry()
    runtime = SubAgentRuntime(
        llm=FakeLLM(),
        hooks=None,
        tool_registry=tool_registry,
        project_root=REPO,
        config=config,
        registry=registry,
    )
    main_conv = ConversationManager(archiver=None)
    manager = SubAgentManager(
        runtime=runtime,
        main_conversation=main_conv,
        max_concurrent=2,
        task_retention_minutes=1,
    )

    with pytest.raises(NotImplementedError) as exc_info:
        await manager.dispatch(
            type="definition",
            role="explorer",
            prompt="x",
            async_=False,  # v1.5 起应抛
        )
    msg = str(exc_info.value)
    assert "v1.5" in msg
    assert "async_=True" in msg


if __name__ == "__main__":
    asyncio.run(run_test())