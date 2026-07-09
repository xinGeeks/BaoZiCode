"""Condition 求值:4 种 matcher + all/any 二选一。

- match_exact / match_glob / match_regex / 3 个 not_ 变种
- evaluate_condition(condition, call) → True/False
  - condition is None / 空 → True(无条件触发)
  - all:全部 match → True
  - any:任一 match → True
  - all + any 同时存在 → HookConditionError(理论上 freeze 阶段已拦住;run-time 防御)
"""
from __future__ import annotations

import fnmatch
import re
from typing import TYPE_CHECKING, Any, Callable

from baozicode.hooks._errors import HookConditionError

if TYPE_CHECKING:
    from baozicode.hooks.schema import ConditionYaml


# 4 + 3 个 matcher,kind → 函数
def _match_exact(value: str, pattern: str) -> bool:
    return value == pattern


def _match_glob(value: str, pattern: str) -> bool:
    return fnmatch.fnmatch(value, pattern)


def _match_regex(value: str, pattern: str) -> bool:
    try:
        return re.search(pattern, value) is not None
    except re.error:
        return False


matchers: dict[str, Callable[[str, str], bool]] = {
    "exact": _match_exact,
    "glob": _match_glob,
    "regex": _match_regex,
}

# not_ 前缀的 3 个变种
for _kind in ("not_exact", "not_glob", "not_regex"):
    _base = _kind.removeprefix("not_")
    matchers[_kind] = (lambda base: lambda v, p: not matchers[base](v, p))(_base)
del _kind, _base


def _matcher_to_value(call: Any, matcher: Any) -> bool:
    """单条 MatcherYaml 对单个 ToolCall 求值。"""
    # tool: 精确(无 regex / glob,精确才是 tool name 唯一身份)
    if matcher.tool is not None:
        if matcher.arg:
            # 同时配 tool + arg → run-time 防御,正常 freeze 阶段不挡
            # 但语义上两个都要通过才算命中
            pass
        if not _match_exact(call.name, matcher.tool):
            return False
    # arg.<name>: MatchValue
    for arg_name, match_value in matcher.arg.items():
        if arg_name not in call.arguments:
            return False  # 缺参数视为不命中(AND 下必拒,OR 下被前置 True 挡住)
        raw = call.arguments[arg_name]
        # 非 str 转 str 再匹配
        actual = raw if isinstance(raw, str) else str(raw)
        fn = matchers.get(match_value.kind)
        if fn is None:
            return False
        if not fn(actual, match_value.value):
            return False
    return True


def evaluate_condition(condition: "ConditionYaml | None", call: Any) -> bool:
    """求值 ConditionYaml → True / False。

    None 或 空 → True(无条件触发)。
    """
    if condition is None:
        return True
    all_list = condition.all or []
    any_list = condition.any or []
    if not all_list and not any_list:
        return True
    if all_list and any_list:
        raise HookConditionError(
            f"condition has both all and any; this should be caught at freeze()"
        )
    if all_list:
        return all(_matcher_to_value(call, m) for m in all_list)
    return any(_matcher_to_value(call, m) for m in any_list)


__all__ = ["evaluate_condition", "matchers"]
