"""v0.9 命令注册 + 分发。

公开 API:
- `CommandRegistry` / `CommandDef` / `CommandType` — 元数据 + boot 校验
- `LocalResult` / `UiStateResult` / `PromptResult` — handler 返回联合类型
- `CommandContext` — handler 运行时接口(Protocol)
- `dispatch(input, ctx)` — slash 分流入口
- `build_builtin_defs(get_handler)` — 构造 10 个内置命令的 CommandDef

模块:
- `registry.py` — CommandDef + CommandRegistry
- `context.py`  — CommandContext Protocol
- `dispatcher.py` — parse_command + dispatch
- `completor.py` — Tab 补全
- `builtin.py`  — 10 个内置命令

依赖方向(单向):
    commands/  ─→  permissions/types.py  (type hint)
    commands/  ─→  llm/base.py           (UsageStats type hint)
    commands/  ─→  config/schema.py      (config property type)
    commands/  ─→  textual/screen.py     (push_modal type hint)

`tui/chat_screen.py` 是唯一真接 concrete `TextualCommandContext` 的地方。
"""

from __future__ import annotations

from baozicode.commands.context import CommandContext
from baozicode.commands.registry import (
    CommandDef,
    CommandRegistry,
    CommandResult,
    CommandType,
    LocalResult,
    PromptResult,
    UiStateResult,
)

__all__ = [
    "CommandContext",
    "CommandDef",
    "CommandRegistry",
    "CommandResult",
    "CommandType",
    "LocalResult",
    "UiStateResult",
    "PromptResult",
    "dispatch",
    "build_builtin_defs",
]


def __getattr__(name: str):
    """延迟 import — dispatcher / completor / builtin 都不强依赖。"""
    if name == "dispatch":
        from baozicode.commands.dispatcher import dispatch
        return dispatch
    if name == "build_builtin_defs":
        from baozicode.commands.builtin import build_builtin_defs
        return build_builtin_defs
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
