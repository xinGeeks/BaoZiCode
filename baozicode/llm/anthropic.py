"""Anthropic 后端实现,包装官方 anthropic-sdk-python。

支持 v0.2 工具调用:
- `tools` 参数转换为 SDK 的 `tools=[{name, description, input_schema}]`
- 流式累积 `tool_use` block:`content_block_start` + 多次 `input_json_delta`
  + `content_block_stop` 时 yield 一个完整的 `ContentDelta(tool_use, ToolCall)`
- `input_json_delta` JSON 解析失败 → yield error-marked ToolCall,不传播异常
- `Message(content=list[ContentBlock])` 转换为 SDK 风格 block 列表
- `role="tool"` 消息 → SDK 期望的 `role="user"` + tool_result blocks
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

from baozicode.agent.events import UsageStats
from baozicode.llm.base import (
    ContentDelta,
    LLMClient,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from baozicode.tools.base import ToolDefinition, ToolCall


def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """把内部 `Message` 转成 Anthropic SDK 期望的 dict 列表。

    注意:
    - role="tool" → role="user"(Anthropic 没有 role="tool",tool_result 走 user 消息)
    - role="system" 不进 messages(system 参数单独传)
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        if isinstance(m.content, str):
            out.append({"role": m.role, "content": m.content})
            continue
        blocks: list[dict[str, Any]] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                blocks.append({"type": "text", "text": b.text})
            elif isinstance(b, ToolUseBlock):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": b.input,
                    }
                )
            elif isinstance(b, ToolResultBlock):
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": b.tool_use_id,
                        "content": b.content,
                        "is_error": b.is_error,
                    }
                )
        role = "user" if m.role == "tool" else m.role
        out.append({"role": role, "content": blocks})
    return out


class AnthropicBackend(LLMClient):
    """Anthropic Claude API 的流式客户端。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**client_kwargs)
        self._model = model

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        *,
        cache_breakpoints: list | None = None,
    ) -> AsyncIterator[ContentDelta]:
        # cache_breakpoints is accepted but ignored in v0.4; v0.5+ will translate to SDK cache_control
        sdk_messages = _convert_messages(messages)

        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 8192,
            "system": system or "",
            "messages": sdk_messages,
        }
        if tools:
            params["tools"] = [t.to_anthropic() for t in tools]

        current_tool: dict[str, Any] | None = None

        async with self._client.messages.stream(**params) as stream:
            async for event in stream:
                et = getattr(event, "type", None)
                if et == "content_block_start":
                    cb = getattr(event, "content_block", None)
                    if cb is not None and getattr(cb, "type", None) == "tool_use":
                        current_tool = {
                            "id": getattr(cb, "id", "") or "",
                            "name": getattr(cb, "name", "") or "",
                            "partial_json": "",
                        }
                elif et == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta is None:
                        continue
                    dtype = getattr(delta, "type", None)
                    if dtype == "text_delta":
                        text = getattr(delta, "text", None)
                        if text:
                            yield ContentDelta(type="text", text=text)
                    elif dtype == "input_json_delta" and current_tool is not None:
                        piece = getattr(delta, "partial_json", None) or ""
                        current_tool["partial_json"] += piece
                elif et == "content_block_stop":
                    if current_tool is not None:
                        raw = current_tool["partial_json"]
                        try:
                            args = json.loads(raw) if raw.strip() else {}
                            err: str | None = None
                        except json.JSONDecodeError as exc:
                            args = {}
                            err = (
                                f"failed to parse tool arguments JSON: {exc}; "
                                f"raw={raw[:200]!r}"
                            )
                        yield ContentDelta(
                            type="tool_use",
                            text=ToolCall(
                                id=current_tool["id"],
                                name=current_tool["name"],
                                arguments=args,
                                error=err,
                            ),
                        )
                        current_tool = None
                elif et == "message_delta":
                    # 流末尾携带 usage 信息(input/output/cache tokens)
                    delta = getattr(event, "usage", None)
                    if delta is not None:
                        yield ContentDelta(
                            type="usage",
                            text=UsageStats(
                                input_tokens=getattr(delta, "input_tokens", 0) or 0,
                                output_tokens=getattr(delta, "output_tokens", 0) or 0,
                                cache_read_tokens=getattr(
                                    delta, "cache_read_input_tokens", 0
                                )
                                or 0,
                                cache_write_tokens=getattr(
                                    delta, "cache_creation_input_tokens", 0
                                )
                                or 0,
                            ),
                        )


__all__ = ["AnthropicBackend"]