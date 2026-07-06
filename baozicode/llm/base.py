"""LLM 抽象层：消息、增量、客户端接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

Role = Literal["user", "assistant"]
DeltaType = Literal["text", "thinking", "tool_use"]


@dataclass
class Message:
    """一条对话消息。v0.1 只支持纯文本。"""

    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class ContentDelta:
    """流式响应的一个增量块。"""

    type: DeltaType
    text: str
    # v0.2+ 才用：thinking 文本、tool_use 调用信息
    extra: dict | None = None


class LLMClient(ABC):
    """LLM 客户端抽象基类。"""

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
    ) -> AsyncIterator[ContentDelta]:
        """流式生成回答。`system` 是不属于 messages 的全局系统提示。"""
        if False:  # pragma: no cover - 强制声明为 generator
            yield ContentDelta(type="text", text="")
