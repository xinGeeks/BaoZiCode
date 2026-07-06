"""Anthropic 后端实现，包装官方 anthropic-sdk-python。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from baozicode.llm.base import ContentDelta, LLMClient, Message


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
    ) -> AsyncIterator[ContentDelta]:
        sdk_messages = [m.to_dict() for m in messages]

        async with self._client.messages.stream(
            model=self._model,
            max_tokens=8192,
            system=system or "",
            messages=sdk_messages,
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield ContentDelta(type="text", text=text)
