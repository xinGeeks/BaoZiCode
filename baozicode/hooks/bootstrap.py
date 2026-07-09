"""Bootstrap:从 AppConfig.hooks 加载 + 冻结 + 创建 dispatcher。

`load_hooks(app_config, agent, *, audit_log_path=None)`:
- app_config.hooks is None → 返回 None(Agent 当 None 处理,v1.0 行为)
- 非 None → registry.load → freeze → create_dispatcher(agent, audit_log=...)
- audit_log_path 给定时,构造 HookAuditLog 实例并注入 dispatcher

错误:HookValidationError → re-raise,App 启动期统一转 SystemExit
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from baozicode.hooks._errors import HookValidationError
from baozicode.hooks.audit import HookAuditLog
from baozicode.hooks.registry import HookRegistry

if TYPE_CHECKING:
    from baozicode.hooks.dispatcher import HookDispatcher


log = logging.getLogger(__name__)


def load_hooks(
    app_config: Any,
    agent: Any,
    *,
    audit_log_path: Union[str, Path, None] = None,
) -> "HookDispatcher | None":
    """加载 hooks 配置 + 启动期校验 + 构造 dispatcher(注入 audit_log)。

    返回 None 表示 v1.0 行为(无 hooks 块,Agent 不走 hook 路径)。
    任何 HookValidationError 抛出 — App 启动期应转 SystemExit,提示明确错误。

    audit_log_path 给定时,默认 100MB 启动期 rotate,JSONL append-only。
    """
    raw_hooks = getattr(app_config, "hooks", None)
    if raw_hooks is None:
        log.debug("hook 系统未启用(app_config.hooks is None)")
        return None

    registry = HookRegistry.load(raw_hooks)
    registry.freeze()
    log.info("hook 系统已加载:%d 条规则", len(registry.all_hooks()))

    audit_log: HookAuditLog | None = None
    if audit_log_path is not None:
        audit_log = HookAuditLog(Path(audit_log_path))
        audit_log.rotate_if_needed()
        log.info("hook 审计日志: %s", audit_log.path)

    return registry.create_dispatcher(agent, audit_log=audit_log)


__all__ = ["load_hooks"]
