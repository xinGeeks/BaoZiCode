"""Bootstrap:从 AppConfig.hooks 加载 + 冻结 + 创建 dispatcher。

`load_hooks(app_config, agent)`:
- app_config.hooks is None → 返回 None(Agent 当 None 处理,v1.0 行为)
- 非 None → registry.load → freeze → create_dispatcher(agent)

错误:HookValidationError → re-raise,App 启动期统一转 SystemExit
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from baozicode.hooks._errors import HookValidationError
from baozicode.hooks.registry import HookRegistry

if TYPE_CHECKING:
    from baozicode.hooks.dispatcher import HookDispatcher


log = logging.getLogger(__name__)


def load_hooks(app_config: Any, agent: Any) -> "HookDispatcher | None":
    """加载 hooks 配置 + 启动期校验 + 构造 dispatcher。

    返回 None 表示 v1.0 行为(无 hooks 块,Agent 不走 hook 路径)。
    任何 HookValidationError 抛出 — App 启动期应转 SystemExit,提示明确错误。
    """
    raw_hooks = getattr(app_config, "hooks", None)
    if raw_hooks is None:
        log.debug("hook 系统未启用(app_config.hooks is None)")
        return None

    registry = HookRegistry.load(raw_hooks)
    registry.freeze()
    log.info("hook 系统已加载:%d 条规则", len(registry.all_hooks()))
    return registry.create_dispatcher(agent)


__all__ = ["load_hooks"]
