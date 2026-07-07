"""权限确认 Modal — v0.5 升级到四档选择 + 持久化。

v0.5 改变:
- `dismiss` 返回 `PermissionChoice` 枚举(ONCE / SESSION / PERSISTENT / DENY)
- 按钮组:[Y 仅本次] [A 本会话] [P 永久] [N 拒绝]
- `previously_denied=True` 时标题后缀加 "(previously denied)"
- 在 Modal 主体展示"将要存储的 glob 模糊 pattern" — 让用户在按
  持久化按钮前能看到自动生成的规则

设计取舍:
- 不在 Modal 内做持久化 — Modal 只汇报"用户选了哪一档",
  由 ChatScreen._permission_callback 统一调 `engine.add_session_rule`
  或 `persistence.append_rule_to_local_yaml`
- "本次"和"本会话"区别:本会话 = engine.add_session_rule(覆盖整个 App 生命周期)
  本次 = 仅这一次 call 放行(返回 True 但不存任何 rule)
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from baozicode.tools.base import ToolCall


class PermissionChoice(str, Enum):
    """Modal 的四档返回。"""

    ONCE = "once"           # 仅本次放行
    SESSION = "session"     # 本会话放行(写 RuleEngine session_rules)
    PERSISTENT = "persistent"  # 永久放行(写 permissions.local.yaml)
    DENY = "deny"           # 拒绝


Mode = Literal["single", "batch"]


def _format_args_short(args: dict, limit: int = 240) -> str:
    try:
        rendered = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = repr(args)
    if len(rendered) > limit:
        rendered = rendered[:limit] + "..."
    return rendered


def derive_glob_pattern(call: ToolCall) -> str:
    """为 v0.5 持久化按钮自动生成 glob 模糊 pattern。

    规则(简化版):
    - 拆出 command 头 1-2 个 token
    - 若 2nd token 不以 `-` 开头(看起来像子命令),保留两个 token
    - 否则只保留第一个 token
    - 末尾追加 ` *`

    示例:
    - Bash("git status")          → "git status *"
    - Bash("npm test --coverage")  → "npm test *"
    - Bash("pytest -v")            → "pytest *"
    - Bash("pytest tests/")        → "pytest tests/ *"
    - Bash("ls")                   → "ls *"

    非 Bash 工具(Read/Write/Edit 等):用 file_path 第一段作为 pattern。
    """
    if call.name == "Bash":
        command = str(call.arguments.get("command", "")).strip()
        if not command:
            return "*"
        tokens = command.split()
        if len(tokens) == 1:
            return f"{tokens[0]} *"
        # 2nd token 不是 flag → 当作子命令保留
        if not tokens[1].startswith("-"):
            return f"{tokens[0]} {tokens[1]} *"
        return f"{tokens[0]} *"

    # 其他工具:从首个 str 参数(通常是 file_path)提取第一段
    for v in call.arguments.values():
        if isinstance(v, str) and v:
            # 取第一段路径(去掉目录前缀)
            base = v.replace("\\", "/").split("/")[-1] or v
            return f"{base}"
    return "*"


class PermissionModal(ModalScreen[PermissionChoice]):
    """高风险工具调用前的确认弹窗(v0.5:四档选择 + 持久化)。"""

    BINDINGS = [
        Binding("y", "allow_once", "Allow once"),
        Binding("a", "allow_session", "Allow session"),
        Binding("p", "allow_persistent", "Allow persistent"),
        Binding("n", "deny", "Deny"),
        Binding("escape", "deny", "Cancel"),
    ]

    DEFAULT_CSS = """
    PermissionModal {
        align: center middle;
    }
    #modal-box {
        width: 80;
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
    #modal-pattern {
        margin-bottom: 1;
        color: $text-muted;
    }
    #modal-buttons {
        height: auto;
        align-horizontal: center;
    }
    Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        call: ToolCall,
        *,
        mode: Mode = "single",
        previously_denied: bool = False,
    ) -> None:
        super().__init__()
        self.call = call
        self.mode = mode
        self.previously_denied = previously_denied
        # 预计算 glob pattern(给 PERSISTENT 按钮的 info card 用)
        self._derived_pattern = derive_glob_pattern(call)

    def compose(self) -> ComposeResult:
        suffix = " (previously denied)" if self.previously_denied else ""
        with Vertical(id="modal-box"):
            yield Static(
                f"⚠ 调用 `{self.call.name}`?{suffix}",
                id="modal-title",
            )
            yield Static(
                f"参数:\n{_format_args_short(self.call.arguments)}",
                id="modal-body",
            )
            yield Static(
                f"按 [P 永久] 将存储 glob pattern: `{self._derived_pattern}`",
                id="modal-pattern",
            )
            with Horizontal(id="modal-buttons"):
                yield Button("✓ 仅本次 (Y)", id="allow-once", variant="success")
                yield Button("✓ 本会话 (A)", id="allow-session", variant="primary")
                yield Button("✓ 永久 (P)", id="allow-persistent", variant="warning")
                yield Button("✗ 拒绝 (N)", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id or ""
        if btn == "allow-once":
            self.dismiss(PermissionChoice.ONCE)
        elif btn == "allow-session":
            self.dismiss(PermissionChoice.SESSION)
        elif btn == "allow-persistent":
            self.dismiss(PermissionChoice.PERSISTENT)
        elif btn == "deny":
            self.dismiss(PermissionChoice.DENY)

    def action_allow_once(self) -> None:
        self.dismiss(PermissionChoice.ONCE)

    def action_allow_session(self) -> None:
        self.dismiss(PermissionChoice.SESSION)

    def action_allow_persistent(self) -> None:
        self.dismiss(PermissionChoice.PERSISTENT)

    def action_deny(self) -> None:
        self.dismiss(PermissionChoice.DENY)


__all__ = ["Mode", "PermissionChoice", "PermissionModal", "derive_glob_pattern"]
