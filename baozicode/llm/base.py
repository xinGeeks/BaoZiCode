"""LLM 抽象层：消息、内容块、增量、客户端接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Union

from baozicode.tools.base import ToolDefinition


Role = Literal["user", "assistant", "system", "tool"]
DeltaType = Literal["text", "thinking", "tool_use", "tool_result", "usage"]


# --- ContentBlock: discriminated union over text / tool_use / tool_result ---


@dataclass
class TextBlock:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ToolUseBlock:
    """Anthropic 风格：在 assistant message 里记录 LLM 调用的工具。"""

    type: Literal["tool_use"] = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    """Anthropic 风格：在 tool message 里记录工具结果。

    v0.7 新增 `offloaded_to` + `original_size`(默认 `None` / `0`):
    - `offloaded_to`:v0.7 Layer-1 offload 把超大 content 写盘时,记录相对
      项目根的 offload 文件路径(例如 `.baozicode/context/<sess>/<block>.json`)。
      LLM API 序列化时不带此字段(只发 `tool_use_id` / `content` / `is_error`)。
    - `original_size`:原 content 的 UTF-8 字节长度;offload 后 `content` 是
      preview 字符串(几百~几千字节),`original_size` 仍是原始大小,token
      估算器用它做精确估算。

    字段默认 `None` / `0` 是 v0.6 行为,所有现有调用方零修改。
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str = ""
    content: str = ""
    is_error: bool = False
    offloaded_to: Path | None = None
    original_size: int = 0


ContentBlock = Union[TextBlock, ToolUseBlock, ToolResultBlock]


# --- Message: content 既可以是 str（快速路径）也可以是 list[ContentBlock] ---


@dataclass
class Message:
    """一条对话消息。

    `content` 是联合类型：
    - `str`：纯文本消息（v0.1 路径，user / assistant 的绝大多数场景）
    - `list[ContentBlock]`：含 tool_use 或 tool_result 的结构化消息（v0.2+）
    """

    role: Role
    content: Union[str, list[ContentBlock]]

    def to_dict(self) -> dict[str, Any]:
        """序列化为通用 dict；具体后端再做协议层转换。"""
        if isinstance(self.content, str):
            return {"role": self.role, "content": self.content}
        blocks: list[dict[str, Any]] = []
        for block in self.content:
            if isinstance(block, TextBlock):
                blocks.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                blocks.append(
                    {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
                )
            elif isinstance(block, ToolResultBlock):
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": block.content,
                        "is_error": block.is_error,
                    }
                )
        return {"role": self.role, "content": blocks}


@dataclass
class ContentDelta:
    """流式响应的一个增量块。

    - `type="text"` + `text=...`：普通文本 token
    - `type="tool_use"` + `text=ToolCall(...)`：一个完整的 tool call（已经过 JSON 解析）
    - `type="tool_result"`：极少使用（一般 tool result 由客户端喂回，不通过 stream）
    - `type="usage"` + `text=UsageStats(...)`：流末尾的 token 用量统计（v0.3 新增）
    """

    type: DeltaType
    text: Union[str, Any]  # str for text/tool_result, ToolCall for tool_use
    extra: dict | None = None


class LLMClient(ABC):
    """LLM 客户端抽象基类。"""

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        *,
        cache_breakpoints: list | None = None,
    ) -> AsyncIterator[ContentDelta]:
        """流式生成回答。

        - `system`: 不属于 messages 的全局系统提示（v0.1 沿用）
        - `tools`: v0.2 新增；None 或 [] 表示不传 tools 参数给 SDK（保留 v0.1 行为）
        - `cache_breakpoints`: v0.4 新增；预期传 `list[CacheBreakpoint]`
          （来自 `baozicode.prompt.types.CacheBreakpoint`）。这里故意只标 `list`
          而非 `list[CacheBreakpoint]`，以避免 `baozicode.llm.base` 与
          `baozicode.prompt.types` 互相 import 形成循环（prompt 包本身依赖
          `baozicode.llm.base.Message`）。后端在 v0.4 接受此参数但忽略其值；
          v0.5+ 会把断点翻译成各家 SDK 的 cache_control 标记。
        """
        if False:  # pragma: no cover - 强制声明为 generator
            yield ContentDelta(type="text", text="")


__all__ = [
    "Role",
    "DeltaType",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ContentBlock",
    "Message",
    "ContentDelta",
    "LLMClient",
]