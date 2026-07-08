"""v0.9 实时 Tab 补全。

`completor.candidates(prefix, registry) -> list[str]` 是单 match / 多 match 分支的来源:
- 0 match → 空列表(UI 弹 hint)
- 1 match → 长度 1 的列表(UI 自动补全)
- 2+ match → 长度 N 的列表(UI 弹菜单)

输入 prefix 规则:
- 空字符串 + 无前缀 → 返回所有非 hidden 命令的主名(按 register 顺序)
- 前缀以 `/` 开头 → 取 `/` 之后的部分作为匹配 key
- 大小写不敏感
- 隐藏命令 (hidden=True) 一律不参与

返回的 list 元素是 canonical primary name(不是 alias)——
UI 看到 `/permissions` 时,也归到 `permission` 主名,显示给用户。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from baozicode.commands.registry import CommandRegistry


def candidates(prefix: str, registry: "CommandRegistry") -> list[str]:
    """返回 prefix 命中的非 hidden 主名列表。

    Args:
        prefix: 用户当前在输入框里的字符串(含或不含前导 `/`)
        registry: 命令注册中心(必须已 freeze)

    Returns:
        命中的 command 主名 list,register 顺序。
    """
    key = prefix.lstrip("/").lower().strip()
    visible = registry.all_visible()
    if not key:
        # 空输入 → 全部可见命令
        return [d.name for d in visible]
    # prefix 命中:d.name.startswith(key) OR 任一 alias.startswith(key)
    out: list[str] = []
    seen: set[str] = set()
    for d in visible:
        if d.name.startswith(key) or any(a.startswith(key) for a in d.aliases):
            if d.name not in seen:
                out.append(d.name)
                seen.add(d.name)
    return out


def has_completable_space(input_text: str) -> bool:
    """判断 input 是否过了第一个空格(此时 Tab 补全不接管)。

    规则:
    - 含至少一个空格 → True(已进 args 区)
    - 否则 False(还在 command name 区,Tab 继续补全)
    """
    return " " in input_text


__all__ = ["candidates", "has_completable_space"]
