"""v1.2 SubAgent Delegation — sub-Agent 工具过滤(4 层 AND)。

公开 API:
- `ToolFilter(role_def, is_background, background_whitelist, all_tools)`
  — 算 sub-Agent 的可见工具集
- `visible_tools` (cached_property) — 过滤后的 ToolDefinition 列表
- `ToolFilterEmptyError` — 过滤后空集时抛出
- `GLOBAL_DENY` — 硬黑名单(`task` 工具,防 sub-Agent 递归 spawn)

四层过滤顺序(从外到内):
1. **L1 — Global deny**: `task` 工具永远屏蔽(防嵌套 + 防 sub-Agent 自派)
2. **L2 — Role allow**(`role_def.tools`): 白名单,None = 全放行
3. **L3 — Role deny**(`role_def.tools_deny`): 黑名单,优先级高于 L2
4. **L4 — Background whitelist**(`is_background=True` 时): 仅放行
   `background_whitelist` 列出的工具(默认 Read/Grep/Glob/WebFetch/notify_complete)

任一层让集合变空就抛 `ToolFilterEmptyError`,消息包含所有层状态。
"""

from __future__ import annotations

from functools import cached_property

from baozicode.agents.schema import AgentDef
from baozicode.tools.base import ToolDefinition

# 硬黑名单:L1 永远过滤掉 `task` 工具(防 sub-Agent 自派发,递归死循环)。
# v1.2 物理禁止嵌套 — 任何角色(包括 builtin)拿不到 task 工具。
GLOBAL_DENY: frozenset[str] = frozenset({"task"})


class ToolFilterEmptyError(Exception):
    """ToolFilter 过滤后空集时抛出。

    消息格式:`visible_tools 为空: {layer_states}` —
    SubAgentManager 看到这异常,async 路径直接 `failed` task,
    sync 路径用 error text 喂回主 LLM。
    """

    def __init__(self, message: str, layer_states: dict[str, object]) -> None:
        super().__init__(message)
        self.layer_states = layer_states


class ToolFilter:
    """sub-Agent 工具过滤 — 4 层 AND。

    Args:
        role_def: 定义式 sub-Agent 的 AgentDef(fork 模式传 None —
            fork 继承父 Agent 工具集,不再过滤)
        is_background: True = 走 L4 background whitelist
        background_whitelist: config.subagents.background_whitelist 列表
        all_tools: 父 Agent 的全部可见 ToolDefinition 列表
            (来自 `ToolRegistry.get_all_tools()` 或 Agent.available_tools)
    """

    def __init__(
        self,
        role_def: AgentDef | None,
        *,
        is_background: bool,
        background_whitelist: list[str],
        all_tools: list[ToolDefinition],
    ) -> None:
        self._role_def = role_def
        self._is_background = is_background
        self._background_whitelist = set(background_whitelist)
        self._all_tools = list(all_tools)
        # v1.5:跟踪 L2 是否显式声明空工具集(role.tools == [] 而非 None)。
        # visible_tools 空集时,显式空 = 合法放行;否则 = 配置冲突报错。
        self._l2_explicit_empty: bool = False

    @cached_property
    def visible_tools(self) -> list[ToolDefinition]:
        """过滤后的可见工具列表(顺序与 all_tools 一致)。

        抛 `ToolFilterEmptyError`:4 层过滤后空集,且 L2 不是显式空。
        L2 显式空(`role.tools == []`)→ 返回空 list,合法。
        """
        # 第 0 层:fork 模式不调用 ToolFilter(继承父工具)。
        # 调用方负责判断 type,这里仅防御性校验。
        if self._role_def is None and not self._is_background:
            # 纯 fork 模式 + 非后台 → 仍走全部工具(不收窄)
            tools = list(self._all_tools)
        else:
            tools = list(self._all_tools)

        # ---- L1: GLOBAL_DENY ----
        l1_after = [t for t in tools if t.name not in GLOBAL_DENY]

        # ---- L2: role.tools(白名单)
        #   - None = 无约束(全放行)
        #   - []   = 显式空(角色主动声明"我要空工具集",允许空集返回)
        #   - [..] = 白名单
        role = self._role_def
        if role is not None and role.tools is not None:
            allow = set(role.tools)
            self._l2_explicit_empty = (allow == set())
            l2_after = [t for t in l1_after if t.name in allow]
        else:
            l2_after = l1_after

        # ---- L3: role.tools_deny(黑名单)----
        if role is not None and role.tools_deny is not None:
            deny = set(role.tools_deny)
            l3_after = [t for t in l2_after if t.name not in deny]
        else:
            l3_after = l2_after

        # ---- L4: background whitelist ----
        if self._is_background:
            wl = self._background_whitelist
            l4_after = [t for t in l3_after if t.name in wl]
        else:
            l4_after = l3_after

        # 防御:L1 全局黑名单在 L4 之后再覆盖一次(防 background_whitelist
        # 误把 task 加进去)
        final = [t for t in l4_after if t.name not in GLOBAL_DENY]

        if not final:
            # v1.5:L2 显式空 = 合法声明,放行(角色主动声明"我要空工具集")
            if self._l2_explicit_empty:
                return []
            # 否则 = 配置冲突,报错
            states = self.layer_states
            raise ToolFilterEmptyError(
                f"visible_tools 为空: {states}",
                layer_states=states,
            )
        return final

    @property
    def layer_states(self) -> dict[str, object]:
        """返回每层过滤后的状态(供 ToolFilterEmptyError 消息 + 调试用)。"""
        role = self._role_def
        return {
            "L1_global_deny": sorted(GLOBAL_DENY),
            "L2_role_tools": (
                sorted(role.tools) if role is not None and role.tools else None
            ),
            "L2_explicit_empty": self._l2_explicit_empty,
            "L3_role_tools_deny": (
                sorted(role.tools_deny)
                if role is not None and role.tools_deny
                else None
            ),
            "L4_background_whitelist": (
                sorted(self._background_whitelist) if self._is_background else None
            ),
            "role": role.name if role else None,
            "is_background": self._is_background,
        }


__all__ = [
    "GLOBAL_DENY",
    "ToolFilter",
    "ToolFilterEmptyError",
]