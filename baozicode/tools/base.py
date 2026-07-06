"""工具调用统一数据模型。

`ToolDefinition / ToolCall / ToolResult` 三个 dataclass 是后端、工具实现、TUI 共用的内部契约。
后端 SDK 类型不许泄漏出 `baozicode/llm/`。
"""

from __future__ import annotations

import locale
from dataclasses import dataclass, field
from typing import Any, Literal


Risk = Literal["low", "high"]


def decode_subprocess_output(data: bytes) -> str:
    """智能解码子进程输出。

    Windows 中文系统上 cmd/PowerShell/部分 rg 编译版默认 GBK,
    直接 UTF-8 解会乱码。本函数先 UTF-8 严格解码,失败回落到
    系统偏好编码,再不行用 cp936 兜底,最后 errors="replace"。
    """
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode(locale.getpreferredencoding(False))
    except (UnicodeDecodeError, LookupError):
        pass
    try:
        return data.decode("cp936")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


@dataclass
class ToolDefinition:
    """工具的静态描述，喂给 LLM 让它知道这个工具能干嘛。"""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    risk: Risk = "low"

    def to_anthropic(self) -> dict[str, Any]:
        """转换为 Anthropic SDK 的 tool 格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_openai(self) -> dict[str, Any]:
        """转换为 OpenAI 兼容的 function calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolCall:
    """LLM 在流中发起的工具调用请求。"""

    id: str
    name: str
    arguments: dict[str, Any]
    error: str | None = None  # 流式累积时 JSON 解析失败的诊断信息


@dataclass
class ToolResult:
    """工具执行结果，喂回给 LLM。"""

    tool_call_id: str
    content: str
    is_error: bool = False

    @classmethod
    def error_result(cls, tool_call_id: str, message: str) -> "ToolResult":
        return cls(tool_call_id=tool_call_id, content=message, is_error=True)

    @classmethod
    def success(cls, tool_call_id: str, content: str) -> "ToolResult":
        return cls(tool_call_id=tool_call_id, content=content, is_error=False)


__all__ = ["Risk", "ToolDefinition", "ToolCall", "ToolResult"]