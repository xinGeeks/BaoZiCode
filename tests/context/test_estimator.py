"""v0.7 估算器单元测试 — 中英文 / ToolUseBlock / mixed / ToolResultBlock original_size。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.context.estimator import (
    cjk_ratio,
    estimate_message_tokens,
    estimate_messages_tokens,
)
from baozicode.llm.base import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def test_cjk_ratio_empty() -> None:
    assert cjk_ratio("") == 0.0


def test_cjk_ratio_ascii_only() -> None:
    assert cjk_ratio("hello world") == 0.0


def test_cjk_ratio_mixed_high_cjk() -> None:
    text = "你好世界 hello"  # 4 CJK + 5 ASCII (incl space), ratio = 4/9 ≈ 0.44
    assert cjk_ratio(text) > 0.3


def test_cjk_ratio_mixed_low_cjk() -> None:
    text = "hello world 你"  # 1 CJK / 12 = 0.08
    assert cjk_ratio(text) < 0.3


def test_estimate_english_user_message() -> None:
    """300 ASCII chars → ~100 + role overhead (4) = 104."""
    text = "a" * 300
    msg = Message(role="user", content=text)
    tokens = estimate_message_tokens(msg)
    # 4 (role) + 100 (text)
    assert 100 <= tokens <= 110, f"got {tokens}"


def test_estimate_chinese_user_message() -> None:
    """300 CJK chars → ~180 (300*3//5) + role overhead."""
    text = "你" * 300
    msg = Message(role="user", content=text)
    tokens = estimate_message_tokens(msg)
    # 4 (role) + 180 (text)
    assert 175 <= tokens <= 190, f"got {tokens}"


def test_estimate_tool_use_block_includes_input_json() -> None:
    """ToolUseBlock with 600-char input → ~200 (600//3) + block overhead (3)."""
    call = ToolUseBlock(id="t1", name="Read", input={"file_path": "x" * 600})
    msg = Message(role="assistant", content=[call])
    tokens = estimate_message_tokens(msg)
    # 4 (role) + 3 (block) + 200 (input) = 207
    assert tokens >= 200, f"got {tokens}"


def test_estimate_tool_result_uses_original_size() -> None:
    """ToolResultBlock with original_size=51200 uses original size not preview."""
    block = ToolResultBlock(
        tool_use_id="t1",
        content="--- preview (51200 bytes) ---\n<first 25 lines>...\n--- offloaded to: ... ---",
        original_size=51_200,
    )
    msg = Message(role="tool", content=[block])
    tokens = estimate_message_tokens(msg)
    # 4 (role) + 3 (block) + 51200//3 (17066) ≈ 17073
    assert tokens >= 17_000, f"got {tokens}, expected ~17000+ from original_size"


def test_estimate_tool_result_falls_back_to_content_size() -> None:
    """Without original_size, uses content byte length."""
    block = ToolResultBlock(tool_use_id="t1", content="x" * 600, original_size=0)
    msg = Message(role="tool", content=[block])
    tokens = estimate_message_tokens(msg)
    # 4 + 3 + 200 = 207
    assert 200 <= tokens <= 215, f"got {tokens}"


def test_estimate_mixed_blocks_sums_correctly() -> None:
    """Multi-block message: role + sum(3 + per-block) without double counting."""
    msg = Message(
        role="assistant",
        content=[
            TextBlock(text="hi"),  # 3 (block) + 1 (text) = 4
            ToolUseBlock(id="t1", name="X", input={"k": "v" * 30}),  # 3 + 10 = 13
        ],
    )
    tokens = estimate_message_tokens(msg)
    # 4 (role) + 4 + 13 = 21
    assert 20 <= tokens <= 25, f"got {tokens}"


def test_estimate_messages_sums_all() -> None:
    """estimate_messages_tokens is sum of per-message estimates."""
    m1 = Message(role="user", content="a" * 30)
    m2 = Message(role="assistant", content="b" * 60)
    m3 = Message(
        role="tool",
        content=[ToolResultBlock(tool_use_id="t1", content="x" * 90)],
    )
    total = estimate_messages_tokens([m1, m2, m3])
    expected = (
        estimate_message_tokens(m1)
        + estimate_message_tokens(m2)
        + estimate_message_tokens(m3)
    )
    assert total == expected
