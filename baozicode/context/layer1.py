"""v0.7 Layer 1 offload engine — 单 block / 单 message 聚合截断。

触发:
- 单 ToolResultBlock.content 字节数 > `per_block_threshold`(默认 8K)→ 写盘
- 单 Message(role="tool").content 里所有 ToolResultBlock 字节合计 > `per_message_threshold`(默认 20K)→ 挑大的依次 offload

preview 格式(头 25 + 尾 25 + 元数据):
    --- preview ({byte_len} bytes) ---
    <first 25 lines>
    ... [N lines / M bytes omitted] ...
    <last 25 lines>
    --- offloaded to: <relpath> ---

idempotent:已 offload 的 block(`offloaded_to is not None`)直接跳过,第二轮 offload 不会重复写盘。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from baozicode.context.schema import ContextConfig
from baozicode.context.storage import ContextStorage
from baozicode.llm.base import (
    ContentBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = ["OffloadEngine", "build_preview"]


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


def _split_head_tail(lines: list[str], n_head: int = 25, n_tail: int = 25) -> tuple[list[str], list[str], int]:
    """把 lines 拆成 head / tail,返回 (head, tail, omitted_count)。

    处理:
    - 短(<= 50 行)→ head = lines, tail = [], omitted = 0
    - 正好 50 行 → head = 前 25, tail = 后 25, omitted = 0(无省略)
    - 长(> 50 行)→ head = 前 25, tail = 后 25, omitted = len - 50
    """
    total = len(lines)
    if total <= 2 * n_head:
        # 短(≤ 50)或正好 50 行:不分头尾
        if total <= n_head:
            return lines, [], 0
        return lines[:n_head], lines[n_head:], 0
    head = lines[:n_head]
    tail = lines[-n_tail:]
    omitted = total - 2 * n_head
    return head, tail, omitted


def build_preview(content: str, relpath: str) -> str:
    """构造 preview 字符串。content 已确定要 offload,byte_len 由调用方计算。"""
    lines = content.splitlines()
    byte_len = _byte_len(content)
    head, tail, omitted = _split_head_tail(lines)
    parts: list[str] = []
    parts.append(f"--- preview ({byte_len} bytes) ---")
    parts.extend(head)
    if omitted > 0:
        # 同时给行数和字节估算(行数准,字节是 head/tail 之外的行总字节)
        omitted_bytes = byte_len - sum(_byte_len(line) for line in head) - sum(_byte_len(line) for line in tail)
        parts.append(f"... [{omitted} lines / {omitted_bytes} bytes omitted] ...")
    parts.extend(tail)
    parts.append(f"--- offloaded to: {relpath} ---")
    return "\n".join(parts)


class OffloadEngine:
    """单次 offload pass:遍历 messages,过大 ToolResultBlock 写盘 + 替换为 preview。"""

    def __init__(self, storage: ContextStorage, config: ContextConfig) -> None:
        self._storage = storage
        self._config = config

    def offload(self, messages: list[Message]) -> list[Message]:
        """返回新的 messages 列表(原列表不变,Block 字段 immutable replace)。

        两步:
        1. 逐 block:超 per_block_threshold → offload + 替换
        2. 逐 message:tool 消息所有 block 字节合计超 per_message_threshold → 挑大的依次 offload
        """
        out: list[Message] = []
        for msg in messages:
            if not _is_tool_message(msg):
                out.append(msg)
                continue
            # Step 1: 逐 block offload
            new_blocks: list[ContentBlock] = []
            for block in _iter_tool_result_blocks(msg):
                new_block = self._offload_single_block(block)
                new_blocks.append(new_block)
            # Step 2: 聚合 offload
            new_blocks = self._aggregate_offload(new_blocks)
            # 用 dataclasses.replace 重建 Message(避免改原对象)
            out.append(replace(msg, content=new_blocks))
        return out

    # ---- internals ----

    def _offload_single_block(
        self, block: ToolResultBlock, *, force: bool = False
    ) -> ToolResultBlock:
        """单 block offload。已 offload 过 → 直接返回(idempotent)。

        force=True → 跳过 per_block_threshold 检查(aggregate path 用)。
        """
        if block.offloaded_to is not None:
            return block
        byte_len = _byte_len(block.content)
        if not force and byte_len <= self._config.per_block_threshold:
            return block
        relpath = self._storage.write_block(
            tool_call_id=block.tool_use_id,
            tool_name="Tool",  # 通用名;实际 tool 名 LLM 上下文已有
            content=block.content,
        )
        preview = build_preview(block.content, str(relpath))
        return replace(
            block,
            content=preview,
            offloaded_to=relpath,
            original_size=byte_len,
        )

    def _aggregate_offload(self, blocks: list[ContentBlock]) -> list[ContentBlock]:
        """per-message 聚合:对仍未 offload 的 tool_result blocks,挑大的依次 offload。"""
        # 只考虑 tool_result blocks
        tool_results = [(i, b) for i, b in enumerate(blocks) if isinstance(b, ToolResultBlock)]
        if len(tool_results) < 2:
            return blocks
        # 计算当前 sum(只看 tool_result bytes)
        def byte_size(b: ToolResultBlock) -> int:
            return b.original_size if b.original_size > 0 else _byte_len(b.content)
        total = sum(byte_size(b) for _, b in tool_results)
        threshold = self._config.per_message_threshold
        if total <= threshold:
            return blocks
        # 按 size desc 排序,挑还没 offload 的,依次 offload 直到 sum ≤ threshold 或只 1 left
        candidates = [(i, b) for i, b in tool_results if b.offloaded_to is None]
        candidates.sort(key=lambda ib: byte_size(ib[1]), reverse=True)
        # 我们要做的是:offload 最大的,重新算 sum,直到 ≤ threshold
        # 但同时保留至少 1 个不 offload(防止 LLM 看不到任何 tool_call_id 关联)
        for idx_in_list, block in candidates:
            if total <= threshold:
                break
            # 至少留 1 个未 offload 的
            remaining_unoffloaded = sum(
                1 for i, b in tool_results if b.offloaded_to is None and i != idx_in_list
            )
            if remaining_unoffloaded == 0:
                # 已经只剩 1 个未 offload(就是要 offload 的这个),停了
                break
            # aggregate 路径强制 offload,忽略 per_block_threshold
            new_block = self._offload_single_block(block, force=True)
            blocks[idx_in_list] = new_block
            total -= byte_size(block)
        return blocks


def _is_tool_message(msg: Message) -> bool:
    """tool 消息(list[ToolResultBlock])是 offload 目标。"""
    return msg.role == "tool" and isinstance(msg.content, list)


def _iter_tool_result_blocks(msg: Message) -> Iterable[ToolResultBlock]:
    if not isinstance(msg.content, list):
        return
    for block in msg.content:
        if isinstance(block, ToolResultBlock):
            yield block
