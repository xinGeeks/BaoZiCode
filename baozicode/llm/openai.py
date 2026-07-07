"""OpenAI 兼容后端实现(含 OpenAI、MiniMax、DeepSeek 三个子类)。

支持 v0.2 工具调用:
- `tools` 参数 → `tools=[{type:"function", function:{name, description, parameters}}]`
- 流式 `tool_calls` 累积(按 index 分组);流结束时 yield 完整 ToolCall
- `Message(content=list[ContentBlock])` 拆分:
  - assistant + ToolUseBlock → assistant 消息 + `tool_calls` 字段
  - tool + ToolResultBlock → 独立 `role="tool"` 消息 + `tool_call_id`
  - user + 文本块 → 拼接成单字符串 content
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from openai import AsyncOpenAI

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
    """内部 Message → OpenAI SDK messages dict 列表。"""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue  # 走 system 参数
        if isinstance(m.content, str):
            out.append({"role": m.role, "content": m.content})
            continue
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                text_parts.append(b.text)
            elif isinstance(b, ToolUseBlock):
                tool_calls.append(
                    {
                        "id": b.id,
                        "type": "function",
                        "function": {
                            "name": b.name,
                            "arguments": json.dumps(b.input),
                        },
                    }
                )
            elif isinstance(b, ToolResultBlock):
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": b.tool_use_id,
                        "content": b.content,
                    }
                )
        if m.role == "assistant" and (text_parts or tool_calls):
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(text_parts) if text_parts else None,
            }
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        elif m.role == "user" and text_parts:
            out.append({"role": "user", "content": "".join(text_parts)})
    return out


class OpenAICompatibleBackend(LLMClient):
    """所有 OpenAI 兼容 API 的共同基类。

    子类只需声明 `DEFAULT_BASE_URL` 和 `DEFAULT_MODEL` 两个类属性。
    实际流式逻辑由本基类统一实现。
    """

    DEFAULT_BASE_URL: ClassVar[str] = "https://api.openai.com/v1"
    DEFAULT_MODEL: ClassVar[str] = "gpt-5"

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or self.DEFAULT_BASE_URL,
        )
        self._model = model or self.DEFAULT_MODEL

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
        if system:
            sdk_messages.insert(0, {"role": "system", "content": system})

        params: dict[str, Any] = {
            "model": self._model,
            "messages": sdk_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            params["tools"] = [t.to_openai() for t in tools]

        tool_acc: dict[int, dict[str, str]] = {}
        usage_yielded = False

        stream = await self._client.chat.completions.create(**params)
        async for chunk in stream:
            # 用量信息(流末尾的 chunk.choices 为空,但 chunk.usage 有值)
            if getattr(chunk, "usage", None) is not None and not usage_yielded:
                u = chunk.usage
                yield ContentDelta(
                    type="usage",
                    text=UsageStats(
                        input_tokens=getattr(u, "prompt_tokens", 0) or 0,
                        output_tokens=getattr(u, "completion_tokens", 0) or 0,
                        # OpenAI 不暴露 cache tokens(留给各家扩展字段)
                        cache_read_tokens=getattr(u, "cached_tokens", 0) or 0,
                        cache_write_tokens=0,
                    ),
                )
                usage_yielded = True
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta is None:
                continue

            if delta.content:
                yield ContentDelta(type="text", text=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    acc = tool_acc.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.id:
                        acc["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            acc["name"] = tc.function.name
                        if tc.function.arguments:
                            acc["arguments"] += tc.function.arguments

        # 流结束 — 把累积的 tool_calls 逐个产出
        for idx in sorted(tool_acc.keys()):
            acc = tool_acc[idx]
            raw = acc["arguments"]
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
                    id=acc["id"],
                    name=acc["name"],
                    arguments=args,
                    error=err,
                ),
            )

        # 兜底:某些 backend 不发 usage chunk 时 yield 0
        if not usage_yielded:
            yield ContentDelta(type="usage", text=UsageStats())


class OpenAIBackend(OpenAICompatibleBackend):
    """OpenAI 官方 Chat Completions API 客户端。"""

    DEFAULT_BASE_URL: ClassVar[str] = "https://api.openai.com/v1"
    DEFAULT_MODEL: ClassVar[str] = "gpt-5"


__all__ = ["OpenAICompatibleBackend", "OpenAIBackend"]