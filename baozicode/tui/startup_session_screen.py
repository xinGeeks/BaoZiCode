"""v0.8 启动时的 session 选择弹窗 — 与 `/resume` 的 SessionSelectScreen 类似,
但多了「开始新 session」选项,语义对齐 CLI 无参启动时的选择。

返回 `str | Literal["__new__" | None`:
- session_id (string) — 用户选了已有 session
- `"__new__"` — 用户主动选「开始新 session」
- `None` — Esc / 取消 / 选「保持当前 session」 → 保留默认行为(继续跑新会话)
"""

from __future__ import annotations

from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

StartupChoice = str | Literal["__new__"] | None
NEW_SESSION = "__new__"


class StartupSessionScreen(ModalScreen[StartupChoice]):
    """启动时弹窗 — 列已有 sessions + 「开始新 session」+ 「保持当前」三档。

    设计意图:CLI 无 --resume / --new 时,App.on_mount 调起,让用户在进
    ChatScreen 之前决定行为,避免无脑覆盖上次会话。
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        current_session_id: str,
        options: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self.current_session_id = current_session_id
        # options 可由 caller 注入;为空时 App.on_mount 之后从 self.app.sessions_list 拉
        self._options: list[tuple[str, str]] | None = options

    def compose(self) -> ComposeResult:
        yield Static("启动 session 选择（按 Esc 保持当前）", id="startup-session-title")
        with Vertical(id="startup-session-buttons"):
            options = self._options or self._build_options()
            for sid, label in options:
                if sid == self.current_session_id:
                    btn_label = f"● {label}  (当前)"
                    variant = "success"
                else:
                    btn_label = f"  {label}"
                    variant = "primary"
                yield Button(btn_label, id=f"select-{sid}", variant=variant)
            yield Button("开始新 session", id="new", variant="warning")
            yield Button("保持当前(取消)", id="cancel", variant="default")

    def on_mount(self) -> None:
        # 如果 options 为空,在 on_mount 时从 app.sessions_list 拉
        if self._options is None:
            try:
                app_sessions = self.app.sessions_list()  # type: ignore[attr-defined]
                self._options = [
                    (s.id, self._label_for(s.title, s.last_message_at, s.message_count))
                    for s in app_sessions
                ]
            except Exception:  # noqa: BLE001
                self._options = []

    def _build_options(self) -> list[tuple[str, str]]:
        """fallback(应该不会进) — 直接用 self._options。"""
        return self._options or []

    @staticmethod
    def _label_for(title: str | None, last_message_at, message_count: int) -> str:
        title = title or "(无标题)"
        short = title[:40] + ("…" if len(title) > 40 else "")
        date = last_message_at.strftime("%Y-%m-%d %H:%M")
        return f"{short}  ·  {date}  ·  {message_count} msg"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "cancel":
            self.dismiss(None)
        elif btn_id == "new":
            self.dismiss(NEW_SESSION)
        elif btn_id.startswith("select-"):
            self.dismiss(btn_id.removeprefix("select-"))

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["StartupSessionScreen", "StartupChoice", "NEW_SESSION"]
