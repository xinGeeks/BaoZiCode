"""根据配置创建对应的 LLM 客户端。"""

from __future__ import annotations

from baozicode.config.schema import AppConfig, BackendName
from baozicode.llm.anthropic import AnthropicBackend
from baozicode.llm.base import LLMClient
from baozicode.llm.deepseek import DeepSeekBackend
from baozicode.llm.minimax import MiniMaxBackend
from baozicode.llm.openai import OpenAIBackend

_BACKEND_CLASSES: dict[BackendName, type[LLMClient]] = {
    "anthropic": AnthropicBackend,
    "openai": OpenAIBackend,
    "minimax": MiniMaxBackend,
    "deepseek": DeepSeekBackend,
}


def create_client(config: AppConfig) -> LLMClient:
    """根据 `config.backend` 字段返回对应的客户端实例。"""
    active = config.active()
    cls = _BACKEND_CLASSES[config.backend]
    return cls(
        api_key=active.api_key,
        model=active.model,
        base_url=active.base_url,
    )
