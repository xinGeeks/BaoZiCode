"""v1.5:SubAgentManager 同步路径已删除 — async_=True 唯一。

覆盖:
1. dispatch(async_=False) 抛 NotImplementedError(msg 含 v1.5 / async_=True 提示)
2. SubAgentManager 类无 _dispatch_sync_blocking 属性
3. task_executor(async=False) 走异常路径
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from baozicode.agents.manager import (
    SubAgentManager,
    _do_dispatch,
    task_executor,
)
from baozicode.agents.registry import AgentRegistry
from baozicode.agents.runtime import SubAgentRuntime
from baozicode.config.schema import BackendConfig
from baozicode.conversation.manager import ConversationManager
from baozicode.tools.registry import get_default_tool_registry
from tests._agent_helpers import make_minimal_config

REPO = Path(__file__).parent.parent


def _make_manager() -> SubAgentManager:
    """最小可工作的 SubAgentManager(只为测 dispatch 入口,跑不到 spawn)。"""
    config = make_minimal_config(
        anthropic=BackendConfig(api_key="x", model="m"),
    )
    registry = AgentRegistry.scan(builtin_dir=REPO / "baozicode" / "agents" / "builtin")
    tool_registry = get_default_tool_registry()
    runtime = SubAgentRuntime(
        llm=None,  # type: ignore[arg-type]
        hooks=None,
        tool_registry=tool_registry,
        project_root=REPO,
        config=config,
        registry=registry,
    )
    main_conv = ConversationManager(archiver=None)
    return SubAgentManager(
        runtime=runtime,
        main_conversation=main_conv,
        max_concurrent=2,
        task_retention_minutes=1,
    )


# ---------------------------------------------------------------------------
# 测 1: dispatch(async_=False) 抛 NotImplementedError
# ---------------------------------------------------------------------------


def test_dispatch_async_false_raises_not_implemented():
    """v1.5:async_=False 必须立即抛 NotImplementedError,不能静默 fallback。"""
    mgr = _make_manager()

    async def _call():
        return await mgr.dispatch(
            type="definition", role="explorer", prompt="x", async_=False,
        )

    with pytest.raises(NotImplementedError) as exc_info:
        asyncio.run(_call())
    msg = str(exc_info.value)
    # 错误信息应给调用方明确指引
    assert "v1.5" in msg, f"msg 应含 v1.5 提示,实际:{msg!r}"
    assert "async_=True" in msg, f"msg 应含 async_=True 指引,实际:{msg!r}"


def test_dispatch_async_false_checked_before_role_lookup():
    """async_=False 应在 role 校验之前抛(避免无谓的 lookup)。"""
    mgr = _make_manager()

    async def _call():
        # role 给一个不存在的名字,确认 NotImplementedError 优先
        return await mgr.dispatch(
            type="definition", role="nonexistent", prompt="x", async_=False,
        )

    with pytest.raises(NotImplementedError):
        asyncio.run(_call())


# ---------------------------------------------------------------------------
# 测 2: SubAgentManager 类无 _dispatch_sync_blocking
# ---------------------------------------------------------------------------


def test_manager_class_no_sync_blocking_method():
    """v1.5:_dispatch_sync_blocking 必须从类上彻底删除。"""
    assert not hasattr(SubAgentManager, "_dispatch_sync_blocking"), (
        "_dispatch_sync_blocking 应该被删除,实际还存在"
    )


# ---------------------------------------------------------------------------
# 测 3: task_executor(async=False) 走 ToolResult error 路径
# ---------------------------------------------------------------------------


def test_task_executor_async_false_returns_error_result():
    """LLM 走 task tool 传 async=False → ToolResult(is_error=True)
    反馈给 LLM,不是抛异常也不是静默忽略。
    """
    from baozicode.tools.base import ToolResult
    mgr_ref: dict[str, SubAgentManager | None] = {"mgr": _make_manager()}

    async def _call():
        return await task_executor(
            {
                "type": "definition",
                "role": "explorer",
                "prompt": "x",
                "async": False,  # LLM 显式传 False
            },
            manager_getter=lambda: mgr_ref["mgr"],
        )

    result = asyncio.run(_call())
    assert isinstance(result, ToolResult), f"应返回 ToolResult,得到 {type(result)}"
    assert result.is_error, f"应标记错误,得到 is_error={result.is_error}"
    msg = result.content if isinstance(result.content, str) else str(result.content)
    assert "v1.5" in msg, f"msg 应含 v1.5 提示,实际:{msg!r}"


# ---------------------------------------------------------------------------
# 测 4: _do_dispatch signature 不再有 async_/timeout_seconds
# ---------------------------------------------------------------------------


def test_do_dispatch_signature_no_async_param():
    """_do_dispatch 已删除 async_/timeout_seconds 参数。"""
    import inspect

    sig = inspect.signature(_do_dispatch)
    params = list(sig.parameters.keys())
    assert "async_" not in params, (
        f"_do_dispatch 签名应删除 async_,实际:{params}"
    )
    assert "timeout_seconds" not in params, (
        f"_do_dispatch 签名应删除 timeout_seconds,实际:{params}"
    )