"""五层防御权限系统(v0.5)。

公开 API:
- `check(call, ctx) -> PermissionDecision` — 5 层流水线总入口
- `bootstrap(project_root, config) -> MergedPermissions` — 启动时初始化
- `DangerousCommandBlacklist`, `PathSandbox`, `RuleEngine`, `apply_mode` — 各层实现

数据模型见 `types.py`。

依赖方向(单向):
    permissions/ → config/ + tools/base.py
    permissions/ → agent/(可选,通过 PermissionCallback 接口)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baozicode.permissions.blacklist import DangerousCommandBlacklist
from baozicode.permissions.engine import RuleEngine
from baozicode.permissions.mode import apply as apply_mode
from baozicode.permissions.sandbox import PathSandbox
from baozicode.permissions.types import (
    MergedPermissions,
    PermissionDecision,
    PermissionMode,
    PermissionRule,
    RuleDecision,
    RuleLayer,
    RuleScope,
    RuleSource,
)

if TYPE_CHECKING:
    from baozicode.config.schema import AppConfig
    from baozicode.tools.base import ToolCall


# L1 单例(模块级,无状态)
_L1 = DangerousCommandBlacklist()


def check(
    call: "ToolCall",
    ctx: MergedPermissions | None = None,
) -> PermissionDecision:
    """5 层防御流水线总入口。

    流水线:L1(黑名单)→ L2(沙箱)→ L3(规则)→ L4(mode)→ L5(用户)

    Args:
        call: 待检查的 ToolCall
        ctx: MergedPermissions(必传;L2/L3/L4 都依赖;为 None 时退化为
             只跑 L1,后面的层 fallthrough)

    Returns:
        第一个非 fallthrough 的决策;全 fallthrough 时返回 L4 兜底(默认 mode=default)
    """
    # L1: 硬拦截黑名单(无状态、零依赖、最快)
    decision = _L1.check(call)
    if decision.decision != "fallthrough":
        return decision

    # L2-L4: 需要 ctx
    if ctx is None:
        return PermissionDecision.fallthrough()

    # L2: 路径沙箱
    if ctx.path_sandbox_enabled:
        sandbox = PathSandbox(real_root=ctx.real_root)  # type: ignore[arg-type]
        decision = sandbox.check(call)
        if decision.decision != "fallthrough":
            return decision

    # L3: 规则引擎
    engine = RuleEngine(merged=ctx)
    decision = engine.check(call)
    if decision.decision != "fallthrough":
        return decision

    # L4: mode 兜底
    mode: PermissionMode = ctx.mode
    decision = apply_mode(PermissionDecision.fallthrough(), mode)
    if decision.decision != "fallthrough":
        return decision

    # 全 fallthrough → L5(用户)决策的占位:仍返回 fallthrough,
    # 由 Agent 层把这个 fallthrough 解释为"弹 Modal"信号
    return PermissionDecision.fallthrough()


def bootstrap(project_root, config: "AppConfig | None" = None) -> MergedPermissions:
    """启动时初始化合并权限状态。

    调用 loader 加载三层 YAML,返回 MergedPermissions 给 Agent / TUI 持有。
    """
    # 延迟 import:避免循环(loader → persistence → types ↔ permissions/__init__)
    from baozicode.permissions.loader import load_permissions_layers

    return load_permissions_layers(project_root)


__all__ = [
    "MergedPermissions",
    "PermissionDecision",
    "PermissionMode",
    "PermissionRule",
    "RuleDecision",
    "RuleLayer",
    "RuleScope",
    "RuleSource",
    "bootstrap",
    "check",
]