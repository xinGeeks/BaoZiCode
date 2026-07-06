"""OpenAI 兼容后端实现（含 OpenAI、MiniMax、DeepSeek 三个子类）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import ClassVar

from openai import AsyncOpenAI

from baozicode.llm.base import ContentDelta, LLMClient, Message


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
    ) -> AsyncIterator[ContentDelta]:
        sdk_messages: list[dict] = []
        if system:
            sdk_messages.append({"role": "system", "content": system})
        sdk_messages.extend(m.to_dict() for m in messages)

        async with self._client.chat.completions.stream(
            model=self._model,
            messages=sdk_messages,
        ) as stream:
            async for event in stream:
                if event.type == "content.delta" and event.delta:
                    yield ContentDelta(type="text", text=event.delta)


class OpenAIBackend(OpenAICompatibleBackend):
    """OpenAI 官方 Chat Completions API 客户端。"""

    DEFAULT_BASE_URL: ClassVar[str] = "https://api.openai.com/v1"
    DEFAULT_MODEL: ClassVar[str] = "gpt-5"
