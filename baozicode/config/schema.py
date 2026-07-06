"""配置数据模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BackendName = Literal["anthropic", "openai", "minimax", "deepseek"]


class BackendConfig(BaseModel):
    """单个 LLM 后端的配置。"""

    api_key: str
    model: str
    base_url: str | None = None


class Permissions(BaseModel):
    """工具调用权限控制(v0.2)。

    - `auto_allow`: 跳过高风险确认 Modal 的工具名列表(仍执行,仍记录卡片)
    - `deny`: 永远拒绝执行的工具名/glob 列表(命中直接 is_error,弹红 ✗ 卡片)
    - `batch_confirm`: 高风险工具连续同类型 ≥2 次时,第一次 Modal 提供
      "Allow all remaining" 按钮
    - `bash_locked_cwd`: 是否把 Bash 会话 cwd 锁回项目根(关掉 cd 跟随)

    任何 v0.2 之后新增的字段都会被 silent ignore(`extra="ignore"`)。
    """

    model_config = ConfigDict(extra="ignore")

    auto_allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    batch_confirm: bool = False
    bash_locked_cwd: bool = False


_DEFAULT_PERMISSIONS = Permissions()


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
    permissions: Permissions | None = None

    def active(self) -> BackendConfig:
        """返回当前激活后端的配置。"""
        return getattr(self, self.backend)

    def all_backends(self) -> list[tuple[BackendName, BackendConfig]]:
        """返回全部 4 个后端,按固定顺序。"""
        return [
            ("anthropic", self.anthropic),
            ("openai", self.openai),
            ("minimax", self.minimax),
            ("deepseek", self.deepseek),
        ]

    def active_permissions(self) -> Permissions:
        """返回当前生效的 permissions,`None` 时回退到全默认。"""
        return self.permissions if self.permissions is not None else _DEFAULT_PERMISSIONS


__all__ = [
    "AppConfig",
    "BackendConfig",
    "BackendName",
    "Permissions",
]