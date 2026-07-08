"""v0.9 命令注册中心 — 元数据 + boot 校验。

公开 API 见 `__init__.py`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Union

# 命令名格式:小写字母 + 数字 + 中划线
_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")


class CommandType(str, Enum):
    """命令按执行模式分三类。

    - LOCAL: 纯本地操作,无回显(handler 主动调 ctx.show_info 才显示)
    - UI_STATE: 影响界面状态(plan_mode / session_id 等)
    - PROMPT: 把预设文本送进 Agent,经 Agent 完整流程处理
    """

    LOCAL = "local"
    UI_STATE = "ui_state"
    PROMPT = "prompt"


@dataclass(frozen=True)
class CommandDef:
    """单条命令的元数据 + handler。

    handler 必填,None 仅在 stub/placeholder 阶段允许(测试用)。
    """

    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    usage: str = ""
    type: CommandType = CommandType.LOCAL
    params_hint: str | None = None
    hidden: bool = False
    handler: Callable[..., Awaitable["CommandResult"]] | None = None


@dataclass(frozen=True)
class LocalResult:
    """LOCAL 类型命令的返回 — 纯本地操作完结,无 UI 副作用。"""


@dataclass(frozen=True)
class UiStateResult:
    """UI_STATE 类型命令的返回 — 改完界面状态(handler 内部已 ctx.refresh_status)。"""


@dataclass(frozen=True)
class PromptResult:
    """PROMPT 类型命令的返回 — text 将作为下一条 user 消息送给 Agent。"""

    text: str


# 联合类型(handler 签名用)
CommandResult = Union[LocalResult, UiStateResult, PromptResult]


class CommandRegistry:
    """命令注册中心。

    用法:
        reg = CommandRegistry()
        reg.register(CommandDef(name="foo", ...))
        reg.register(CommandDef(name="bar", ...))
        reg.freeze()              # 别名冲突 → SystemExit
        def_ = reg.lookup("foo")  # None 或 CommandDef
        visible = reg.all_visible()
    """

    def __init__(self) -> None:
        self._defs: list[CommandDef] = []
        self._index: dict[str, CommandDef] = {}
        self._frozen: bool = False

    def register(self, def_: CommandDef) -> None:
        """注册一条命令。freeze 后再 register 抛 RuntimeError。"""
        if self._frozen:
            raise RuntimeError(
                f"registry 已 freeze,不能再 register 新命令: {def_.name!r}"
            )
        self._validate_name(def_.name)
        for alias in def_.aliases:
            self._validate_name(alias)
        self._defs.append(def_)

    def freeze(self) -> None:
        """冻结并校验:别名 + 主名撞名则 SystemExit 退出。

        校验时把当前所有已注册的 def 名称 + 别名收集,任一字符串
        出现两次 → panic(列出冲突的双方名字)。
        """
        if self._frozen:
            return
        counts: dict[str, list[str]] = {}
        for d in self._defs:
            for key in (d.name, *d.aliases):
                counts.setdefault(key, []).append(d.name)
        collisions = {k: v for k, v in counts.items() if len(v) > 1}
        if collisions:
            # 拼 panic 消息 — 列每个冲突的 alias → 双方名字
            lines = []
            for alias, names in sorted(collisions.items()):
                lines.append(f"  {alias!r} -> {' / '.join(sorted(set(names)))}")
            raise SystemExit(
                "command registry alias collision:\n" + "\n".join(lines)
            )
        # 构造只读索引
        for d in self._defs:
            self._index[d.name] = d
            for alias in d.aliases:
                self._index[alias] = d
        self._frozen = True

    def lookup(self, name: str) -> CommandDef | None:
        """大小写不敏感地查命令。

        freeze 前也能查(直接线性扫描),freeze 后 O(1) 走索引。
        """
        key = name.lower()
        if self._frozen:
            return self._index.get(key)
        # freeze 前:线性扫描(开发期友好,生产代码不允许未 freeze)
        for d in self._defs:
            if d.name == key:
                return d
            if key in d.aliases:
                return d
        return None

    def all_visible(self) -> list[CommandDef]:
        """返回所有非 hidden 的命令,保持 register 顺序。"""
        return [d for d in self._defs if not d.hidden]

    def all_registered(self) -> list[CommandDef]:
        """返回全部命令(含 hidden),保持 register 顺序。"""
        return list(self._defs)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _NAME_RE.match(name):
            raise ValueError(
                f"命令名不合法 (需 ^[a-z][a-z0-9-]*$): {name!r}"
            )


__all__ = [
    "CommandType",
    "CommandDef",
    "LocalResult",
    "UiStateResult",
    "PromptResult",
    "CommandResult",
    "CommandRegistry",
]
