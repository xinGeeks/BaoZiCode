"""v0.7 启发式 token 估算器 — 无 tiktoken 依赖。

设计目标:误差 ±20% 即可,13K 余量兜底。纯 Python,启动快,零外部依赖。

估算规则(每条 message):
- 4 tokens role overhead
- 每个 content block 3 tokens
- TextBlock:
    CJK ratio > 0.3 → `len(text) * 3 // 5`(中文 1 字 ≈ 0.6 token)
    否则           → `len(text) // 3`(英文 1 char ≈ 0.33 token)
- ToolUseBlock:
    + `len(json.dumps(input)) // 3`
- ToolResultBlock:
    用 `original_size`(bytes) if > 0,否则 `len(content.encode("utf-8"))` // 3
    `original_size` 是 v0.7 offload 后保留的真实大小,比 preview 字符串准确
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Union

from baozicode.llm.base import (
    ContentBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = [
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "cjk_ratio",
]

# 启发常数
_ROLE_OVERHEAD = 4
_BLOCK_OVERHEAD = 3
_ASCII_RATIO = 3  # 1 char ≈ 1/3 token
_CJK_NUM = 3
_CJK_DEN = 5  # 1 char ≈ 3/5 token
_CJK_THRESHOLD = 0.3


def cjk_ratio(text: str) -> float:
    """计算 CJK 字符占比。

    CJK 范围:0x4E00-0x9FFF(基本汉字)+ 0x3000-0x303F(CJK 符号)+ 0xFF00-0xFFEF(全角)。
    """
    if not text:
        return 0.0
    cjk_count = sum(
        1
        for ch in text
        if (
            0x4E00 <= ord(ch) <= 0x9FFF
            or 0x3000 <= ord(ch) <= 0x303F
            or 0xFF00 <= ord(ch) <= 0xFFEF
        )
    )
    return cjk_count / len(text)


def _text_block_tokens(block: TextBlock) -> int:
    text = block.text
    if cjk_ratio(text) > _CJK_THRESHOLD:
        return len(text) * _CJK_NUM // _CJK_DEN
    return len(text) // _ASCII_RATIO


def _tool_use_block_tokens(block: ToolUseBlock) -> int:
    return len(json.dumps(block.input, ensure_ascii=False)) // _ASCII_RATIO


def _tool_result_block_tokens(block: ToolResultBlock) -> int:
    # v0.7:已 offload 的 block 用 original_size,否则用 content 字节数
    if block.original_size > 0:
        return block.original_size // _ASCII_RATIO
    return len(block.content.encode("utf-8")) // _ASCII_RATIO


def _block_tokens(block: ContentBlock) -> int:
    if isinstance(block, TextBlock):
        return _text_block_tokens(block)
    if isinstance(block, ToolUseBlock):
        return _tool_use_block_tokens(block)
    if isinstance(block, ToolResultBlock):
        return _tool_result_block_tokens(block)
    return 0


def estimate_message_tokens(message: Message) -> int:
    """估算单条 message 的 token 数。"""
    total = _ROLE_OVERHEAD
    content = message.content
    if isinstance(content, str):
        # str content:走 text block 同一启发式
        if cjk_ratio(content) > _CJK_THRESHOLD:
            total += len(content) * _CJK_NUM // _CJK_DEN
        else:
            total += len(content) // _ASCII_RATIO
        return total
    # list[ContentBlock]
    for block in content:
        total += _BLOCK_OVERHEAD
        total += _block_tokens(block)
    return total


def estimate_messages_tokens(messages: Sequence[Message]) -> int:
    """估算整个 messages 列表的 token 数。"""
    return sum(estimate_message_tokens(m) for m in messages)
