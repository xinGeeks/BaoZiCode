"""主对话屏幕 — v0.3 改造为 Agent 事件流订阅者。

事件映射:
- text      → Markdown 流式追加
- tool_call → 挂 ToolCallCard
- tool_result → 挂 ToolResultCard(若 call 被拒绝则把对应 Card 标 ✗)
- progress  → 状态栏(iteration/max · phase)
- usage     → 日志条(本轮 + 累计)
- error     → 红色 ✗
- done      → 收尾(解锁输入、清状态栏、显示运行结果)

slash 命令:
- /help /clear /exit /model /tools /permissions(v0.2 沿用)
- /plan [task] — 进入只读模式跑 Agent,只放开 4 个读类工具
- /do         — 退出 plan mode,跑 Agent(用全工具)
- /auto       — 切换 auto_allow 模式(本会话跳过所有 Modal)
- /stop       — 取消正在运行的 Agent
- /status     — 显示当前 mode/backend/model/token 等

Bindings:
- Ctrl+C — 运行中:取消 Agent;idle:退出
- Esc    — 运行中:取消 Agent;idle:无操作(留给 Modal 自己处理)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Markdown, Static

from baozicode.agent.events import AgentEvent, Progress, StopReason, UsageStats
from baozicode.config.schema import BackendName
from baozicode.tools.base import ToolCall, ToolResult
from baozicode.tui.banner import BAOZI_BANNER, WELCOME_TEMPLATE
from baozicode.tui.permission_modal import PermissionModal
from baozicode.tui.tool_card import ToolCallCard, ToolResultCard

if TYPE_CHECKING:
    from baozicode.agent.loop import Agent
    from baozicode.app import BaoZiCodeApp

SLASH_COMMANDS = (
    "/help",
    "/clear",
    "/exit",
    "/model",
    "/tools",
    "/permissions",
    "/plan",
    "/do",
    "/auto",
    "/stop",
    "/status",
)


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


class StatusBar(Static):
    """底部状态栏 — 显示 mode、iteration/max、phase、token 累计。

    真实样式在 styles.tcss 中定义 — `dock: bottom` 与 #input 冲突,
    所以这里只声明 DEFAULT_CSS 占位。
    """


class ChatScreen(Screen):
    """通过 `app.push_screen` 装载的主屏幕。

    v0.3 起通过订阅 Agent.run() 的事件流驱动,内部不再持有 ReAct while 循环。
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel_or_quit", "Cancel/Quit", show=True),
        Binding("escape", "cancel_running", "Cancel", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        # 不能叫 is_running — Textual.Screen.is_running 是 MessagePump 自带 property,
        # 我们覆盖它会让 Screen._on_mount 里的 signal.subscribe 拿到 False 抛 SignalError。
        self.agent_running: bool = False
        self.plan_mode: bool = False
        self.auto_mode: bool = False
        self._current_agent: Agent | None = None
        self._stream_widget: Markdown | None = None
        self._stream_writer = None
        self._pending_calls: dict[str, ToolCallCard] = {}
        self._consecutive_tool_name: str | None = None
        self._consecutive_tool_count: int = 0
        self._batch_allow_for: str | None = None
        self._tools_cache: list | None = None
        self._last_user_text: str = ""

    # ---- layout ----

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="chat-scroll"):
            yield Static(BAOZI_BANNER, id="banner")
            yield Static(id="welcome", classes="info-message")
        yield StatusBar(id="status-bar")
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
        self._update_status_bar(idle=True)

    # ---- input handling ----

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.agent_running:
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
        cmd, _, args = text.partition(" ")
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
        elif cmd == "/plan":
            await self._handle_plan(args.strip())
        elif cmd == "/do":
            await self._handle_do(args.strip())
        elif cmd == "/auto":
            self._toggle_auto_mode()
        elif cmd == "/stop":
            self._handle_stop()
        elif cmd == "/status":
            self._show_status()

    # ---- slash command handlers ----

    def _show_help(self) -> None:
        help_text = (
            "**可用命令**\n\n"
            "- `/help` — 显示本帮助\n"
            "- `/clear` — 清空对话历史\n"
            "- `/exit` — 退出 BaoZiCode（Ctrl+C 同样有效）\n"
            "- `/model` — 切换到另一后端（4 后端之一）\n"
            "- `/tools` — 列出可用工具（含 side_effect 标记）\n"
            "- `/permissions` — 查看当前权限配置\n"
            "- `/plan [task]` — 进入 plan mode（只读工具）跑 Agent；带 task 直接跑\n"
            "- `/do [task]` — 退出 plan mode，跑 Agent；带 task 直接跑\n"
            "- `/auto` — 切换 auto 模式（本会话跳过所有 Modal）\n"
            "- `/stop` — 取消正在运行的 Agent（Esc/Ctrl+C 同样有效）\n"
            "- `/status` — 显示 mode / backend / model / token 累计\n\n"
            "**v0.3 关键变化**\n\n"
            "- Agent 自主循环：一次消息可能跨多轮（最多 "
            "`max_iterations` 轮，可在 `config.yaml` 配）\n"
            "- Plan Mode：先 `/plan` 让模型只读规划，再用 `/do` 执行\n"
            "- 自动停止条件：模型说完 / 迭代上限 / 连续调到未知工具 / 连续失败 / 出错\n"
            "- 进度状态栏：底部实时显示 `{iteration}/{max} · {phase}`\n"
        )
        self._append_info(help_text)

    def _clear_conversation(self) -> None:
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        app.conversation.clear()
        app.session_usage = UsageStats()
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
        read_only = [t for t in tools if not t.side_effect]
        with_effect = [t for t in tools if t.side_effect]
        lines = ["**可用工具**\n"]
        lines.append("**只读**（plan mode 也会暴露，副作用工具被屏蔽）:")
        for t in read_only:
            lines.append(f"- `{t.name}` — {t.description.split('.')[0]}")
        lines.append("\n**有副作用**（plan mode 下隐藏）:")
        for t in with_effect:
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
            f"- 会话级 auto_mode: {self.auto_mode}",
        ]
        self._append_info("\n".join(lines))

    async def _handle_plan(self, task: str) -> None:
        """`/plan [task]` — 进入 plan mode 后跑 Agent。"""
        if self.agent_running:
            self._append_info("Agent 正在运行，请先 /stop 或等待完成。")
            return
        self.plan_mode = True
        if task:
            await self._send_user_message(task)
        else:
            self._append_info(
                "已切换到 plan mode。下条消息会用只读工具运行 Agent。"
            )
            self._update_status_bar(idle=True)

    async def _handle_do(self, task: str) -> None:
        """`/do [task]` — 退出 plan mode 后跑 Agent(全工具)。"""
        if self.agent_running:
            self._append_info("Agent 正在运行，请先 /stop 或等待完成。")
            return
        self.plan_mode = False
        if task:
            await self._send_user_message(task)
        else:
            self._append_info(
                "已退出 plan mode。下条消息会用全部工具运行 Agent。"
            )
            self._update_status_bar(idle=True)

    def _toggle_auto_mode(self) -> None:
        self.auto_mode = not self.auto_mode
        self._append_info(
            f"auto_mode 已切换为 **{'ON' if self.auto_mode else 'OFF'}**。"
            f"{'此后跳过所有 Modal 自动通过。' if self.auto_mode else '高风险工具会再次弹 Modal。'}"
        )
        self._update_status_bar(idle=True)

    def _handle_stop(self) -> None:
        if self.agent_running and self._current_agent is not None:
            self._current_agent.cancel()
            self._append_info("已请求取消 Agent...")
        else:
            self._append_info("当前没有运行中的 Agent。")

    def _show_status(self) -> None:
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        perms = app.config.active_permissions()
        agent_cfg = app.config.active_agent()
        usage = app.session_usage
        lines = [
            "**当前状态**\n",
            f"- mode: `{'plan' if self.plan_mode else 'full'}`（"
            f"{'只读工具' if self.plan_mode else '全部工具'}）",
            f"- auto_mode: `{self.auto_mode}`（本会话 Modal 跳过开关）",
            f"- backend: `{app.config.backend}`",
            f"- model: `{app.config.active().model}`",
            f"- max_iterations: `{agent_cfg.max_iterations}`",
            f"- permissions.auto_allow: `{perms.auto_allow or '(空)'}`",
            f"- permissions.deny: `{perms.deny or '(空)'}`",
            f"- token 累计: input={usage.input_tokens} "
            f"output={usage.output_tokens} "
            f"cache_read={usage.cache_read_tokens} "
            f"cache_write={usage.cache_write_tokens}",
        ]
        self._append_info("\n".join(lines))

    # ---- agent loop ----

    def _get_tools(self):
        if self._tools_cache is None:
            from baozicode.tools.registry import get_all_tools

            self._tools_cache = get_all_tools()
        return self._tools_cache

    async def _permission_callback(self, call: ToolCall) -> bool:
        """TUI 端 Modal 钩子,Agent 在每个非 auto_allowed 工具前 await 此函数。

        /auto 模式:不弹 Modal 直接放行。
        batch_confirm 模式:连续同类型第二次起 Modal 带"Allow remaining"按钮。
        """
        if self.auto_mode:
            return True
        perms = self.app.config.active_permissions()  # type: ignore[attr-defined]
        consecutive = (
            self._consecutive_tool_name == call.name
            and self._consecutive_tool_count >= 1
        )
        mode = "batch" if (perms.batch_confirm and consecutive) else "single"
        if self._batch_allow_for == call.name:
            return True
        result = await self.app.push_screen(  # type: ignore[attr-defined]
            PermissionModal(call, mode=mode),
            wait_for_dismiss=True,
        )
        if result is None:
            return False
        allow, batch_apply = bool(result[0]), bool(result[1])
        if batch_apply:
            self._batch_allow_for = call.name
        return allow

    async def _send_user_message(self, text: str) -> None:
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        from baozicode.agent.loop import Agent

        # ---- 准备 Agent ----
        perms = app.config.active_permissions()
        agent = Agent(
            llm_client=app.llm_client,
            tools=self._get_tools(),
            conversation=app.conversation,
            permissions=perms,
            system_prompt=app.config.system_prompt,
            max_iterations=app.config.active_agent().max_iterations,
            plan_mode=self.plan_mode,
            permission_callback=self._permission_callback,
        )
        self._current_agent = agent
        app._current_agent = agent

        # ---- 重置本轮 batch / consecutive 状态 ----
        self._consecutive_tool_name = None
        self._consecutive_tool_count = 0
        self._batch_allow_for = None
        self._pending_calls.clear()

        # ---- UI 准备 ----
        self._append_user(text)
        self._last_user_text = text
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        assistant_md = Markdown()
        self._stream_widget = assistant_md
        await scroll.mount(assistant_md)
        scroll.scroll_end(animate=False)
        stream = Markdown.get_stream(assistant_md)
        self._stream_writer = stream

        self.agent_running = True
        self._refresh_input_lock()
        self._update_status_bar(idle=False, progress_text="0/? · starting")

        # ---- 订阅 Agent 事件流 ----
        stop_reason: StopReason | None = None
        try:
            async for event in agent.run(text):
                stop_reason = await self._handle_event(event, scroll, stream)
                if stop_reason is not None:
                    break
        except Exception as exc:  # noqa: BLE001
            self._append_error(f"Agent 顶层异常: {exc}")
        finally:
            await stream.stop()
            self._stream_widget = None
            self._stream_writer = None
            self.agent_running = False
            self._refresh_input_lock()
            self._update_status_bar(idle=True)
            self._current_agent = None
            if stop_reason is not None:
                self._render_done(stop_reason)

    async def _handle_event(
        self,
        event: AgentEvent,
        scroll: VerticalScroll,
        stream,
    ) -> StopReason | None:
        """处理单个 AgentEvent。

        返回 StopReason(若有)表示 Agent 应被中断;否则返回 None 继续接收。
        目前只有 future hook 才需要返回非 None,默认 None。
        """
        if event.type == "text":
            chunk = event.payload
            if chunk:
                await stream.write(chunk)
                scroll.scroll_end(animate=False)
        elif event.type == "tool_call":
            call: ToolCall = event.payload
            card = ToolCallCard(call)
            await scroll.mount(card)
            scroll.scroll_end(animate=False)
            self._pending_calls[call.id] = card
            # 维护 consecutive 计数(供 Modal 的 batch 模式判断)
            if self._consecutive_tool_name == call.name:
                self._consecutive_tool_count += 1
            else:
                self._consecutive_tool_name = call.name
                self._consecutive_tool_count = 1
        elif event.type == "tool_result":
            result: ToolResult = event.payload
            result_card = ToolResultCard(result)
            await scroll.mount(result_card)
            scroll.scroll_end(animate=False)
            # 若对应 call 被拒绝 / 出错,更新 ToolCallCard 视觉
            card = self._pending_calls.pop(result.tool_call_id, None)
            if card is not None and result.is_error:
                card.mark_denied(_short_error(result.content))
        elif event.type == "usage":
            payload = event.payload
            this_turn: UsageStats = payload["this_turn"]
            session_total: UsageStats = payload["session_total"]
            app: BaoZiCodeApp = self.app  # type: ignore[assignment]
            app.session_usage = session_total
            self._append_info(
                f"📊 tokens · this: in={this_turn.input_tokens} "
                f"out={this_turn.output_tokens} · "
                f"session: in={session_total.input_tokens} "
                f"out={session_total.output_tokens}"
            )
        elif event.type == "progress":
            progress: Progress = event.payload
            self._update_status_bar(
                idle=False,
                progress_text=(
                    f"{progress.iteration}/{progress.max} · {progress.phase}"
                ),
            )
        elif event.type == "error":
            self._append_error(str(event.payload))
        elif event.type == "done":
            # 由 _render_done 在 finally 里统一处理
            pass
        return None

    def _render_done(self, reason: StopReason) -> None:
        # 在最后一条 assistant 文本下追加一行总结,告诉用户为什么停了
        reason_text = {
            StopReason.COMPLETED: "✓ 任务完成（模型主动结束）",
            StopReason.MAX_ITERATIONS_REACHED: (
                "⚠ 迭代次数到上限（max_iterations），强制终止"
            ),
            StopReason.USER_CANCELLED: "⏹ 用户取消（Esc / Ctrl+C / /stop）",
            StopReason.UNKNOWN_TOOL_HALLUCINATION: (
                "⏹ 模型连续调到未知工具名，终止"
            ),
            StopReason.DENIALS_EXCEEDED: "⏹ 同一工具被拒多次，终止",
            StopReason.FAILED_TOOL_LOOP: (
                "⏹ 同一工具连续失败，终止（多半是模型/工具 bug）"
            ),
            StopReason.STREAM_ERROR: "⏹ LLM 流式输出出错",
        }
        self._append_info(reason_text.get(reason, f"⏹ Agent 结束: {reason}"))

    # ---- helpers ----

    def _refresh_input_lock(self) -> None:
        try:
            input_widget = self.query_one("#input", Input)
        except Exception:
            return
        input_widget.disabled = self.agent_running
        if not self.agent_running:
            input_widget.focus()

    def _update_status_bar(
        self,
        *,
        idle: bool,
        progress_text: str | None = None,
    ) -> None:
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        try:
            bar = self.query_one("#status-bar", StatusBar)
        except Exception:
            return
        mode = "plan" if self.plan_mode else "full"
        backend = app.config.backend
        model = app.config.active().model
        auto = "auto" if self.auto_mode else "ask"
        if idle:
            text = (
                f"● {mode} · {backend}/{model} · {auto} · idle"
            )
        else:
            text = f"● {mode} · {backend}/{model} · {auto} · {progress_text or '...'}"
        bar.update(text)

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

    # ---- bindings ----

    def action_cancel_or_quit(self) -> None:
        """Ctrl+C:idle → 退出;运行中 → 取消 Agent(同 /stop)。"""
        if self.agent_running and self._current_agent is not None:
            self._current_agent.cancel()
            self._append_info("已请求取消 Agent...(Ctrl+C)")
        else:
            self.app.exit()

    def action_cancel_running(self) -> None:
        """Esc:idle → no-op(避免与 Modal 冲突);运行中 → 取消。"""
        if self.agent_running and self._current_agent is not None:
            self._current_agent.cancel()
            self._append_info("已请求取消 Agent...(Esc)")


def _short_error(content: str, limit: int = 60) -> str:
    text = content.strip().splitlines()[0] if content else ""
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


__all__ = ["ChatScreen", "ModelSelectScreen", "StatusBar", "SLASH_COMMANDS"]
