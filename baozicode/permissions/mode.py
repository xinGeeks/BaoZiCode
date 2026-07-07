"""L4 PermissionMode — 三档信任级别(v0.5)。

三档:
- `strict`     fallthrough → DENY(所有未明确允许的全拒,人在回路不再弹)
- `default`    fallthrough → fallthrough(交给 L5 user)
- `permissive` fallthrough → ALLOW(全放行,人在回路不再弹)

`apply()` 在 L1/L2/L3 都 fallthrough 之后被调用,作为"兜底信任等级"。
它的作用是把 fallthrough 转成确定性决策,这样后续的流水线不必再关心
"前面有没有 deny"。

重要语义:
- 一旦 L1/L2/L3 已经给出 allow/deny,`apply()` **不改变** 它
- `strict` 模式下,L5 Modal 不再被触发(已 deny)
- `permissive` 模式下,L5 Modal 不再被触发(已 allow)
- `default` 模式下,L5 Modal 才有机会出现
"""

from __future__ import annotations

from baozicode.permissions.types import PermissionDecision, PermissionMode


def apply(decision_so_far: PermissionDecision, mode: PermissionMode) -> PermissionDecision:
    """根据 mode 处理一个 fallthrough 决策;allow/deny 原样返回。

    Args:
        decision_so_far: 前面 L1-L3 给出的决策
        mode: 当前 L4 模式

    Returns:
        - 如果 decision_so_far 是 allow/deny,原样返回
        - 如果 decision_so_far 是 fallthrough:
            - strict → deny
            - default → fallthrough(原样)
            - permissive → allow
    """
    if decision_so_far.decision != "fallthrough":
        return decision_so_far

    if mode == "strict":
        return PermissionDecision(
            decision="deny",
            layer="L4_mode",
            reason=(
                "L4 模式 = strict:任何未明确允许的工具调用都被拒绝"
                "(所有未匹配的 fallthrough 视为 deny)"
            ),
        )
    if mode == "permissive":
        return PermissionDecision(
            decision="allow",
            layer="L4_mode",
            reason="L4 模式 = permissive:任何未明确拒绝的工具调用都被放行",
        )
    # default:fallthrough 透传
    return decision_so_far


__all__ = ["apply"]
