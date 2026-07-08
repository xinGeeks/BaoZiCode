"""多轮对话历史管理。v0.1 仅内存,不持久化。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baozicode.llm.base import Message, TextBlock, ToolResultBlock, ToolUseBlock
from baozicode.tools.base import ToolCall, ToolResult

if TYPE_CHECKING:
    from baozicode.agent.collector import TurnSnapshot


class ConversationManager:
    """维护 `user` / `assistant` / `tool` 消息的有序列表。

    v0.2 新增结构化消息支持:
    - assistant 消息可以含 `TextBlock` + `ToolUseBlock`(模型既吐文字又吐 tool call)
    - tool 消息含 `ToolResultBlock`(喂回工具结果给模型)
    """

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

    def add_message(self, message: Message) -> Message:
        """追加任意 Message(用于聚合 text + 多个 tool_use 的 assistant 消息)。"""
        self._messages.append(message)
        return message

    def add_tool_call(
        self,
        call: ToolCall,
        text_before: str = "",
    ) -> Message:
        """追加 assistant 消息,内容含(text_before)+ ToolUseBlock。

        同一 assistant turn 内若有多个 tool call,可重复追加 blocks,
        但 Message.content 是单 list,需要 caller 自己聚合后调一次。
        本方法只处理单 tool call + 可选前导文本的简单情况。
        """
        blocks: list = []
        if text_before:
            blocks.append(TextBlock(text=text_before))
        blocks.append(
            ToolUseBlock(id=call.id, name=call.name, input=call.arguments)
        )
        msg = Message(role="assistant", content=blocks)
        self._messages.append(msg)
        return msg

    def add_tool_result(self, result: ToolResult) -> Message:
        """追加 tool 消息,内容为 ToolResultBlock。"""
        block = ToolResultBlock(
            tool_use_id=result.tool_call_id,
            content=result.content,
            is_error=result.is_error,
        )
        msg = Message(role="tool", content=[block])
        self._messages.append(msg)
        return msg

    def add_turn(self, snapshot: "TurnSnapshot") -> Message:
        """从 TurnSnapshot 重建 assistant Message 并入库。

        v0.3 Agent 的标准入库路径 —— TurnSnapshot 是 LLM 决策的唯一可信源,
        通过 TurnSnapshot.to_message() 重建,保证 tool_use id/name/arguments
        字节级一致。
        """
        msg = snapshot.to_message()
        self._messages.append(msg)
        return msg

    def clear(self) -> None:
        self._messages.clear()

    def to_list(self) -> list[Message]:
        """返回历史消息的副本。"""
        return list(self._messages)

    def set_messages(self, messages: list[Message]) -> None:
        """v0.7:用新消息列表替换全部历史 — Layer-2 摘要压缩后调用。

        替换前会拷贝入参,防止 caller 之后 mutate 影响内部状态。
        """
        self._messages = list(messages)

    def __len__(self) -> int:
        return len(self._messages)