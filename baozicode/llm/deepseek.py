"""DeepSeek 后端实现（OpenAI 兼容）。"""

from __future__ import annotations

from typing import ClassVar

from baozicode.llm.openai import OpenAICompatibleBackend


class DeepSeekBackend(OpenAICompatibleBackend):
    """DeepSeek API 客户端，OpenAI 兼容协议。"""

    DEFAULT_BASE_URL: ClassVar[str] = "https://api.deepseek.com/v1"
    DEFAULT_MODEL: ClassVar[str] = "deepseek-chat"
