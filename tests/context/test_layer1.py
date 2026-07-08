"""v0.7 Layer 1 OffloadEngine 单元测试 — 单 block / 聚合 / idempotent / preview 格式。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.context.layer1 import OffloadEngine, build_preview
from baozicode.context.schema import ContextConfig
from baozicode.context.storage import ContextStorage
from baozicode.llm.base import Message, ToolResultBlock


def _make_tool_message(*blocks: ToolResultBlock) -> Message:
    return Message(role="tool", content=list(blocks))


def test_offload_50k_block_writes_and_sets_metadata(
    context_storage: ContextStorage, context_config: ContextConfig
) -> None:
    """50K Read → offloaded,offloaded_to set,original_size 正确,content=preview。"""
    engine = OffloadEngine(storage=context_storage, config=context_config)
    # 640 行 × 80 字符 = 51_200 字节 + 639 换行 = 51_839 字节(多行内容触发 head/tail 截断)
    big = "\n".join("x" * 80 for _ in range(640))
    expected_size = len(big.encode("utf-8"))  # 51839
    msg = _make_tool_message(ToolResultBlock(tool_use_id="t1", content=big))
    out_msgs = engine.offload([msg])
    assert len(out_msgs) == 1
    out_msg = out_msgs[0]
    block = out_msg.content[0]  # type: ignore[union-attr]
    assert block.offloaded_to is not None
    assert block.original_size == expected_size
    assert f"preview ({expected_size} bytes)" in block.content
    assert "offloaded to:" in block.content
    # 物理文件存在
    full = context_storage.project_root / block.offloaded_to
    assert full.is_file()
    # content 确实被替换为 preview(比原内容短 — 头 25 + 尾 25 + 元数据)
    assert len(block.content.encode("utf-8")) < expected_size


def test_offload_small_block_untouched(
    context_storage: ContextStorage, context_config: ContextConfig
) -> None:
    """4 KB block → 整条不变,offloaded_to = None,original_size = 0。"""
    engine = OffloadEngine(storage=context_storage, config=context_config)
    msg = _make_tool_message(ToolResultBlock(tool_use_id="t1", content="x" * 4096))
    out_msgs = engine.offload([msg])
    out_msg = out_msgs[0]
    block = out_msg.content[0]  # type: ignore[union-attr]
    assert block.offloaded_to is None
    assert block.original_size == 0
    assert block.content == "x" * 4096


def test_offload_two_12k_blocks_in_one_message(
    context_storage: ContextStorage, context_config: ContextConfig
) -> None:
    """两个 12K block(都 > per_block=8K)→ 两者都被 step 1 offload,各占独立文件。"""
    engine = OffloadEngine(storage=context_storage, config=context_config)
    b1 = ToolResultBlock(tool_use_id="t1", content="a" * 12_000)
    b2 = ToolResultBlock(tool_use_id="t2", content="b" * 12_500)  # 略大
    msg = _make_tool_message(b1, b2)
    out_msgs = engine.offload([msg])
    nb1, nb2 = out_msgs[0].content  # type: ignore[misc]
    # step 1 触发 per_block=8K:两者都 > 8192,都 offloaded
    assert nb1.offloaded_to is not None
    assert nb1.original_size == 12_000
    assert nb2.offloaded_to is not None
    assert nb2.original_size == 12_500
    # 两个独立文件
    files = list(context_storage.session_dir.iterdir())
    assert len(files) == 2


def test_offload_three_8k_blocks_in_one_message(
    context_storage: ContextStorage, context_config: ContextConfig
) -> None:
    """3 个 8K block,total 24K,per_message=20K → 最大 offload(sum→16K ≤ 20K)。"""
    engine = OffloadEngine(storage=context_storage, config=context_config)
    b1 = ToolResultBlock(tool_use_id="t1", content="a" * 8_000)
    b2 = ToolResultBlock(tool_use_id="t2", content="b" * 8_000)
    b3 = ToolResultBlock(tool_use_id="t3", content="c" * 8_000)
    msg = _make_tool_message(b1, b2, b3)
    out_msgs = engine.offload([msg])
    blocks = out_msgs[0].content  # type: ignore[union-attr]
    offloaded = [b for b in blocks if b.offloaded_to is not None]
    unoffloaded = [b for b in blocks if b.offloaded_to is None]
    assert len(offloaded) == 1
    assert len(unoffloaded) == 2


def test_offload_idempotent(
    context_storage: ContextStorage, context_config: ContextConfig
) -> None:
    """第二轮 offload 不会再 offload 已 offload 的 block。"""
    engine = OffloadEngine(storage=context_storage, config=context_config)
    big = "x" * 50_000
    msg = _make_tool_message(ToolResultBlock(tool_use_id="t1", content=big))
    out1 = engine.offload([msg])
    out2 = engine.offload(out1)
    # 第二次 offload 后,offloaded_to / original_size 仍跟第一次一样
    b1 = out1[0].content[0]  # type: ignore[union-attr]
    b2 = out2[0].content[0]  # type: ignore[union-attr]
    assert b1.offloaded_to == b2.offloaded_to
    assert b1.original_size == b2.original_size
    # 物理文件数没变(只 1 个,而不是 2 个)
    files = list(context_storage.session_dir.iterdir())
    assert len(files) == 1


def test_build_preview_short_content() -> None:
    """短 content(< 50 行)→ 没省略标记,只有 head。"""
    text = "line1\nline2\nline3"
    prev = build_preview(text, ".baozicode/context/abc/x.json")
    assert "preview (" in prev
    assert "line1" in prev
    assert "line3" in prev
    assert "omitted" not in prev
    assert prev.endswith("--- offloaded to: .baozicode/context/abc/x.json ---")


def test_build_preview_long_content() -> None:
    """长 content(> 50 行)→ 头 25 + 省略标记 + 尾 25。"""
    text = "\n".join(f"line {i}" for i in range(100))
    prev = build_preview(text, ".baozicode/context/abc/x.json")
    assert "line 0" in prev
    assert "line 24" in prev
    assert "line 25" not in prev  # 中间被省
    assert "line 99" in prev
    assert "omitted" in prev


def test_non_tool_message_untouched(
    context_storage: ContextStorage, context_config: ContextConfig
) -> None:
    """user / assistant 消息不会被 offload(offload 只针对 tool_result block)。"""
    engine = OffloadEngine(storage=context_storage, config=context_config)
    user = Message(role="user", content="x" * 50_000)  # 50K 文本
    out_msgs = engine.offload([user])
    # 内容完全不变
    assert out_msgs[0].content == "x" * 50_000
    assert out_msgs[0].role == "user"
