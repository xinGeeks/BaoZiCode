"""三层 stop guards — 防止 Agent 跑飞。

D4 决策:
- 三个 guard 是独立判断函数(纯函数,无副作用)
- 共享 GuardState(deny_counts / recent_failures / recent_unknown)
- 任一返回 StopReason → Agent._terminate(reason) 触发

阈值(从 spec 拍板):
- (a) 未知工具:连续 2 次同未知名 → UNKNOWN_TOOL_HALLUCINATION
- (b) 拒绝累计:同 tool_name 累计 deny 3 次 → DENIALS_EXCEEDED
- (c) 失败循环:同 (name, sha256(error_msg)[:16]) 连续 3 次 → FAILED_TOOL_LOOP
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field

from baozicode.agent.events import StopReason


def error_signature(error_msg: str) -> str:
    """对 error_msg 取 sha256 前 16 字符作为精确 hash。

    防止 LLM "改个参数重试" 逃避检测(改参数 error_msg 通常会变 → 哈希不同 → 不计入死循环)。
    """
    return hashlib.sha256(error_msg.encode("utf-8")).hexdigest()[:16]


@dataclass
class GuardState:
    """per-run 状态,在 Agent.run 开始时 reset。"""

    deny_counts: dict[str, int] = field(default_factory=dict)
    recent_failures: deque[tuple[str, str]] = field(
        default_factory=lambda: deque(maxlen=10)
    )
    recent_unknown: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.deny_counts.clear()
        self.recent_failures.clear()
        self.recent_unknown.clear()


def check_unknown_tool(
    call,
    state: GuardState,
    *,
    valid_names: set[str] | None = None,
) -> StopReason | None:
    """(a) 不在 `valid_names` 集合内的工具名连续 2 次出现 → 幻觉终止。

    - v0.3 修复:`valid_names` 必须由 Agent 显式传入,守卫只在 `call.name` 不
      属于 `valid_names` 时才计入 `recent_unknown`。否则合法的 Read/Bash 重复
      调用会被误判为幻觉。
    - 第 1 次出现 → 追加到 recent_unknown,返回 None(让 Agent 当失败工具处理)。
    - 连续 2 次出现 → 返回 UNKNOWN_TOOL_HALLUCINATION。
    """
    name = call.name
    if valid_names is not None and name in valid_names:
        # 合法工具不计入 recent_unknown,不触发终止
        return None
    if name in state.recent_unknown:
        return StopReason.UNKNOWN_TOOL_HALLUCINATION
    state.recent_unknown.append(name)
    return None


def check_deny_threshold(call, state: GuardState) -> StopReason | None:
    """(b) 同 tool_name 累计被拒 3 次。

    调用方在 deny 时(用户按 N 或 permissions.deny 命中)调 record_denial(state, name)
    把计数 +1,然后调用此函数判断是否到阈值。
    """
    name = call.name
    if state.deny_counts.get(name, 0) >= 3:
        return StopReason.DENIALS_EXCEEDED
    return None


def check_failed_loop(call, state: GuardState, error_msg: str) -> StopReason | None:
    """(c) 同 (name, error_hash) 连续 3 次失败。

    调用方在 tool_result.is_error=True 时调 record_failure(state, name, error_msg)
    把 (name, hash) 追加到 recent_failures,然后调用此函数判断。
    """
    name = call.name
    sig = error_signature(error_msg)
    state.recent_failures.append((name, sig))

    if len(state.recent_failures) >= 3:
        last3 = list(state.recent_failures)[-3:]
        if all(n == name and s == sig for n, s in last3):
            return StopReason.FAILED_TOOL_LOOP
    return None


def record_denial(state: GuardState, name: str) -> None:
    """累计一次 deny。Agent 在用户按 N 或 permissions.deny 命中时调。"""
    state.deny_counts[name] = state.deny_counts.get(name, 0) + 1


__all__ = [
    "GuardState",
    "check_deny_threshold",
    "check_failed_loop",
    "check_unknown_tool",
    "error_signature",
    "record_denial",
]
