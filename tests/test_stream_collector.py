"""StreamCollector + TurnSnapshot 的双路收集行为测试。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.collector import StreamCollector, TurnSnapshot
from baozicode.llm.base import ContentDelta
from baozicode.tools.base import ToolCall


async def _drain_text(collector: StreamCollector, delta: ContentDelta) -> list[str]:
    """收集 collector.absorb 产生的所有 text chunk。"""
    chunks: list[str] = []
    async for chunk in collector.absorb(delta):
        chunks.append(chunk)
    return chunks


async def test_text_chunks_stream_live() -> None:
    """text delta 一字一字 yield 给 UI(TUI 的 Markdown stream.write 拿这些)。"""
    c = StreamCollector()
    out = []
    out.extend(await _drain_text(c, ContentDelta(type="text", text="Hello")))
    out.extend(await _drain_text(c, ContentDelta(type="text", text=" ")))
    out.extend(await _drain_text(c, ContentDelta(type="text", text="world")))
    assert out == ["Hello", " ", "world"]
    print("[OK] text chunks: yielded live, no buffering")


async def test_text_chunks_accumulate_into_snapshot() -> None:
    """text delta 同时累积到 snapshot.text(供 Agent 决策用)。"""
    c = StreamCollector()
    await _drain_text(c, ContentDelta(type="text", text="Hello"))
    await _drain_text(c, ContentDelta(type="text", text=" "))
    await _drain_text(c, ContentDelta(type="text", text="world"))
    snap = c.snapshot()
    assert snap.text == "Hello world"
    print("[OK] text chunks accumulate into snapshot.text")


async def test_empty_text_chunk_yields_nothing() -> None:
    """空字符串 text delta 不应 yield(避免界面渲染空刷新)。"""
    c = StreamCollector()
    chunks = await _drain_text(c, ContentDelta(type="text", text=""))
    assert chunks == []
    print("[OK] empty text chunk yields nothing")


async def test_tool_use_yields_no_text_but_accumulates_in_snapshot() -> None:
    """tool_use delta 不应给 TUI 推 text 文本;只进入 snapshot 的 tool_calls 列表。"""
    c = StreamCollector()
    call = ToolCall(id="t1", name="Read", arguments={"file_path": "/x"})
    chunks = await _drain_text(c, ContentDelta(type="tool_use", text=call))
    assert chunks == []
    snap = c.snapshot()
    assert len(snap.tool_calls) == 1
    assert snap.tool_calls[0].name == "Read"
    print("[OK] tool_use: no text yielded, captured in snapshot.tool_calls")


async def test_multiple_tool_calls_preserve_order() -> None:
    c = StreamCollector()
    for i in range(3):
        await _drain_text(
            c, ContentDelta(type="tool_use", text=ToolCall(id=f"id{i}", name="Read", arguments={"i": i}))
        )
    snap = c.snapshot()
    assert [t.id for t in snap.tool_calls] == ["id0", "id1", "id2"]
    print("[OK] tool_use deltas preserve LLM order in snapshot")


async def test_snapshot_to_message_round_trip_text_only() -> None:
    """仅 text 的 snapshot.to_message() 仍走结构化路径(content=list,含 1 个 TextBlock)。

    设计取舍:即便不需要 tool_use 也走 blocks 路径,换得 Anthropic ↔ OpenAI
    一致的 message 形态,TUI 上层不必分支。
    """
    c = StreamCollector()
    await _drain_text(c, ContentDelta(type="text", text="answer"))
    snap = c.snapshot()
    msg = snap.to_message()
    assert msg.role == "assistant"
    assert isinstance(msg.content, list)
    assert len(msg.content) == 1
    assert msg.content[0].type == "text"
    assert msg.content[0].text == "answer"
    print("[OK] snapshot.to_message() (text only) → list[1 TextBlock]")


async def test_snapshot_to_message_round_trip_structured() -> None:
    """含 tool_use 的 snapshot 重建为结构化 Message,blocks 含 ToolUseBlock + 之前的 TextBlock。"""
    c = StreamCollector()
    await _drain_text(c, ContentDelta(type="text", text="thinking "))
    await _drain_text(c, ContentDelta(type="text", text="out loud"))
    await _drain_text(
        c,
        ContentDelta(
            type="tool_use",
            text=ToolCall(id="t1", name="Read", arguments={"file_path": "/x"}),
        ),
    )
    snap = c.snapshot()
    msg = snap.to_message()
    assert msg.role == "assistant"
    # content is list[ContentBlock]
    assert isinstance(msg.content, list)
    assert len(msg.content) == 2  # 1 TextBlock + 1 ToolUseBlock
    assert msg.content[0].type == "text"
    assert msg.content[0].text == "thinking out loud"
    assert msg.content[1].type == "tool_use"
    assert msg.content[1].id == "t1"
    assert msg.content[1].name == "Read"
    assert msg.content[1].input == {"file_path": "/x"}
    print("[OK] snapshot.to_message() preserves text + tool_use blocks")


async def test_snapshot_to_message_id_argument_byte_exact() -> None:
    """LLM tool_use id/name/arguments 经过 to_message 后必须字节一致(模型后续引用要靠 id)。"""
    c = StreamCollector()
    args = {"file_path": "/path/with spaces", "offset": 42, "flag": True}
    await _drain_text(
        c,
        ContentDelta(type="tool_use", text=ToolCall(id="abc-123", name="Read", arguments=args)),
    )
    snap = c.snapshot()
    blocks = snap.to_message().content
    assert isinstance(blocks, list)
    block = blocks[0]
    assert block.id == "abc-123"
    assert block.name == "Read"
    assert block.input == args  # dict equality
    print("[OK] snapshot.to_message() preserves tool id/name/arguments")


async def main() -> None:
    await test_text_chunks_stream_live()
    await test_text_chunks_accumulate_into_snapshot()
    await test_empty_text_chunk_yields_nothing()
    await test_tool_use_yields_no_text_but_accumulates_in_snapshot()
    await test_multiple_tool_calls_preserve_order()
    await test_snapshot_to_message_round_trip_text_only()
    await test_snapshot_to_message_round_trip_structured()
    await test_snapshot_to_message_id_argument_byte_exact()
    print("\nAll stream_collector tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
