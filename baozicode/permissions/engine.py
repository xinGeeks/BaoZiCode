"""L3 RuleEngine — 规则引擎(v0.5)。

核心职责:
- 合并 `session_rules`(内存)+ `merged.rules`(从 YAML 加载)
- 按 source 优先级扫描:**session → local → project → user_global**
- 同层内按声明顺序扫描
- `deny` 命中即短路(优先级最高,无可争议)
- `allow` 作为"候选"继续扫描,若后续层无 deny 才生效
- 没规则命中 → fallthrough(交给 L4 mode / L5 user)

匹配规则:
- `pattern` 用 `fnmatch.fnmatch` 对每个 str 值做 glob 匹配
  (支持 `*` / `?` / `[seq]` / `[!seq]`,** 不支持 ** `**` 双星号递归)
- 工具名必须精确匹配(非 glob)
- 任一 argument 值命中 pattern → 整条 call 匹配

设计取舍:
- `fnmatch` 而非 regex:用户写规则更简单,90% 场景够用
- 同 (tool, pattern) 多次出现时,声明顺序在前者胜出(去重留前者)
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from baozicode.permissions.types import (
    MergedPermissions,
    PermissionDecision,
    PermissionRule,
    RuleLayer,
    RuleSource,
)


# RuleSource 优先级(数字越小越优先)
_SOURCE_PRIORITY: dict[RuleSource, int] = {
    "session": 0,
    "local": 1,
    "project": 2,
    "user_global": 3,
}


@dataclass
class _ScoredRule:
    """内部用:rule + 优先级(用于稳定排序)。"""
    rule: PermissionRule
    priority: int  # 越小越优先
    index: int     # 同优先级内,声明顺序


class RuleEngine:
    """L3 规则引擎 — 检查 ToolCall 是否命中已知规则。

    用法:
        engine = RuleEngine(merged_permissions)
        decision = engine.check(call)
        if decision.decision == "deny":
            # 拦截
        elif decision.decision == "allow":
            # 放行
        else:
            # fallthrough,交给 L4/L5
    """

    def __init__(self, merged: MergedPermissions | None = None):
        self.merged = merged or MergedPermissions()
        # v0.5 设计:session_rules 实际存在 self.merged.session_rules 上,
        # 这样 `permissions.check(call, ctx)` 每次构造新 engine 也能看到
        # 累积的 SESSION 放行规则(否则 /clear 之前的 session 规则会丢)。

    # ---- 增删改 ----

    def add_session_rule(self, rule: PermissionRule) -> None:
        """新增一条 session 级规则(运行时,内存,随进程结束)。

        v0.5:实际写入 `self.merged.session_rules`,而非引擎私有列表 —
        以便跨 check() 调用的 engine 实例都能看到。
        """
        # 用 object.__setattr__ 触发 frozen-like 行为(PermissionRule 是
        # 普通 dataclass,直接赋值即可;这里 source 字段是 str Literal)
        object.__setattr__(rule, "source", "session")
        self.merged.session_rules.append(rule)

    def set_merged(self, merged: MergedPermissions) -> None:
        """整体替换 merged(测试 / 重新加载时用)。"""
        self.merged = merged

    # ---- 匹配 ----

    @staticmethod
    def _args_text_values(arguments: dict) -> list[str]:
        """把 arguments 拍平成 str 列表(fnmatch 只能对 str 匹配)。"""
        out: list[str] = []
        for v in arguments.values():
            if isinstance(v, str):
                out.append(v)
        return out

    @staticmethod
    def _matches_tool(rule: PermissionRule, tool_name: str) -> bool:
        return rule.tool == tool_name

    @staticmethod
    def _matches_pattern(rule: PermissionRule, args_values: list[str]) -> bool:
        """任一 argument str 值命中 pattern 即匹配。"""
        for v in args_values:
            if fnmatch.fnmatch(v, rule.pattern):
                return True
        return False

    def _collect_sorted(self) -> list[_ScoredRule]:
        """合并 session + merged.rules,按 (priority, index) 排序。

        v0.5:session 规则从 `self.merged.session_rules` 读取,确保跨
        engine 实例的累积。
        """
        all_rules: list[_ScoredRule] = []
        # session(从 merged 上读)
        for idx, r in enumerate(self.merged.session_rules):
            all_rules.append(_ScoredRule(r, _SOURCE_PRIORITY["session"], idx))
        # merged.rules(已标注 source)
        for idx, r in enumerate(self.merged.rules):
            prio = _SOURCE_PRIORITY.get(r.source, 99)
            all_rules.append(_ScoredRule(r, prio, idx))
        all_rules.sort(key=lambda x: (x.priority, x.index))
        return all_rules

    def check(self, call) -> PermissionDecision:
        """主入口 — 检查 ToolCall 是否命中已知规则。

        返回:
        - PermissionDecision(deny) — 命中 deny 规则
        - PermissionDecision(allow) — 命中 allow 规则,且无 deny 覆盖
        - PermissionDecision(fallthrough) — 没匹配
        """
        args_values = self._args_text_values(call.arguments)
        sorted_rules = self._collect_sorted()

        # 第一遍:找 deny(最高优先级,短路)
        for scored in sorted_rules:
            r = scored.rule
            if r.decision != "deny":
                continue
            if not self._matches_tool(r, call.name):
                continue
            if not self._matches_pattern(r, args_values):
                continue
            return PermissionDecision(
                decision="deny",
                layer="L3_rule",
                reason=(
                    f"规则拒绝: {r.tool}({r.pattern!r}) [{r.source}]"
                ),
                matched_pattern=r.pattern,
            )

        # 第二遍:找 allow(继续扫描确认没 deny 后生效)
        for scored in sorted_rules:
            r = scored.rule
            if r.decision != "allow":
                continue
            if not self._matches_tool(r, call.name):
                continue
            if not self._matches_pattern(r, args_values):
                continue
            return PermissionDecision(
                decision="allow",
                layer="L3_rule",
                reason=(
                    f"规则放行: {r.tool}({r.pattern!r}) [{r.source}]"
                ),
                matched_pattern=r.pattern,
            )

        return PermissionDecision.fallthrough()

    # ---- 调试 ----

    def session_rule_count(self) -> int:
        return len(self.merged.session_rules)

    def merged_rule_count(self) -> int:
        return len(self.merged.rules)

    def list_all(self) -> list[PermissionRule]:
        """返回所有规则(按评估顺序),调试 / /permissions 展示用。"""
        return [s.rule for s in self._collect_sorted()]


__all__ = ["RuleEngine"]
