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

def clear_hook_runtime_state(agent: Any) -> None:
    """v1.1.1:重置 Agent 上 hook 注入的 3 类运行时状态,新对话起点。

    清 3 项(都安全 getattr,缺失跳过):
    - _pending_reminders:sticky `hook_prompt` reminder 会污染新对话第一轮
    - _hook_stable_overrides:`## Hook Overrides` 段钉在 stable_system 末尾
    - _temp_reminders:本意 turn-scoped,跨 turn 持久化无意义

    Hook 定义本身(config.yaml: hooks: 列表)不动 —— 只清运行时注入状态。
    由 chat_screen._clear_conversation 在 /clear 路径调用。
    """
    if agent is None:
        return
    for attr in ("_pending_reminders", "_hook_stable_overrides", "_temp_reminders"):
        if hasattr(agent, attr):
            try:
                setattr(agent, attr, [])
            except Exception:
                pass


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
    "clear_hook_runtime_state",
    "evaluate_condition",
    "execute_action",
    "load_hooks",
    "matchers",
]
