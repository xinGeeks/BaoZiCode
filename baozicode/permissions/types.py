"""权限系统核心数据类型(v0.5)。

`PermissionDecision` 是 5 层防御流水线的统一返回值。
`PermissionRule` 是 L3 规则引擎的单条规则表示。
`RuleLayer` 标识规则来自哪一层(决定优先级)。
`PermissionMode` 是 L4 的三档信任级别。

数据模型保持 dataclass + Literal,避免 Pydantic 引入额外开销
(规则列表常驻内存,per-call 实例化代价敏感)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# 5 层防御的命名 + 兜底"none"
RuleLayer = Literal[
    "L1_blacklist",  # 硬拦截黑名单
    "L2_sandbox",    # 路径沙箱
    "L3_rule",       # 规则引擎
    "L4_mode",       # 权限模式
    "L5_user",       # 人在回路
    "none",          # 兜底(全部 fallthrough)
]

# 规则来源层(L3 内部用,标识规则从哪里加载)
RuleSource = Literal[
    "session",         # 运行时内存(session 级)
    "local",           # <project>/.baozicode/permissions.local.yaml
    "project",         # <project>/.baozicode/permissions.yaml
    "user_global",     # ~/.config/baozicode/permissions.yaml
]

# 单条规则的判定结果
RuleDecision = Literal["allow", "deny"]

# 三档权限模式
PermissionMode = Literal["strict", "default", "permissive"]

# 人在回路放行的生命周期范围
RuleScope = Literal["once", "session", "persistent"]


@dataclass
class PermissionRule:
    """L3 规则引擎的单条规则。

    `tool` 必须精确匹配 ToolCall.name。
    `pattern` 用 fnmatch glob 匹配 ToolCall.arguments 中任一字符串值。
    `decision` 决定匹配后的处置。
    `source` 标识该规则从哪一层加载(用于调试和 L3 优先级判定)。
    """

    tool: str
    pattern: str
    decision: RuleDecision
    source: RuleSource = "user_global"


@dataclass
class PermissionDecision:
    """5 层防御流水线的统一返回。

    `decision`:
    - "allow"        — 放行,执行工具
    - "deny"         — 拒绝,is_error 喂回 LLM
    - "fallthrough"  — 本层无意见,继续下一层(最终兜底为 deny)

    `layer`: 哪个层产生了这个决策(给 UI 展示 / 给 LLM 解释)
    `reason`: 人类可读的解释,deny 时作为 ToolResult.content 喂回 LLM
    `matched_pattern`: 命中的规则 pattern / 正则 / glob,调试用
    `scope`: 仅在 L5 时有 once/session/persistent 区分
    """

    decision: Literal["allow", "deny", "fallthrough"]
    layer: RuleLayer
    reason: str = ""
    matched_pattern: str | None = None
    scope: RuleScope = "once"

    @classmethod
    def fallthrough(cls) -> "PermissionDecision":
        """构造一个 fallthrough 占位决策。"""
        return cls(decision="fallthrough", layer="none", reason="")


@dataclass
class MergedPermissions:
    """加载后的合并权限状态(由 loader 返回,Agent 持有)。

    `rules`: 所有层合并后的规则列表(已按 source 标注)
    `mode`: 当前生效的 PermissionMode(优先级: session > local > project > user_global > default)
    `sources_loaded`: 实际加载到的 YAML 路径列表(用于 /permissions 展示)
    `real_root`: L2 PathSandbox 的项目根(已 resolve symlink)
    `path_sandbox_enabled`: L2 是否启用(可在 project YAML 显式禁用)
    `session_rules`: 运行时由 SESSION Modal 放行累积的 session 级规则(内存中)
    """

    rules: list[PermissionRule] = field(default_factory=list)
    mode: PermissionMode = "default"
    sources_loaded: list[str] = field(default_factory=list)
    real_root: object = None  # Path 类型,延迟 import 避免循环
    path_sandbox_enabled: bool = True
    session_rules: list[PermissionRule] = field(default_factory=list)


__all__ = [
    "MergedPermissions",
    "PermissionDecision",
    "PermissionMode",
    "PermissionRule",
    "RuleDecision",
    "RuleLayer",
    "RuleScope",
    "RuleSource",
]