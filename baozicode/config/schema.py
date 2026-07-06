"""配置数据模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BackendName = Literal["anthropic", "openai", "minimax", "deepseek"]


class BackendConfig(BaseModel):
    """单个 LLM 后端的配置。"""

    api_key: str
    model: str
    base_url: str | None = None


class AppConfig(BaseModel):
    """BaoZiCode 全局配置。

    四个后端的 `BackendConfig` 块全部必填——这样切换后端时只需改一个字段
    （`backend:`），不需要再去配另一块；Pydantic 校验也会立刻指出缺什么。
    """

    backend: BackendName
    system_prompt: str = "You are BaoZiCode, a helpful AI coding assistant."
    anthropic: BackendConfig
    openai: BackendConfig
    minimax: BackendConfig
    deepseek: BackendConfig

    def active(self) -> BackendConfig:
        """返回当前激活后端的配置。"""
        return getattr(self, self.backend)

    def all_backends(self) -> list[tuple[BackendName, BackendConfig]]:
        """返回全部 4 个后端，按固定顺序。"""
        return [
            ("anthropic", self.anthropic),
            ("openai", self.openai),
            ("minimax", self.minimax),
            ("deepseek", self.deepseek),
        ]
