"""v1.0 Skills — 工具白名单双层防御。

公开 API 见 `__init__.py`。

L1(静态)  `validate_declared_tools()`  — boot / 加载时:SkillFrontmatter.allowed-tools
   声明的工具名必须存在于 ToolRegistry,缺则立即 ValueError,阻止错的 Skill
   进入 active 集合。

L2(动态)  `SkillWhitelistFilter.is_allowed(call)`  — 每个 tool_call 之前:
   - 没有任何 Skill 激活 → 放行(无 Skill 限制)
   - 全部激活 Skill 的 allowed_tools 都是 None/未声明 → 放行(无限制)
   - 至少一个声明了 allowed_tools → 取 union;
     tool 名不在 union 内 → 拒;但 `tool_type="internal"` 豁免(load_skill)

整体设计:Skill 是收窄(L1+L2 同时收紧),不是放宽 ——
v0.5 五层防御在 Skill 允许的集合上继续生效。
"""

from __future__ import annotations

import logging

from baozicode.skills.activation import SkillActivation
from baozicode.tools.base import ToolCall
from baozicode.tools.registry import ToolRegistry

_log = logging.getLogger(__name__)

__all__ = ["SkillWhitelistFilter", "validate_declared_tools"]


def validate_declared_tools(
    allowed_tools: list[str] | None,
    tool_registry: ToolRegistry,
    *,
    skill_name: str,
) -> None:
    """L1:校验 Skill 声明的工具名都在 ToolRegistry 内。

    Args:
        allowed_tools: SkillFrontmatter.allowed_tools 列表(None = 未声明,不校验)
        tool_registry: 当前 ToolRegistry 实例
        skill_name: 用于错误消息的 Skill 名

    Raises:
        ValueError: 任一声明的工具不在 registry 中(含详细缺失列表 + 可用列表)
    """
    if not allowed_tools:
        return
    available = set(tool_registry.get_tool_names())
    missing = [t for t in allowed_tools if t not in available]
    if missing:
        raise ValueError(
            f"Skill {skill_name!r} 声明的 allowed-tools 有未注册工具: "
            f"{missing!r}; "
            f"可用工具: {sorted(available)!r}"
        )


class SkillWhitelistFilter:
    """L2:每次 tool_call 之前过 Skill 白名单守卫。

    Usage:
        filt = SkillWhitelistFilter(activation, tool_registry)
        if not filt.is_allowed(call):
            return ToolResult.error_result(call.id, "...")
    """

    def __init__(
        self,
        activation: SkillActivation,
        tool_registry: ToolRegistry,
    ) -> None:
        self._activation = activation
        self._tool_registry = tool_registry

    def is_allowed(self, call: ToolCall) -> bool:
        """判断该 ToolCall 是否被当前激活的 Skill 白名单放行。

        规则(按顺序短路):
        1. _active 为空 → True(没有 Skill 限制)
        2. _active 全部都没有 declared allowed_tools → True
        3. tool_def.tool_type == "internal" → True(系统级永远可用)
        4. call.name 在 union(所有 active Skill 的 declared allowed_tools)→ True
        5. 其它 → False
        """
        # 1) 无激活 Skill → 不限制
        if not self._activation.active_names():
            return True

        # 2) 收集 union + 检测是否有人声明了限制
        allowed: set[str] = set()
        for entry in self._activation._active.values():
            allowed.update(entry.allowed_tools)
        if not allowed:
            # 所有激活 Skill 都没声明 allowed_tools → 不限制
            return True

        # 3) internal 工具豁免(load_skill)
        tool_def = self._tool_registry.get_tool(call.name)
        if tool_def is not None and tool_def.tool_type == "internal":
            return True

        # 4) 在 union 内 → 通过
        if call.name in allowed:
            return True

        # 5) 拒绝
        _log.info(
            "tool %r 被 Skill 白名单拦截:不在 union %r 内",
            call.name, sorted(allowed),
        )
        return False

    def active_declared_tools(self) -> set[str]:
        """当前所有 active Skill 声明的 allowed-tools union(测试 + 调试用)。"""
        out: set[str] = set()
        for entry in self._activation._active.values():
            out.update(entry.allowed_tools)
        return out
