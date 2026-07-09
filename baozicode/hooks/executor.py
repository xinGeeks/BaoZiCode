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
- 实际子 Agent 执行留 v1.1.1(同 migration 文档 §4.5)

prompt:
- slot=sticky_reminder(默认)→ agent.enqueue_reminder(kind="hook_prompt", body, ttl)
- slot=stable_system → agent.set_dynamic_section("hook_overrides", content)
- slot=temp → agent._temp_reminders(下轮 _inject_reminders 消费即清)
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
        res_obj=SimpleNamespace(),
        deny_reason_default=action.deny_reason,
        hook_id=ctx.hook_id,
        extra_names={"body": body, "status": status, "headers": {}},
    )


# ---------- sub-agent ----------


async def execute_subagent(action: _SubAgentAction, ctx: "HookContext") -> ActionResult:
    """v1.1 占位:parse_expr 评估,res 里有 output 字段占位。

    实际子 Agent 启动(开子 ConversationManager + Agent)留 v1.1.1。
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
        res_obj=SimpleNamespace(),
        deny_reason_default=action.deny_reason,
        hook_id=ctx.hook_id,
        extra_names={
            "output": placeholder_output,
            "status": "placeholder",
            "body": placeholder_output,
        },
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

    # slot == "temp" — 一次性 reminder,append 到 agent._temp_reminders,
    # 下轮 _inject_reminders 消费即清(详见 Agent._inject_reminders step 5)
    if ctx.agent is None:
        return ActionResult(deny=False)
    temp_list = getattr(ctx.agent, "_temp_reminders", None)
    if temp_list is None:
        log.warning("hook %s agent 没有 _temp_reminders 字段,跳过 temp 注入", ctx.hook_id)
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
    extra_names: dict[str, Any] | None = None,
) -> ActionResult:
    """用 simpleeval 评估 parse_expr,允许赋 res.deny / res.deny_reason。

    v1.1.1 修复:simpleeval 默认 `EvalWithCompoundTypes._eval_assign` 只 warn
    不实际赋值,导致 `res.deny = True` 被静默忽略(spec 承诺的 deny 触发
    从未真正生效)。用 `_AssigningEval` 子类覆盖 _eval_assign,只允许:
    - 根是 names 字典里的标识符(此处是 `res` + extra_names 里的键)
    - 链式属性赋值:`res.deny = ...` / `res.deny_reason = ...`
    其他形式(ast.Subscript、函数调用、import 等)仍走 SimpleEval 默认拒绝。

    extra_names:额外可读标识符(http 注入 `body`/`status`/`headers`;
    sub-agent 注入 `output` 等)。spec 文档承诺 `body`/`status` 是顶层
    可读 —— v1.1.0 漏注入,本函数加回来。
    """
    try:
        from simpleeval import EvalWithCompoundTypes
        import ast as _ast
    except ImportError:
        raise HookParseError(
            "simpleeval 未安装;pip install simpleeval 后再启用 http/sub-agent 的 parse_expr"
        )

    class _AssigningEval(EvalWithCompoundTypes):
        """只允许对 names 根的属性赋值,其他 AST 节点仍拒绝。

        v1.1.1 增量:支持多语句(parse_expr 写成多行更可读;spec 文档示例就有
        `res.deny = ...; res.deny_reason = ...` 两行)。simpleeval 默认
        `eval()` 用 mode='eval' 只支持单表达式,这里 override 用 mode='exec' 逐句跑。
        """

        def _eval_assign(self, node):  # type: ignore[override]
            # 多 target / 非单赋值 → 默认拒绝
            if len(node.targets) != 1:
                return super()._eval_assign(node)
            target = node.targets[0]
            # 必须是 Attribute 链(支持 res.deny / res.deny.reason 这种)
            if not isinstance(target, _ast.Attribute):
                return super()._eval_assign(node)
            # 沿 Attribute 链回溯到根,根必须是 self.names 里的 Name
            parts: list[str] = []
            cur = target
            while isinstance(cur, _ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if not isinstance(cur, _ast.Name) or cur.id not in self.names:
                return super()._eval_assign(node)
            # 先求值 rhs(可能产生副作用:warn 但不阻断)
            value = self._eval(node.value)
            # 沿链 setattr
            obj = self.names[cur.id]
            parts.reverse()  # 最外层 attr 在前
            for attr in parts[:-1]:
                obj = getattr(obj, attr)
            setattr(obj, parts[-1], value)
            return value

        def eval(self, expr, previously_parsed=None):  # type: ignore[override]
            """支持多语句:用 mode='exec' 解析 + 逐句 dispatch 到 _eval。"""
            import warnings as _w
            try:
                tree = _ast.parse(expr, mode="exec")
            except SyntaxError:
                return super().eval(expr, previously_parsed)
            last = None
            for stmt in tree.body:
                with _w.catch_warnings():
                    _w.simplefilter("ignore")
                    last = self._eval(stmt)
            return last

    names: dict[str, Any] = {"res": res_obj}
    if extra_names:
        names.update(extra_names)
    evaluator = _AssigningEval(names=names)
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
