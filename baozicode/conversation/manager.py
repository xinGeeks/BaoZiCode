"""多轮对话历史管理。v0.1 仅内存，不持久化。"""

from __future__ import annotations

from baozicode.llm.base import Message


class ConversationManager:
    """维护 `user` / `assistant` 消息的有序列表。"""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add_user(self, text: str) -> Message:
        msg = Message(role="user", content=text)
        self._messages.append(msg)
        return msg

    def add_assistant(self, text: str) -> Message:
        msg = Message(role="assistant", content=text)
        self._messages.append(msg)
        return msg

    def clear(self) -> None:
        self._messages.clear()

    def to_list(self) -> list[Message]:
        """返回历史消息的副本。"""
        return list(self._messages)

    def __len__(self) -> int:
        return len(self._messages)
