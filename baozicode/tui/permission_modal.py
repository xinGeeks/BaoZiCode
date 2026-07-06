"""权限确认 Modal — 工具调用前询问用户。

两种模式:
- 默认:`ModalScreen[bool]` — Y 允许 / N 拒绝 / Esc 拒绝
- 批量:`ModalScreen[tuple[bool, bool]]` — 额外 "Allow all remaining" 按钮
"""

from __future__ import annotations

from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from baozicode.tools.base import ToolCall

Mode = Literal["single", "batch"]


def _format_args_short(args: dict, limit: int = 240) -> str:
    import json

    try:
        rendered = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = repr(args)
    if len(rendered) > limit:
        rendered = rendered[:limit] + "..."
    return rendered


class PermissionModal(ModalScreen[bool | tuple[bool, bool]]):
    """高风险工具调用前的确认弹窗。

    - `mode="single"`:dismiss True(允许)或 False(拒绝)
    - `mode="batch"`:dismiss (allow, batch_apply_all),batch_apply_all 为 True
      时 agent loop 记下"对剩下的同类型工具一律放行"
    """

    BINDINGS = [
        Binding("y", "allow", "Allow"),
        Binding("n", "deny", "Deny"),
        Binding("escape", "deny", "Cancel"),
    ]

    DEFAULT_CSS = """
    PermissionModal {
        align: center middle;
    }
    #modal-box {
        width: 70;
        height: auto;
        padding: 1 2;
        border: thick $warning;
        background: $surface;
    }
    #modal-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }
    #modal-body {
        margin-bottom: 1;
    }
    #modal-buttons {
        height: auto;
        align-horizontal: center;
    }
    Button {
        margin: 0 1;
    }
    """

    def __init__(self, call: ToolCall, *, mode: Mode = "single") -> None:
        super().__init__()
        self.call = call
        self.mode = mode

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-box"):
            yield Static(f"⚠ 调用 `{self.call.name}`?", id="modal-title")
            yield Static(
                f"参数:\n{_format_args_short(self.call.arguments)}",
                id="modal-body",
            )
            with Horizontal(id="modal-buttons"):
                yield Button("✓ 允许 (Y)", id="allow", variant="success")
                if self.mode == "batch":
                    yield Button("✓ 允许剩余全部", id="allow-all", variant="warning")
                yield Button("✗ 拒绝 (N)", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id or ""
        if btn == "allow":
            self.dismiss((True, False))
        elif btn == "allow-all":
            self.dismiss((True, True))
        elif btn == "deny":
            self.dismiss((False, False))

    def action_allow(self) -> None:
        self.dismiss((True, False))

    def action_deny(self) -> None:
        self.dismiss((False, False))


__all__ = ["PermissionModal", "Mode"]