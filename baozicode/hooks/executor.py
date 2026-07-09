"""Action 执行器:4 种 action 各自的 run 函数 + 公共 ActionResult。

shell:
- asyncio.create_subprocess_exec("bash", "-c", command)
- exit_code == 0 → deny=False
- exit_code != 0 → deny=True,reason = stdout 第一行;空 stdout 兜底"hook shell 拦截..."
- timeout → deny=True,reason="hook shell 执行超时"

http:
- aiohttp GET / POST
- 4xx/5xx / 连接异常 → deny=False(接口报错 ≠ 主动拦截)
- parse_expr:simpleeval,res = SimpleNamespace(status, body);可赋 res.deny + res.deny_reason
- deny=True 时 deny_reason 必填配套

sub-agent:
- v1.1 占位:parse_expr 评估,res 里有 output 字段占位
- 实际子 Agent 执行留 v1.2

prompt:
- slot=sticky_reminder(默认)→ agent.enqueue_reminder(kind="hook_prompt", body, ttl)
- slot=stable_system → agent.set_dynamic_section("hook_overrides", content)
- slot=temp → agent_state.temp_reminders(下轮消失)
- enqueue=False → 仅 log.info

shell action 不允许 deny / parse_expr(Pydantic extra="forbid" 挡住);同理 prompt。
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING

from baozicode.hooks._errors import HookParseError, HookSlotError
from baozicode.hooks.schema import (
    HookDefYaml,
    _HttpAction,
    _PromptAction,
    _ShellAction,
    _SubAgentAction,
)

if TYPE_CHECKING:
    from baozicode.hooks.dispatcher import HookContext


log = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """单条 action 执行的结果。

    deny / reason 用于 dispatcher 短路判断;
    enqueue_body 用于 prompt action 之后回流给 Agent 的 reminder;
    error 用于异常路径(非 deny,只是记录)。
    """

    deny: bool = False
    reason: str | None = None
    enqueue_body: str | None = None
    error: str | None = None


# ---------- shell ----------


async def execute_shell(action: _ShellAction, ctx: "HookContext") -> ActionResult:
    """跑 bash -c command。env 自动注入 $TOOL_NAME / $ARG_<NAME> 等。"""
    env = os.environ.copy()
    env["TOOL_NAME"] = getattr(ctx.payload, "name", "") if ctx.payload else ""
    env["TOOL_CALL_ID"] = getattr(ctx.payload, "id", "") if ctx.payload else ""
    env["EVENT"] = ctx.event
    env["HOOK_ID"] = ctx.hook_id
    if ctx.payload and getattr(ctx.payload, "arguments", None):
        for k, v in ctx.payload.arguments.items():
            v_str = v if isinstance(v, str) else str(v)
            env[f"ARG_{k.upper()}"] = v_str

    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", action.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_b, _ = await asyncio.wait_for(
                proc.communicate(), timeout=action.timeout_seconds
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return ActionResult(
                deny=True,
                reason=f"hook shell 执行超时 ({action.timeout_seconds}s)",
            )
    except FileNotFoundError:
        return ActionResult(
            deny=True,
            reason="hook shell 执行失败:bash 不在 PATH 中",
        )
    except Exception as exc:
        log.warning("hook %s shell 执行异常: %s", ctx.hook_id, exc)
        return ActionResult(deny=False, error=str(exc))

    if proc.returncode == 0:
        return ActionResult(deny=False)

    stdout = stdout_b.decode("utf-8", errors="replace").strip() if stdout_b else ""
    reason = stdout.split("\n", 1)[0] if stdout else "hook shell 拦截高危工具调用"
    return ActionResult(deny=True, reason=reason)


# ---------- http ----------


async def execute_http(action: _HttpAction, ctx: "HookContext") -> ActionResult:
    """发 HTTP,parse_expr 决定 deny。

    4xx/5xx / 连接异常 → deny=False(只 log.warning,不主动拦)。
    """
    import aiohttp  # 延迟 import,避免 hooks 包强制依赖 aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            method_fn = session.get if action.method == "GET" else session.post
            kwargs: dict[str, Any] = {}
            if action.body is not None:
                kwargs["json"] = action.body
            async with method_fn(action.url, **kwargs) as resp:
                status = resp.status
                try:
                    body: Any = await resp.json(content_type=None)
                except Exception:
                    body = await resp.text()
    except Exception as exc:
        log.warning("hook %s http 调用失败(%s): %s", ctx.hook_id, action.method, exc)
        return ActionResult(deny=False, error=str(exc))

    if not action.parse_expr:
        return ActionResult(deny=False)

    return _eval_parse_expr(
        action.parse_expr,
        res_obj=SimpleNamespace(status=status, body=body),
        deny_reason_default=action.deny_reason,
        hook_id=ctx.hook_id,
    )


# ---------- sub-agent ----------


async def execute_subagent(action: _SubAgentAction, ctx: "HookContext") -> ActionResult:
    """v1.1 占位:parse_expr 评估,res 里有 output 字段占位。

    实际子 Agent 启动(开子 ConversationManager + Agent)留 v1.2。
    """
    if not action.parse_expr:
        log.info(
            "hook %s sub-agent 触发(goal=%r),v1.1 仅占位不实际执行",
            ctx.hook_id, action.goal,
        )
        return ActionResult(deny=False)

    log.warning("sub-agent 执行器 v1.1 仅占位:hook %s", ctx.hook_id)
    placeholder_output = {"goal": action.goal, "status": "v1.1-not-implemented"}
    return _eval_parse_expr(
        action.parse_expr,
        res_obj=SimpleNamespace(
            status="placeholder",
            output=placeholder_output,
            body=placeholder_output,
        ),
        deny_reason_default=action.deny_reason,
        hook_id=ctx.hook_id,
    )


# ---------- prompt ----------


async def execute_prompt(action: _PromptAction, ctx: "HookContext") -> ActionResult:
    """注入 prompt 到 system prompt。slot 决定位置。"""
    # tool.pre / tool.post 配 stable_system 被 freeze 阶段挡住;防御性 run-time 检查
    if action.slot == "stable_system" and ctx.event in ("tool.pre", "tool.post"):
        log.error(
            "hook %s prompt slot=stable_system 在 %s 事件被忽略(injection 时机不对)",
            ctx.hook_id, ctx.event,
        )
        return ActionResult(deny=False)

    body = action.content
    if action.slot == "sticky_reminder" and not action.enqueue:
        # 显式 enqueue=False → 仅日志
        log.info("hook_prompt: %s", body[:120])
        return ActionResult(deny=False, enqueue_body=None)

    if action.slot == "sticky_reminder":
        if ctx.agent is None:
            log.warning("hook %s 无法 enqueue reminder:agent 未注入", ctx.hook_id)
            return ActionResult(deny=False)
        try:
            ctx.agent.enqueue_reminder(kind="hook_prompt", body=body, ttl="sticky")
        except Exception as exc:
            log.warning("hook %s enqueue_reminder 失败: %s", ctx.hook_id, exc)
            return ActionResult(deny=False, error=str(exc))
        return ActionResult(deny=False, enqueue_body=body)

    if action.slot == "stable_system":
        if ctx.agent is None:
            return ActionResult(deny=False, error="agent not injected")
        try:
            ctx.agent.set_dynamic_section("hook_overrides", body)
        except Exception as exc:
            raise HookSlotError(f"stable_system 注入失败: {exc}") from exc
        return ActionResult(deny=False, enqueue_body=body)

    # slot == "temp"
    if ctx.agent is None:
        return ActionResult(deny=False)
    state = getattr(ctx.agent, "state", None) or getattr(ctx.agent, "_state", None)
    if state is None:
        log.warning("hook %s 无法找到 agent state 注入 temp reminder", ctx.hook_id)
        return ActionResult(deny=False)
    temp_list = getattr(state, "temp_reminders", None)
    if temp_list is None:
        log.warning("hook %s agent.state 没有 temp_reminders 字段", ctx.hook_id)
        return ActionResult(deny=False)
    temp_list.append(body)
    return ActionResult(deny=False, enqueue_body=body)


# ---------- 公共 ----------


async def execute_action(action: Any, ctx: "HookContext") -> ActionResult:
    """dispatch 到对应 executor。"""
    if isinstance(action, _ShellAction):
        return await execute_shell(action, ctx)
    if isinstance(action, _HttpAction):
        return await execute_http(action, ctx)
    if isinstance(action, _SubAgentAction):
        return await execute_subagent(action, ctx)
    if isinstance(action, _PromptAction):
        return await execute_prompt(action, ctx)
    return ActionResult(deny=False, error=f"unknown action kind: {type(action).__name__}")


def _eval_parse_expr(
    expr: str,
    *,
    res_obj: Any,
    deny_reason_default: str | None,
    hook_id: str,
) -> ActionResult:
    """用 simpleeval 评估 parse_expr,允许赋 res.deny / res.deny_reason。"""
    try:
        from simpleeval import EvalWithCompoundTypes, NameNotDefined
    except ImportError:
        raise HookParseError(
            "simpleeval 未安装;pip install simpleeval 后再启用 http/sub-agent 的 parse_expr"
        )

    evaluator = EvalWithCompoundTypes(names={"res": res_obj})
    try:
        evaluator.eval(expr)
    except Exception as exc:
        raise HookParseError(f"parse_expr 评估失败: {exc}") from exc

    deny = bool(getattr(res_obj, "deny", False))
    if not deny:
        return ActionResult(deny=False)

    reason = getattr(res_obj, "deny_reason", None) or deny_reason_default
    if not reason:
        raise HookParseError(
            f"hook {hook_id} parse_expr 设了 res.deny=True 但未给 deny_reason"
        )
    return ActionResult(deny=True, reason=str(reason))


__all__ = ["ActionResult", "execute_action"]


def now_iso() -> str:
    """审计时间戳:UTC ISO 8601 with millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_invocation(
    *,
    event: str,
    hook_id: str,
    action_kind: str,
    tool_name: str = "",
    tool_call_id: str = "",
    deny: bool = False,
    reason: str | None = None,
    duration_ms: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    """构造 HookInvocation 字典(供 audit.py JSONL 写入)。

    这里放在 executor 是为了集中 dataclass 风格;audit.py 可直接调用此函数
    或者根据需要重写。
    """
    return {
        "timestamp": now_iso(),
        "event": event,
        "hook_id": hook_id,
        "action_kind": action_kind,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "deny": deny,
        "reason": reason,
        "duration_ms": duration_ms,
        "error": error,
    }
