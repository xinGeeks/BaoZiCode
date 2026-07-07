"""v0.4 Phase 3 — Cache strategy: BuiltPrompt.cache_breakpoints + UsageStats accumulation +
Agent forwards cache_breakpoints to llm.stream.

3 tests:
1. BuiltPrompt.cache_breakpoints has system_start + after_tools
2. UsageStats accumulates cache_read/cache_write fields
3. Agent.run forwards cache_breakpoints to llm.stream
"""

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import UsageStats
from baozicode.agent.loop import Agent
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.prompt.builder import PromptBuilder
from baozicode.prompt.types import CacheBreakpoint
from baozicode.tools.base import ToolCall
from baozicode.tools.registry import get_all_tools

sys.path.insert(0, str(Path(__file__).parent))
from _agent_helpers import make_minimal_config


# ---- 1: BuiltPrompt.cache_breakpoints shape ----


def test_built_prompt_cache_breakpoints_shape() -> None:
    """BuiltPrompt.cache_breakpoints is a list of CacheBreakpoint, length >= 2,
    contains system_start (priority >= 90) and after_tools.
    """
    cfg = make_minimal_config()
    tools = get_all_tools()
    bp = PromptBuilder().build(cfg, plan_mode=False, tools=tools)
    bps = bp.cache_breakpoints
    assert isinstance(bps, list)
    assert len(bps) >= 2, f"expected >= 2 breakpoints, got {len(bps)}"
    for item in bps:
        assert isinstance(item, CacheBreakpoint)
    sys_start = [b for b in bps if b.location == "system_start"]
    after_tools = [b for b in bps if b.location == "after_tools"]
    assert sys_start, "must include a CacheBreakpoint('system_start', ...)"
    assert sys_start[0].priority >= 90, f"system_start priority should be >= 90, got {sys_start[0].priority}"
    assert after_tools, "must include a CacheBreakpoint('after_tools', ...)"
    print("[OK] BuiltPrompt.cache_breakpoints: system_start + after_tools present (len >= 2)")


# ---- 2: UsageStats addition preserves cache fields ----


def test_usage_stats_accumulates_cache_fields() -> None:
    """UsageStats + UsageStats sums input/output/cache_read/cache_write fields."""
    a = UsageStats(input_tokens=10, output_tokens=5)
    b = UsageStats(input_tokens=20, output_tokens=7, cache_read_tokens=15, cache_write_tokens=4)
    combined = a + b
    assert combined == UsageStats(
        input_tokens=30,
        output_tokens=12,
        cache_read_tokens=15,
        cache_write_tokens=4,
    )
    print("[OK] UsageStats addition sums cache_read + cache_write fields")


# ---- 3: Agent.run forwards cache_breakpoints to llm.stream ----


class _FakeLLM(LLMClient):
    """Record every stream() call's kwargs and yield a single text delta so Agent completes."""

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
        self.call_kwargs.append(
            {
                "messages": list(messages),
                "system": system,
                "tools": list(tools) if tools else [],
                "cache_breakpoints": cache_breakpoints,
            }
        )
        yield ContentDelta(type="text", text="done")
        yield ContentDelta(type="usage", text=UsageStats())


def _perm():
    class _P:
        deny: list[str] = []
        auto_allow: list[str] = []

    return _P()


async def test_agent_forwards_cache_breakpoints_to_llm_stream() -> None:
    """Agent.run calls llm.stream with cache_breakpoints == self._prompt.cache_breakpoints."""
    cfg = make_minimal_config()
    llm = _FakeLLM()
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_perm(),
        config=cfg,
    )
    async for _ in a.run("hello"):
        pass
    assert len(llm.call_kwargs) >= 1, "LLM should have been called at least once"
    first = llm.call_kwargs[0]
    assert "cache_breakpoints" in first, "cache_breakpoints kwarg must be passed to llm.stream"
    passed = first["cache_breakpoints"]
    assert passed == a._prompt.cache_breakpoints, (
        f"forwarded cache_breakpoints should equal self._prompt.cache_breakpoints; "
        f"got {passed!r} vs {a._prompt.cache_breakpoints!r}"
    )
    # sanity: should include the system_start + after_tools markers
    locations = [bp.location for bp in passed]
    assert "system_start" in locations
    assert "after_tools" in locations
    print("[OK] Agent.run forwards cache_breakpoints == self._prompt.cache_breakpoints")


def main() -> None:
    test_built_prompt_cache_breakpoints_shape()
    test_usage_stats_accumulates_cache_fields()
    asyncio.run(test_agent_forwards_cache_breakpoints_to_llm_stream())
    print("\nAll cache_strategy tests passed.")


if __name__ == "__main__":
    main()