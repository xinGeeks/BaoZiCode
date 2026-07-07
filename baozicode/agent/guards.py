"""三层 stop guards — 防止 Agent 跑飞(v0.5 调整 deny 行为)。

D4 决策:
- 三个 guard 是独立判断函数(纯函数,无副作用)
- 共享 GuardState(deny_counts / recent_failures / recent_unknown)

v0.5 调整(denial 行为):
- v0.4:同 tool_name 累计 deny 3 次 → DENIALS_EXCEEDED 终止
- v0.5:deny **不再终止** Agent Loop;改为当某工具连续拒绝次数达到
  `denial_warn_threshold` 时,向 LLM 注入 `<system-reminder type="denial_rate_limit">`,
  提示它调整策略(换工具、改参数、问用户等)
- 行为变化:`StopReason.DENIALS_EXCEEDED` 不再被 Agent 触发,
  仍保留为枚举值(backward compat);改用 `should_inject_denial_reminder` 钩子

阈值(从 spec 拍板):
- (a) 未知工具:连续 2 次同未知名 → UNKNOWN_TOOL_HALLUCINATION(未变)
- (b) 拒绝累计:同 tool_name 累计 ≥ `denial_warn_threshold`(默认 5)→
      注入 reminder(不再终止)
- (c) 失败循环:同 (name, sha256(error_msg)[:16]) 连续 3 次 → FAILED_TOOL_LOOP(未变)
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


# ---- v0.5 改造:deny 不再终止,改为 reminder ----

def record_denial_warn(state: GuardState, name: str) -> None:
    """累计一次 deny。Agent 在每次工具调用被拒时调。

    v0.5:不再触发 DENIALS_EXCEEDED,只用于 should_inject_denial_reminder 判断。
    """
    state.deny_counts[name] = state.deny_counts.get(name, 0) + 1


def should_inject_denial_reminder(
    tool_name: str,
    state: GuardState,
    threshold: int = 5,
) -> bool:
    """(b) v0.5 替代 `check_deny_threshold`:是否该向 LLM 注入 denial reminder。

    返回 True 表示同一 tool_name 累计拒绝次数达到阈值,Agent 应在
    `_inject_reminders` 中追加 `<system-reminder type="denial_rate_limit">`。
    """
    return state.deny_counts.get(tool_name, 0) >= threshold


# ---- v0.4 兼容:旧 API 保留,内部转调 ----

def record_denial(state: GuardState, name: str) -> None:
    """v0.4 兼容 API,内部转调 `record_denial_warn`。"""
    record_denial_warn(state, name)


def check_deny_threshold(call, state: GuardState) -> StopReason | None:
    """v0.4 兼容:旧逻辑下"同 tool_name 累计 3 次 → DENIALS_EXCEEDED"。

    v0.5:此函数永远返回 None(deny 不再终止)。保留是为了不破坏旧 import。
    """
    # 故意不实现终止语义 — 见 D7:deny 不终止 Agent Loop
    return None


__all__ = [
    "GuardState",
    "check_deny_threshold",  # v0.4 兼容,实际 no-op
    "check_failed_loop",
    "check_unknown_tool",
    "error_signature",
    "record_denial",          # v0.4 兼容
    "record_denial_warn",     # v0.5 推荐
    "should_inject_denial_reminder",
]
