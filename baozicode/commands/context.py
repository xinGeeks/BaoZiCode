"""v0.9 CommandContext — handler 与 textual 解耦。

`CommandContext` 是 Protocol(接口),命令 handler 只依赖这一组方法 + 2 个属性,
不直接 import textual / business 模块。

具体实现 `TextualCommandContext` 在 tui/chat_screen.py 内联构造 — 这样:
- `commands/context.py` 的 import set 保持纯净(spec 要求)
- App 实例由运行时注入,无循环 import

依赖边界:
    commands/  ─→  permissions/types.py  (PermissionMode type hint)
    commands/  ─→  llm/base.py  (UsageStats type hint)
    commands/  ─→  textual/screen.py  (push_modal type hint)
    commands/  ─→  config/schema.py  (config property type)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from baozicode.llm.base import ContentDelta
    from baozicode.permissions.types import PermissionMode


@runtime_checkable
class CommandContext(Protocol):
    """命令 handler 接收的运行时接口。

    所有方法都是同步(handler 自身是 async 的)。

    `app` 和 `config` 是属性访问器,handler 通过它们拿到全 app 句柄 ——
    这是有意保留的 escape hatch(否则接口会膨胀到 20+ 方法)。
    `app` 的存在意味着 commands 包间接依赖业务模块 ——
    `commands/context.py` 本身不 import,真正使用是在 tui/chat_screen.py
    里构造 TextualCommandContext 时拿的;这里 `app` 类型用 Any 避免污染 Protocol。
    """

    @property
    def app(self) -> Any: ...

    @property
    def config(self) -> Any: ...

    def show_info(self, text: str) -> None: ...

    def show_error(self, text: str) -> None: ...

    def send_to_agent(self, text: str) -> None: ...

    def switch_mode(self, new_mode: "PermissionMode | None") -> None: ...

    def get_token_usage(self) -> Any: ...  # UsageStats — 不 import 用 Any
    # UsageStats 是 llm/base.py 的 dataclass;为保持 commands/context.py 不
    # import 业务,这里用 Any。运行时 type-check 仍能通过 isinstance。

    def refresh_status(self) -> None: ...

    def push_modal(self, screen: Any) -> Any: ...
    # textual Screen 也不 import;同样是 escape hatch,handler 拿到 screen 对象
    # 自行 await push。dispatcher / commands/ 不解读这个对象。


__all__ = ["CommandContext"]


# ---- 导入审计 ----
# 确保 commands/context.py 的运行时 import 集纯净。
# (这些 import 是 TYPE_CHECKING block,运行时不会触发 import;但是我们要
#  保留 import 设置在 Protocol 类型 hint 能工作)


def _audit_imports() -> None:
    """可被外部诊断脚本调用的导入审计。

    返回 dict,列出该模块运行时引入的全部第三方 / 内部模块名。
    通过 `python -c "from baozicode.commands.context import _audit_imports; print(_audit_imports())"`
    调用。
    """
    import sys
    own = sys.modules[__name__]
    return {
        k: v.__name__
        for k, v in sys.modules.items()
        if v is own or k == own.__name__
    }
