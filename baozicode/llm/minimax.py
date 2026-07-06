"""MiniMax 后端实现（OpenAI 兼容）。"""

from __future__ import annotations

from typing import ClassVar

from baozicode.llm.openai import OpenAICompatibleBackend


class MiniMaxBackend(OpenAICompatibleBackend):
    """MiniMax API 客户端，OpenAI 兼容协议。

    默认 base_url 是占位猜测值，**强烈建议在 config.yaml 中显式设置**。
    """

    DEFAULT_BASE_URL: ClassVar[str] = "https://api.minimaxi.com/v1"
    DEFAULT_MODEL: ClassVar[str] = "MiniMax-M3"
