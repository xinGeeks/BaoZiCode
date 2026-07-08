"""v0.7 ToolResultBlock offload metadata fields — default + round-trip."""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.llm.base import Message, ToolResultBlock


def test_tool_result_block_defaults_to_no_offload() -> None:
    """Bare `ToolResultBlock(content='abc')` → offloaded_to=None, original_size=0."""
    block = ToolResultBlock(content="abc")
    assert block.content == "abc"
    assert block.offloaded_to is None
    assert block.original_size == 0
    assert block.is_error is False
    assert block.tool_use_id == ""
    print("[OK] ToolResultBlock() defaults: offloaded_to=None, original_size=0")


def test_tool_result_block_with_offload_metadata() -> None:
    """Construct with offloaded_to + original_size; both fields stick."""
    block = ToolResultBlock(
        tool_use_id="t1",
        content="--- preview ... ---",
        is_error=False,
        offloaded_to=Path(".baozicode/context/abc/Read_a1b2c3d4_0.json"),
        original_size=51_200,
    )
    assert block.offloaded_to == Path(".baozicode/context/abc/Read_a1b2c3d4_0.json")
    assert block.original_size == 51_200
    print("[OK] ToolResultBlock with offload metadata: fields persist")


def test_tool_result_block_asdict_round_trip() -> None:
    """dataclasses.asdict round-trips offload fields without losing them."""
    block = ToolResultBlock(
        tool_use_id="t1",
        content="preview",
        offloaded_to=Path(".baozicode/context/abc/x.json"),
        original_size=12345,
    )
    d = asdict(block)
    assert d["offloaded_to"] == Path(".baozicode/context/abc/x.json")
    assert d["original_size"] == 12345
    print("[OK] asdict() preserves offloaded_to and original_size")


def test_message_to_dict_does_not_emit_offload_fields() -> None:
    """Message.to_dict() must NOT include offloaded_to / original_size in API payload."""
    msg = Message(
        role="tool",
        content=[
            ToolResultBlock(
                tool_use_id="t1",
                content="preview",
                offloaded_to=Path(".baozicode/context/abc/x.json"),
                original_size=12345,
            )
        ],
    )
    d = msg.to_dict()
    block_dict = d["content"][0]
    assert block_dict["type"] == "tool_result"
    assert block_dict["tool_use_id"] == "t1"
    assert block_dict["content"] == "preview"
    assert block_dict["is_error"] is False
    # The offload fields must NOT leak into the LLM-facing payload
    assert "offloaded_to" not in block_dict
    assert "original_size" not in block_dict
    print("[OK] Message.to_dict() strips offload fields (LLM sees only 3 legacy fields)")
