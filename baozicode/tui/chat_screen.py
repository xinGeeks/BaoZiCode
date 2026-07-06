"""主对话屏幕 — 含 agent loop、工具卡片、权限弹窗、slash 命令。"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Markdown, Static

from baozicode.config.schema import BackendName
from baozicode.llm.base import Message, TextBlock, ToolUseBlock
from baozicode.tui.banner import BAOZI_BANNER, WELCOME_TEMPLATE
from baozicode.tui.permission_modal import PermissionModal
from baozicode.tui.tool_card import ToolCallCard, ToolResultCard

if TYPE_CHECKING:
    from baozicode.app import BaoZiCodeApp

SLASH_COMMANDS = ("/help", "/clear", "/exit", "/model", "/tools", "/permissions")


class ModelSelectScreen(ModalScreen[BackendName | None]):
    """`/model` 触发的后端切换弹窗（4 选项列表）。

    返回选中的后端名；按 Esc 或选 "取消" 返回 None。
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        current_backend: BackendName,
        options: list[tuple[BackendName, str]],
    ) -> None:
        super().__init__()
        self.current_backend = current_backend
        self.options = options

    def compose(self) -> ComposeResult:
        yield Static("选择 LLM 后端（按 Esc 取消）", id="model-title")
        with Vertical(id="model-buttons"):
            for backend, model in self.options:
                if backend == self.current_backend:
                    label = f"● {backend} · {model}  (当前)"
                    variant = "success"
                else:
                    label = f"  {backend} · {model}"
                    variant = "primary"
                yield Button(label, id=f"select-{backend}", variant=variant)
            yield Button("取消", id="cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "cancel":
            self.dismiss(None)
        elif btn_id.startswith("select-"):
            self.dismiss(btn_id.removeprefix("select-"))  # type: ignore[arg-type]

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChatScreen(Screen):
    """通过 `app.push_screen` 装载的主屏幕。"""

    def __init__(self) -> None:
        super().__init__()
        self.is_streaming = False
        self.is_tool_executing = False
        self._current_assistant_widget: Markdown | None = None
        self._batch_allow_for: str | None = None
        self._consecutive_tool_name: str | None = None
        self._consecutive_tool_count: int = 0
        self._tools_cache: list | None = None

    # ---- layout ----

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-scroll"):
            yield Static(BAOZI_BANNER, id="banner")
            yield Static(id="welcome", classes="info-message")
        yield Input(
            placeholder="输入消息后回车发送（/help 查看命令）",
            id="input",
        )

    def on_mount(self) -> None:
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        welcome_text = WELCOME_TEMPLATE.format(
            backend=app.config.backend,
            model=app.config.active().model,
        )
        self.query_one("#welcome", Static).update(welcome_text)
        self.query_one("#input", Input).focus()

    # ---- input handling ----

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.is_streaming or self.is_tool_executing:
            return
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        self.run_worker(self._dispatch(text), exclusive=True)

    async def _dispatch(self, text: str) -> None:
        if text.startswith("/"):
            await self._handle_slash(text)
        else:
            await self._send_user_message(text)

    async def _handle_slash(self, text: str) -> None:
        cmd, *_ = text.split(maxsplit=1)
        if cmd not in SLASH_COMMANDS:
            self._append_info(f"未知命令: {cmd}（输入 /help 查看可用命令）")
            return
        if cmd == "/help":
            self._show_help()
        elif cmd == "/clear":
            self._clear_conversation()
        elif cmd == "/exit":
            self.app.exit()
        elif cmd == "/model":
            await self._switch_model()
        elif cmd == "/tools":
            self._show_tools()
        elif cmd == "/permissions":
            self._show_permissions()

    # ---- slash command handlers ----

    def _show_help(self) -> None:
        help_text = (
            "**可用命令**\n\n"
            "- `/help` — 显示本帮助\n"
            "- `/clear` — 清空对话历史\n"
            "- `/exit` — 退出 BaoZiCode（Ctrl+C 同样有效）\n"
            "- `/model` — 切换到另一后端（Anthropic ↔ OpenAI）\n"
            "- `/tools` — 列出可用工具\n"
            "- `/permissions` — 查看当前权限配置\n\n"
            "**工具调用**\n\n"
            "模型可在回答中调用 7 个工具（Read / Write / Edit / Bash / Grep / "
            "Glob / WebFetch）。高风险工具（Write / Edit / Bash）执行前会弹窗确认；"
            "低风险工具（Read / Grep / Glob / WebFetch）自动执行。"
        )
        self._append_info(help_text)

    def _clear_conversation(self) -> None:
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        app.conversation.clear()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        for child in list(scroll.children):
            if child.id not in ("banner", "welcome"):
                child.remove()
        self._append_info("对话已清空。")

    async def _switch_model(self) -> None:
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        options = [(name, cfg.model) for name, cfg in app.available_backends()]
        chosen = await self.app.push_screen(
            ModelSelectScreen(
                current_backend=app.config.backend,
                options=options,
            ),
            wait_for_dismiss=True,
        )
        if chosen and chosen != app.config.backend:
            app.switch_backend(chosen)
            self._append_info(
                f"已切换到 `{app.config.backend}` · `{app.config.active().model}`"
            )

    def _show_tools(self) -> None:
        from baozicode.tools.registry import get_all_tools

        tools = get_all_tools()
        low = [t for t in tools if t.risk == "low"]
        high = [t for t in tools if t.risk == "high"]
        lines = ["**可用工具**\n"]
        lines.append("**低风险**（自动执行）:")
        for t in low:
            lines.append(f"- `{t.name}` — {t.description.split('.')[0]}")
        lines.append("\n**高风险**（弹窗确认）:")
        for t in high:
            lines.append(f"- `{t.name}` — {t.description.split('.')[0]}")
        self._append_info("\n".join(lines))

    def _show_permissions(self) -> None:
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        perms = app.config.active_permissions()
        source = "config" if app.config.permissions is not None else "default"
        lines = [
            f"**当前权限**（来源: {source}）\n",
            f"- `auto_allow`: {perms.auto_allow or '(空)'}",
            f"- `deny`: {perms.deny or '(空)'}",
            f"- `batch_confirm`: {perms.batch_confirm}",
            f"- `bash_locked_cwd`: {perms.bash_locked_cwd}",
        ]
        self._append_info("\n".join(lines))

    # ---- agent loop ----

    def _get_tools(self):
        if self._tools_cache is None:
            from baozicode.tools.registry import get_all_tools

            self._tools_cache = get_all_tools()
        return self._tools_cache

    def _matches_deny(self, call, permissions) -> bool:
        """deny pattern:fnmatch 同时作用于 name 和任一 argument 值(独立匹配)。"""
        for pattern in permissions.deny:
            if fnmatch.fnmatch(call.name, pattern):
                return True
            for v in call.arguments.values():
                if isinstance(v, str) and fnmatch.fnmatch(v, pattern):
                    return True
        return False

    def _is_auto_allowed(self, call, permissions) -> bool:
        return call.name in permissions.auto_allow

    def _tool_risk(self, call) -> str:
        from baozicode.tools.registry import get_tool

        t = get_tool(call.name)
        return t.risk if t else "high"

    async def _check_permission(self, call) -> tuple[bool, bool]:
        """返回 (allow, batch_apply)。"""
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        perms = app.config.active_permissions()
        if self._matches_deny(call, perms):
            return False, False
        if self._is_auto_allowed(call, perms):
            return True, False
        if self._tool_risk(call) == "low":
            return True, False
        if self._batch_allow_for == call.name:
            return True, False
        consecutive = (
            self._consecutive_tool_name == call.name
            and self._consecutive_tool_count >= 1
        )
        use_batch = perms.batch_confirm and consecutive
        result = await self.app.push_screen(
            PermissionModal(call, mode="batch" if use_batch else "single"),
            wait_for_dismiss=True,
        )
        if result is None:
            return False, False
        return bool(result[0]), bool(result[1])

    async def _send_user_message(self, text: str) -> None:
        from baozicode.tools.registry import execute_tool_call

        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        app.conversation.add_user(text)
        self._append_user(text)

        scroll = self.query_one("#chat-scroll", VerticalScroll)
        assistant_md = Markdown()
        self._current_assistant_widget = assistant_md
        await scroll.mount(assistant_md)
        scroll.scroll_end(animate=False)
        stream = Markdown.get_stream(assistant_md)

        self._batch_allow_for = None
        self._consecutive_tool_name = None
        self._consecutive_tool_count = 0

        try:
            while True:
                self._set_streaming(True)
                full_text = ""
                pending_calls: list = []
                try:
                    async for delta in app.llm_client.stream(
                        app.conversation.to_list(),
                        system=app.config.system_prompt,
                        tools=self._get_tools(),
                    ):
                        if delta.type == "text":
                            full_text += delta.text
                            await stream.write(delta.text)
                        elif delta.type == "tool_use":
                            pending_calls.append(delta.text)
                except Exception as exc:  # noqa: BLE001
                    self._append_error(f"LLM stream error: {exc}")
                    break

                if not pending_calls:
                    if full_text:
                        app.conversation.add_assistant(full_text)
                    break

                blocks: list = []
                if full_text:
                    blocks.append(TextBlock(text=full_text))
                for c in pending_calls:
                    blocks.append(
                        ToolUseBlock(id=c.id, name=c.name, input=c.arguments)
                    )
                app.conversation.add_message(
                    Message(role="assistant", content=blocks)
                )

                for call in pending_calls:
                    self._set_tool_executing(True)
                    try:
                        card = ToolCallCard(call)
                        await scroll.mount(card)
                        scroll.scroll_end(animate=False)

                        allow, batch_apply = await self._check_permission(call)
                        if batch_apply:
                            self._batch_allow_for = call.name

                        if not allow:
                            card.mark_denied(
                                "denied by permissions.deny 或用户按 N"
                            )
                            from baozicode.tools.base import ToolResult

                            result = ToolResult(
                                tool_call_id=call.id,
                                content=(
                                    "Tool call denied by user or "
                                    "permissions.deny policy."
                                ),
                                is_error=True,
                            )
                        else:
                            result = await execute_tool_call(call)

                        result_card = ToolResultCard(result)
                        await scroll.mount(result_card)
                        scroll.scroll_end(animate=False)
                        app.conversation.add_tool_result(result)

                        if self._consecutive_tool_name == call.name:
                            self._consecutive_tool_count += 1
                        else:
                            self._consecutive_tool_name = call.name
                            self._consecutive_tool_count = 1
                    finally:
                        self._set_tool_executing(False)
        except Exception as exc:  # noqa: BLE001
            self._append_error(str(exc))
        finally:
            await stream.stop()
            self._set_streaming(False)
            self._current_assistant_widget = None
            self._batch_allow_for = None
            self._consecutive_tool_name = None
            self._consecutive_tool_count = 0

    # ---- helpers ----

    def _set_streaming(self, on: bool) -> None:
        self.is_streaming = on
        self._refresh_input_lock()

    def _set_tool_executing(self, on: bool) -> None:
        self.is_tool_executing = on
        self._refresh_input_lock()

    def _refresh_input_lock(self) -> None:
        locked = self.is_streaming or self.is_tool_executing
        try:
            input_widget = self.query_one("#input", Input)
        except Exception:
            return
        input_widget.disabled = locked
        if not locked:
            input_widget.focus()

    def _append_user(self, text: str) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        widget = Static(f"**You:** {text}", classes="user-message")
        scroll.mount(widget)
        scroll.scroll_end(animate=False)

    def _append_info(self, text: str) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        widget = Static(text, classes="info-message")
        scroll.mount(widget)
        scroll.scroll_end(animate=False)

    def _append_error(self, text: str) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        widget = Static(f"**✗ 错误:** {text}", classes="error-message")
        scroll.mount(widget)
        scroll.scroll_end(animate=False)