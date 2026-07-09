"""HookDispatcher:事件分发主入口 + 多 hook 串行短路。

run(event, payload) → HookResult:
1. 遍历 registry.list_hooks(event),按声明顺序
2. 对每条 hook 先 evaluate_condition(if_, payload) → 不命中就跳过整条
3. 命中就顺序执行 actions,任一 deny → 停止当前 hook 的后续 actions,返回 deny
4. 任一 hook 拒 → 停止整个 dispatcher 后续 hooks

每个 action 用 try/except 包,fail-open:异常 → log.warning,继续下一个

async post hook(async=True)→ asyncio.create_task 后台跑;默认 enqueue=False 仅 log
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from baozicode.hooks.executor import execute_action
from baozicode.hooks.schema import _PromptAction, _ShellAction, HookDefYaml

if TYPE_CHECKING:
    from baozicode.hooks.registry import HookRegistry


log = logging.getLogger(__name__)


@dataclass
class HookContext:
    """单条 action 执行时的上下文。"""

    event: str
    hook_id: str
    agent: Any = None
    payload: Any = None
    timeout: int = 30
    enqueue: bool = False


@dataclass
class HookResult:
    """dispatcher.run 的返回值。"""

    denied: bool = False
    denied_hook_id: str | None = None
    reason: str | None = None
    error: str | None = None


class HookDispatcher:
    """持有 registry + agent 反向引用;对外暴露 run() 一个方法。"""

    def __init__(
        self,
        registry: "HookRegistry",
        agent: Any,
        *,
        audit_log: Any = None,
    ) -> None:
        self._registry = registry
        self._agent = agent
        # v1.1.1:audit_log(可选)—— HookAuditLog 实例,HookInvocation 落 JSONL
        self._audit_log = audit_log
        # v1.1.1:run_once 去重 —— 整个 Agent.run 生命周期内只跑一次
        self._fired_once: set[str] = set()

    def agent(self) -> Any:
        return self._agent

    def run(self, event: str, payload: Any) -> HookResult:
        """同步分发 event;pre hook 路径必须等结果,post 可后台。"""
        hooks = self._registry.list_hooks(event)
        if not hooks:
            return HookResult()

        for hook in hooks:
            # v1.1.1:run_once 全 session 只跑一次 —— 已跑过就跳过
            if hook.run_once and hook.id in self._fired_once:
                continue

            # async post 后台跑(其他 event 不支持 async,freeze 已挡)
            if hook.async_:
                if event == "tool.post":
                    asyncio.create_task(self._run_hook_async(hook, event, payload))
                    if hook.run_once:
                        self._fired_once.add(hook.id)
                    continue
                log.error(
                    "hook %s async=true 在 %s 事件上不被允许,跳过",
                    hook.id, event,
                )
                continue

            result = self._run_hook_sync(hook, event, payload)
            if hook.run_once:
                self._fired_once.add(hook.id)
            if result.denied:
                return result

        return HookResult()

    def _run_hook_sync(self, hook: HookDefYaml, event: str, payload: Any) -> HookResult:
        """同步跑一条 hook:condition + 串行 actions。

        v1.1.1:成功执行或 deny 都会写一条 HookInvocation 审计(若 audit_log 注入)。
        """
        # 条件不命中 → 跳过整条
        from baozicode.hooks.condition import evaluate_condition
        import time as _time

        t0 = _time.monotonic()
        denied = False
        denial_reason: str | None = None
        error_msg: str | None = None
        try:
            if not evaluate_condition(hook.if_, payload):
                return HookResult()
        except Exception as exc:
            log.warning(
                "hook %s condition 评估失败,跳过本条: %s",
                hook.id, exc,
            )
            return HookResult()

        ctx = HookContext(
            event=event,
            hook_id=hook.id,
            agent=self._agent,
            payload=payload,
            timeout=hook.timeout_seconds,
        )

        for action in hook.actions:
            try:
                coro = execute_action(action, ctx)
                result = asyncio.run(coro)
            except Exception as exc:
                log.warning(
                    "hook %s action %s 抛异常,继续: %s",
                    hook.id, type(action).__name__, exc,
                )
                error_msg = str(exc)
                continue

            if result.error:
                log.warning("hook %s action 报错: %s", hook.id, result.error)
                error_msg = result.error
            if result.deny:
                denied = True
                denial_reason = result.reason
                self._record_audit(
                    event=event, hook=hook, payload=payload,
                    deny=True, reason=result.reason,
                    duration_ms=int((_time.monotonic() - t0) * 1000),
                )
                return HookResult(
                    denied=True,
                    denied_hook_id=hook.id,
                    reason=result.reason,
                )

        self._record_audit(
            event=event, hook=hook, payload=payload,
            deny=False, reason=None,
            duration_ms=int((_time.monotonic() - t0) * 1000),
            error=error_msg,
        )
        return HookResult()

    def _record_audit(
        self,
        *,
        event: str,
        hook: HookDefYaml,
        payload: Any,
        deny: bool,
        reason: str | None,
        duration_ms: int,
        error: str | None = None,
    ) -> None:
        """写一条 HookInvocation 到 audit_log(注入则跳过)。

        全程 try/except —— audit 写失败不阻断 hook 主流程(fail-open)。
        """
        if self._audit_log is None:
            return
        try:
            from baozicode.hooks.audit import HookInvocation
            tool_name = ""
            tool_call_id = ""
            if payload is not None:
                tool_name = getattr(payload, "name", "") or ""
                tool_call_id = getattr(payload, "id", "") or ""
            inv = HookInvocation(
                event=event,
                hook_id=hook.id,
                action_kind=hook.actions[0].action if hook.actions else "",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                deny=deny,
                reason=reason,
                duration_ms=duration_ms,
                error=error,
            )
            self._audit_log.record_invocation_sync(inv)
        except Exception as exc:
            log.warning("hook audit 记录失败: %s", exc)

    async def _run_hook_async(
        self, hook: HookDefYaml, event: str, payload: Any
    ) -> None:
        """async post 后台跑:类似同步,但 enqueue=True 时调 agent.enqueue_reminder。"""
        from baozicode.hooks.condition import evaluate_condition
        try:
            if not evaluate_condition(hook.if_, payload):
                return
        except Exception as exc:
            log.warning("hook %s async condition 失败,跳过: %s", hook.id, exc)
            return

        ctx = HookContext(
            event=event,
            hook_id=hook.id,
            agent=self._agent,
            payload=payload,
            timeout=hook.timeout_seconds,
        )

        for action in hook.actions:
            try:
                # 收集 prompt + enqueue 处理
                if isinstance(action, _PromptAction) and action.slot == "sticky_reminder":
                    if action.enqueue and self._agent is not None:
                        try:
                            self._agent.enqueue_reminder(
                                kind="hook_prompt",
                                body=action.content,
                                ttl="sticky",
                            )
                        except Exception as exc:
                            log.warning("hook %s async enqueue 失败: %s", hook.id, exc)
                        continue
                    if not action.enqueue:
                        log.info("hook_prompt(async): %s", action.content[:120])
                        continue
                if isinstance(action, _ShellAction) and not action.enqueue:
                    # 后台写日志不灌 prompt
                    try:
                        result = await execute_action(action, ctx)
                        if result.error:
                            log.warning("hook %s async action 报错: %s", hook.id, result.error)
                    except Exception as exc:
                        log.warning("hook %s async action 异常: %s", hook.id, exc)
                    continue

                # 默认:后台跑,失败仅 log(不灌 prompt,不阻塞主流程)
                result = await execute_action(action, ctx)
                if result.error:
                    log.warning("hook %s async action 报错: %s", hook.id, result.error)
            except Exception as exc:
                log.warning("hook %s async action 异常: %s", hook.id, exc)


__all__ = ["HookContext", "HookDispatcher", "HookResult"]
