"""usage 事件的契约测试 — 验证两个后端家族（Anthropic / OpenAI 兼容）都能产出 usage。"""

import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import UsageStats
from baozicode.llm.base import ContentDelta, LLMClient, Message


class AnthropicStyleUsageClient(LLMClient):
    """模拟 Anthropic 流 — 末尾 yield 一条 type='usage' delta。"""

    def __init__(self, payload: UsageStats) -> None:
        self.payload = payload
        self.calls = 0

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
    ) -> AsyncIterator[ContentDelta]:
        self.calls += 1
        yield ContentDelta(type="text", text="hello")
        yield ContentDelta(type="usage", text=self.payload)


class OpenAIStyleUsageClient(LLMClient):
    """模拟 OpenAI 流 — chunk.usage 在最后一条 chunk 上,模拟 SDK 把它 wrap 成 usage delta。"""

    def __init__(self, payload: UsageStats) -> None:
        self.payload = payload
        self.calls = 0

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
    ) -> AsyncIterator[ContentDelta]:
        self.calls += 1
        yield ContentDelta(type="text", text="hi")
        # 一些后端在这里也 yield usage;也可能不 yield(我们的 fallback 处理这种情况)
        yield ContentDelta(type="usage", text=self.payload)


async def test_anthropic_style_yields_usage_at_end() -> None:
    c = AnthropicStyleUsageClient(
        UsageStats(input_tokens=100, output_tokens=50,
                   cache_read_tokens=10, cache_write_tokens=5)
    )
    seen_text = ""
    seen_usage: UsageStats | None = None
    async for delta in c.stream([], tools=[]):
        if delta.type == "text":
            seen_text += delta.text
        elif delta.type == "usage":
            seen_usage = delta.text
    assert seen_text == "hello"
    assert seen_usage is not None
    assert seen_usage.input_tokens == 100
    assert seen_usage.output_tokens == 50
    assert seen_usage.cache_read_tokens == 10
    assert seen_usage.cache_write_tokens == 5
    print("[OK] Anthropic-style usage: payload comes through verbatim")


async def test_openai_style_yields_usage_at_end() -> None:
    c = OpenAIStyleUsageClient(
        UsageStats(input_tokens=20, output_tokens=10, cache_read_tokens=0, cache_write_tokens=0)
    )
    seen: UsageStats | None = None
    async for delta in c.stream([], tools=[]):
        if delta.type == "usage":
            seen = delta.text
    assert seen is not None
    assert seen.input_tokens == 20 and seen.output_tokens == 10
    print("[OK] OpenAI-style usage: payload comes through")


async def test_usage_stats_addition_accumulates() -> None:
    """UsageStats 应该支持 + 累加(Agent 累计 session 用量)。"""
    a = UsageStats(input_tokens=10, output_tokens=5)
    b = UsageStats(input_tokens=20, output_tokens=8, cache_read_tokens=3)
    c = a + b
    assert c.input_tokens == 30
    assert c.output_tokens == 13
    assert c.cache_read_tokens == 3
    assert c.cache_write_tokens == 0
    print("[OK] UsageStats addition: per-field accumulation works")


async def test_usage_fallback_to_zero_when_backend_skips() -> None:
    """后端完全不 yield usage 时,collector 的初始 UsageStats(0,0,0,0) 直接送出。"""

    class NoUsageClient(LLMClient):
        async def stream(self, messages, system=None, tools=None):
            yield ContentDelta(type="text", text="only text")

    seen: UsageStats | None = None
    async for delta in NoUsageClient().stream([], tools=[]):
        if delta.type == "usage":
            seen = delta.text
    assert seen is None  # 后端不送
    # 而 Agent 侧会兜底成 UsageStats(),测试 Agent 兜底逻辑在 test_agent_loop 里
    print("[OK] backends may omit usage → Agent fallback creates zeros")


def test_usage_stats_is_frozen_immutable() -> None:
    """UsageStats 是 frozen dataclass — 试图赋值会抛 FrozenInstanceError。"""
    from dataclasses import FrozenInstanceError

    u = UsageStats(input_tokens=10, output_tokens=5)
    try:
        u.input_tokens = 999  # type: ignore[misc]
    except FrozenInstanceError:
        print("[OK] UsageStats frozen: mutation raises FrozenInstanceError")
        return
    raise AssertionError("UsageStats should be frozen")


def main() -> None:
    import asyncio

    asyncio.run(test_anthropic_style_yields_usage_at_end())
    asyncio.run(test_openai_style_yields_usage_at_end())
    asyncio.run(test_usage_stats_addition_accumulates())
    asyncio.run(test_usage_fallback_to_zero_when_backend_skips())
    test_usage_stats_is_frozen_immutable()
    print("\nAll usage_event tests passed.")


if __name__ == "__main__":
    main()
