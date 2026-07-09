"""v1.1 Hooks Lifecycle — public API re-exports.

触发条件明确、动作固定的 Agent 生命周期自动化。设计原则:
- 配置嵌 `config.yaml` 的 `hooks:` 块
- L1 硬黑名单永远先于 hook.pre(任何 hook.allow 不能绕过)
- hook.post 每次 tool_call 尝试必触发(完整审计)
- 失败 fail-open(log.warning,不打断 Agent 主流程)
- 默认向后兼容 v1.0(无 `hooks:` 块行为不变)
"""
from __future__ import annotations

from baozicode.hooks.audit import HookAuditLog, HookInvocation
from baozicode.hooks.bootstrap import load_hooks
from baozicode.hooks.condition import evaluate_condition, matchers
from baozicode.hooks.dispatcher import HookContext, HookDispatcher, HookResult
from baozicode.hooks.executor import ActionResult, execute_action
from baozicode.hooks.registry import HookRegistry, HookValidationError
from baozicode.hooks.schema import (
    ActionYaml,
    ConditionYaml,
    EventName,
    HookDefYaml,
    MatchValue,
    MatcherYaml,
)

__all__ = [
    "ActionResult",
    "ActionYaml",
    "ConditionYaml",
    "EventName",
    "HookAuditLog",
    "HookContext",
    "HookDefYaml",
    "HookDispatcher",
    "HookInvocation",
    "HookRegistry",
    "HookResult",
    "HookValidationError",
    "MatchValue",
    "MatcherYaml",
    "evaluate_condition",
    "execute_action",
    "load_hooks",
    "matchers",
]
