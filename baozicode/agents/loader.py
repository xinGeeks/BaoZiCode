"""v1.2 SubAgent Delegation — 占位符替换。

公开 API:
- `substitute_placeholders(body, args)` — 把 `{var}` / `{var:default}` 替换为 args 值
- `MissingPlaceholderError` — 必填变量缺值时抛出

格式约定(跟 Skill v1.0 一致):
- `{var}` — 必填,args 缺 key → raise MissingPlaceholderError
- `{var:default}` — 可选,args 缺 key → 用 default
- 转义:`{{` 渲染成 `{`,`}}` 渲染成 `}`
"""

from __future__ import annotations

import re

__all__ = ["MissingPlaceholderError", "substitute_placeholders"]


class MissingPlaceholderError(KeyError):
    """必填 `{var}` 在 args 里缺值时抛出(可读性更好的 KeyError 子类)。"""


# 模式分两组:
# 1. `{{` 或 `}}`(转义)
# 2. `{var}` 或 `{var:default}` — var 是 [a-zA-Z_][a-zA-Z0-9_]*
_PLACEHOLDER_RE = re.compile(
    r"(\{\{|\}\})|\{([a-zA-Z_][a-zA-Z0-9_]*)(?::([^}]*))?\}"
)


def substitute_placeholders(
    body: str,
    args: dict[str, str] | None,
) -> str:
    """替换 body 里的 `{var}` / `{var:default}` 占位符。

    Args:
        body: 含占位符的原始文本
        args: 变量名 → 替换值。`None` 视为 `{}`

    Returns:
        替换后的文本

    Raises:
        MissingPlaceholderError: 必填 `{var}` 在 args 里缺值
    """
    if args is None:
        args = {}

    def repl(m: re.Match[str]) -> str:
        if m.group(1):  # 转义 `{{` / `}}`
            return m.group(1)[0]  # 单字符 `{` 或 `}`
        var_name = m.group(2)
        default = m.group(3)  # 可能为 None(无 `:default`)
        if var_name in args:
            return args[var_name]
        if default is not None:
            return default
        raise MissingPlaceholderError(
            f"占位符 {{{var_name}}} 缺少值(args={sorted(args.keys())})"
        )

    return _PLACEHOLDER_RE.sub(repl, body)
