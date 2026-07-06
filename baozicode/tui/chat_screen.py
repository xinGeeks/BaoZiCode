"""主对话屏幕。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Markdown, Static

from baozicode.config.schema import BackendName
from baozicode.tui.banner import BAOZI_BANNER, WELCOME_TEMPLATE

if TYPE_CHECKING:
    from baozicode.app import BaoZiCodeApp


SLASH_COMMANDS = ("/help", "/clear", "/exit", "/model")


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
        self.options = options  # [(backend_name, model_label), ...]

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
            backend = btn_id.removeprefix("select-")
            self.dismiss(backend)  # type: ignore[arg-type]

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChatScreen(Screen):
    """通过 `app.push_screen` 装载的主屏幕。"""

    def __init__(self) -> None:
        super().__init__()
        self.is_streaming = False
        self._current_assistant_widget: Markdown | None = None

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
        if self.is_streaming:
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

    # ---- slash command handlers ----

    def _show_help(self) -> None:
        help_text = (
            "**可用命令**\n\n"
            "- `/help` — 显示本帮助\n"
            "- `/clear` — 清空对话历史\n"
            "- `/exit` — 退出 BaoZiCode（Ctrl+C 同样有效）\n"
            "- `/model` — 切换到另一后端（Anthropic ↔ OpenAI）\n"
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
        chosen = await self.push_screen(
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

    # ---- user message + streaming ----

    async def _send_user_message(self, text: str) -> None:
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        app.conversation.add_user(text)
        self._append_user(text)

        self._set_streaming(True)
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        assistant_md = Markdown()
        self._current_assistant_widget = assistant_md
        await scroll.mount(assistant_md)
        scroll.scroll_end(animate=False)

        # Textual 8.x：Markdown.get_stream() 为高频流式设计（>20 appends/秒也能跟上），
        # 后台任务会自动合并多次 write，避免 Markdown.update() 每 token 重新解析整篇。
        from textual.widgets.markdown import MarkdownStream

        stream = Markdown.get_stream(assistant_md)
        full_response = ""
        try:
            async for delta in app.llm_client.stream(
                app.conversation.to_list(),
                system=app.config.system_prompt,
            ):
                if delta.type == "text" and delta.text:
                    full_response += delta.text
                    await stream.write(delta.text)
        except Exception as exc:  # noqa: BLE001
            error_block = f"\n\n**✗ 错误**: `{exc}`\n"
            full_response += error_block
            await stream.write(error_block)
            self._append_error(str(exc))
        finally:
            await stream.stop()
            if full_response:
                app.conversation.add_assistant(full_response)
            self._set_streaming(False)
            self._current_assistant_widget = None

    # ---- helpers ----

    def _set_streaming(self, on: bool) -> None:
        self.is_streaming = on
        input_widget = self.query_one("#input", Input)
        input_widget.disabled = on
        if not on:
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
