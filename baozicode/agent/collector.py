"""StreamCollector — 流式收集器,Agent 决策的唯一可信源(D 决策)。

设计要点:
- 接受 LLM 流出的 ContentDelta,实时 yield text 块给 TUI
- 内部累加完整 TurnSnapshot(text + tool_calls + blocks)
- 一轮结束,Agent 用 TurnSnapshot.to_message() 重建 assistant message

`text` 字段是 `text_blocks` 拼接后的字符串视图。Agent 不直接用 text,
总是从 blocks 重建 message,保证 tool_use id/name/arguments 不丢。
"""

from __future__ import annotations

from typing import AsyncIterator

from baozicode.llm.base import (
    ContentDelta,
    Message,
    TextBlock,
    ToolUseBlock,
)
from baozicode.tools.base import ToolCall


class TurnSnapshot:
    """一轮 LLM 响应的完整快照。

    `text` 是纯文本拼接;`tool_calls` 是 ListCall 列表(LLM 原始决策);
    `text_blocks` 和 `tool_use_blocks` 是按 LLM 输出顺序排列的 ContentBlock,
    用于精确重建 assistant message。
    """

    def __init__(
        self,
        text: str = "",
        tool_calls: list[ToolCall] | None = None,
        text_blocks: list[TextBlock] | None = None,
        tool_use_blocks: list[ToolUseBlock] | None = None,
    ) -> None:
        self.text = text
        self.tool_calls = tool_calls or []
        self.text_blocks = text_blocks or []
        self.tool_use_blocks = tool_use_blocks or []

    def to_message(self) -> Message:
        """从完整 snapshot 重建 assistant Message,保证 tool_use id 字节级一致。"""
        if not self.text_blocks and not self.tool_use_blocks:
            # 没产出任何块 → 退化到纯文本路径(v0.1 行为,保留快速路径)
            return Message(role="assistant", content=self.text)

        blocks: list = []
        # text_blocks 和 tool_use_blocks 内部已经按到达顺序排好,
        # 但有些后端会把同一 text 块切碎,合并连续 text 减少块数
        for blk in self.text_blocks:
            if (
                blocks
                and isinstance(blocks[-1], TextBlock)
                and isinstance(blk, TextBlock)
            ):
                blocks[-1] = TextBlock(text=blocks[-1].text + blk.text)
            else:
                blocks.append(blk)
        blocks.extend(self.tool_use_blocks)
        return Message(role="assistant", content=blocks)

    def __repr__(self) -> str:
        return (
            f"TurnSnapshot(text_len={len(self.text)}, "
            f"tool_calls={len(self.tool_calls)}, "
            f"text_blocks={len(self.text_blocks)}, "
            f"tool_use_blocks={len(self.tool_use_blocks)})"
        )


class StreamCollector:
    """流式收集器 — 吸收 LLM ContentDelta,实时 yield text,内部累加 TurnSnapshot。

    用法:
        collector = StreamCollector()
        async for delta in llm.stream(...):
            async for chunk in collector.absorb(delta):
                ...  # 推给 TUI(text 块)
        turn = collector.snapshot()  # 完整结构
        msg = turn.to_message()       # 重建 assistant message
    """

    def __init__(self) -> None:
        self._text: str = ""
        self._tool_calls: list[ToolCall] = []
        self._text_blocks: list[TextBlock] = []
        self._tool_use_blocks: list[ToolUseBlock] = []

    async def absorb(self, delta: ContentDelta) -> AsyncIterator[str]:
        """吸收一个 ContentDelta,实时 yield text 块,内部累加完整结构。"""
        if delta.type == "text":
            chunk = delta.text or ""
            self._text += chunk
            self._text_blocks.append(TextBlock(text=chunk))
            if chunk:
                yield chunk
        elif delta.type == "tool_use":
            # delta.text 是 ToolCall 实例
            call: ToolCall = delta.text
            self._tool_calls.append(call)
            self._tool_use_blocks.append(
                ToolUseBlock(id=call.id, name=call.name, input=call.arguments)
            )
        # usage / thinking / 其它 type:不 emit text,只让 collector 跳过
        # usage 由 Agent 在流结束后单独 yield,不在 collector 里处理

    def snapshot(self) -> TurnSnapshot:
        """产出本轮完整快照。Agent 调此方法拿到 TurnSnapshot 后重建 message。"""
        return TurnSnapshot(
            text=self._text,
            tool_calls=list(self._tool_calls),
            text_blocks=list(self._text_blocks),
            tool_use_blocks=list(self._tool_use_blocks),
        )


__all__ = ["StreamCollector", "TurnSnapshot"]
