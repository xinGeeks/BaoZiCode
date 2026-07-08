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
- /permissions mode — 切换 strict/default/permissive(v0.5 五层防御)
- /plan [task] — 进入只读模式跑 Agent,只放开 4 个读类工具
- /do         — 退出 plan mode,跑 Agent(用全工具)
- /auto       — 切换 auto_allow 模式(本会话跳过所有 Modal)
- /stop       — 取消正在运行的 Agent
- /status     — 显示当前 mode/backend/model/token 等

v0.5 变化:
- 5 层防御(L1 黑名单 / L2 沙箱 / L3 规则 / L4 mode / L5 user)
- /permissions mode 子命令切换 session_mode
- 状态栏追加 perm_mode 段
- 重试 previously denied 调用时弹 🔧 提示卡

Bindings:
- Ctrl+C — 运行中:取消 Agent;idle:退出
- Esc    — 运行中:取消 Agent;idle:无操作(留给 Modal 自己处理)
"""

from __future__ import annotations

import json
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
from baozicode.tui.permission_modal import PermissionChoice, PermissionModal
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
    "/mcp",
    "/compact",
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


class ModeSelectScreen(ModalScreen[str | None]):
    """`/permissions mode` 触发的模式切换弹窗（3 选项列表）。

    返回选中的 mode 字符串("strict" / "default" / "permissive" / None);
    None = 取消(未做选择)。
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    MODE_LABELS = {
        "strict": "strict — 所有未明确允许的工具调用都会被拒绝",
        "default": "default — 未匹配规则的工具调用会弹 Modal 让用户决定",
        "permissive": "permissive — 所有未明确拒绝的工具调用都自动放行",
    }

    def __init__(self, current_mode: str) -> None:
        super().__init__()
        self.current_mode = current_mode

    def compose(self) -> ComposeResult:
        yield Static("选择权限模式（按 Esc 取消）", id="mode-title")
        with Vertical(id="mode-buttons"):
            for mode, desc in self.MODE_LABELS.items():
                if mode == self.current_mode:
                    label = f"● {desc}  (当前)"
                    variant = "success"
                else:
                    label = f"  {desc}"
                    variant = "primary"
                yield Button(label, id=f"select-{mode}", variant=variant)
            yield Button("取消", id="cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "cancel":
            self.dismiss(None)
        elif btn_id.startswith("select-"):
            self.dismiss(btn_id.removeprefix("select-"))

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
        self._pending_calls: dict[str, tuple[ToolCallCard, tuple[str, str]]] = {}
        self._consecutive_tool_name: str | None = None
        self._consecutive_tool_count: int = 0
        self._batch_allow_for: str | None = None
        self._tools_cache: list | None = None
        self._last_user_text: str = ""
        # v0.5:跟踪最近被拒的工具调用,key = (tool_name, json.dumps(arguments, sort_keys=True))
        # 重试时 Modal 标题加 "(previously denied)" + 弹 🔧 提示卡
        self._previously_denied: set[tuple[str, str]] = set()
        # v0.5:本次会话累积拒绝次数(L1-L4 系统拒 + L5 用户拒),供 /status 展示
        self._session_deny_count: int = 0

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
            sub = args.strip().lower()
            if sub == "mode":
                await self._handle_permissions_mode()
            else:
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
        elif cmd == "/mcp":
            await self._handle_mcp(args.strip())
        elif cmd == "/compact":
            await self._handle_compact()

    # ---- slash command handlers ----

    def _show_help(self) -> None:
        help_text = (
            "**可用命令**\n\n"
            "- `/help` — 显示本帮助\n"
            "- `/clear` — 清空对话历史\n"
            "- `/exit` — 退出 BaoZiCode（Ctrl+C 同样有效）\n"
            "- `/model` — 切换到另一后端（4 后端之一）\n"
            "- `/tools` — 列出可用工具（含 side_effect 标记）\n"
            "- `/permissions` — 查看当前权限配置（v0.5:含 mode / 三层 YAML / session rules）\n"
            "- `/permissions mode` — 切换权限模式（strict / default / permissive）\n"
            "- `/plan [task]` — 进入 plan mode（只读工具）跑 Agent；带 task 直接跑\n"
            "- `/do [task]` — 退出 plan mode，跑 Agent；带 task 直接跑\n"
            "- `/auto` — 切换 auto 模式（本会话跳过所有 Modal）\n"
            "- `/stop` — 取消正在运行的 Agent（Esc/Ctrl+C 同样有效）\n"
            "- `/status` — 显示 mode / backend / model / token 累计\n"
            "- `/mcp` — 查看 MCP server 状态（v0.6）\n"
            "- `/mcp reconnect <name>` — 重连指定 MCP server\n"
            "- `/compact` — 手动压缩上下文（v0.7：Layer 1 offload + Layer 2 摘要）\n\n"
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
        # v0.5:/clear 视作新会话,重置 deny 计数 + previously_denied 集合
        # (session_rule 不清 — 那些是用户在本进程生命周期内累积的"会话级"规则)
        self._session_deny_count = 0
        self._previously_denied.clear()
        # v0.7:/clear 同步清掉 offload 文件
        try:
            app.context_storage.cleanup()
        except Exception:  # noqa: BLE001
            pass
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        for child in list(scroll.children):
            if child.id not in ("banner", "welcome"):
                child.remove()
        self._append_info("对话已清空。")

    def _clear_partial_cards(self) -> None:
        """v0.7:`/compact` 在请求 Agent 暂停前调用 — 摘掉任何"半截"卡片(没有 tool_result 的 tool_call、空 text 等)。

        简化实现:只清掉最近一轮所有非 banner / welcome 子节点,然后让 caller 自己 yield 新的状态行。
        """
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        # 只清空最后一个非空节点之后的所有卡片(简化:全清掉,然后保留 banner/welcome)
        for child in list(scroll.children):
            if child.id not in ("banner", "welcome"):
                child.remove()

    async def _handle_compact(self) -> None:
        """`/compact` — 手动触发上下文压缩(v0.7)。

        - Agent 在跑:`agent.request_compact()`,主循环下个迭代顶部自动跑 manual 压缩
        - Agent 空闲:直接调 `app.run_compact_now()` 跑同步压缩,显示 token 数变化
        """
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        agent = app.current_agent()
        if agent is not None:
            # Agent 正在跑 — 走异步路径(下一轮迭代顶部生效)
            self._clear_partial_cards()
            agent.request_compact()
            self._append_info("[已请求压缩,Agent 下一轮迭代顶部执行...]")
            return
        # 空闲 — 直接跑
        triggered, status = await app.run_compact_now()
        self._append_info(status)

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
        """`/permissions` — 展示 v0.5 五层防御当前状态。

        显示:当前 mode / 三层 YAML 来源 / session rule 数 / 规则汇总(按 tool 分组,top 10)。
        若 App 上没挂 permissions_v5(老 back-compat 路径),退回到 v0.2 字段展示。
        """
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        merged = getattr(app, "permissions_v5", None)
        engine = getattr(app, "permissions_engine", None)
        project_root = getattr(app, "project_root", None)

        if merged is None:
            # 旧 v0.2 路径
            perms = app.config.active_permissions()
            source = "config" if app.config.permissions is not None else "default"
            lines = [
                f"**当前权限**(v0.2 兼容路径,无 v0.5 merged_permissions;来源: {source})\n",
                f"- `auto_allow`: {perms.auto_allow or '(空)'}",
                f"- `deny`: {perms.deny or '(空)'}",
                f"- `batch_confirm`: {perms.batch_confirm}",
                f"- `bash_locked_cwd`: {perms.bash_locked_cwd}",
                f"- 会话级 auto_mode: {self.auto_mode}",
            ]
            self._append_info("\n".join(lines))
            return

        # v0.5 五层防御
        current_mode = self._current_permission_mode()
        session_mode = getattr(app, "session_mode", None)
        mode_hint = (
            f"(session override: `{session_mode}`)"
            if session_mode
            else f"(YAML 声明: `{merged.mode}`)"
        )

        # 三层 YAML 路径 + 实际加载情况
        if project_root is not None:
            from baozicode.permissions.loader import _search_paths
            paths = _search_paths(project_root)
        else:
            paths = []
        path_lines: list[str] = []
        for source, path in paths:
            exists = path.is_file()
            mark = "✓" if exists else "✗"
            loaded = "已加载" if str(path) in merged.sources_loaded else (
                "存在但未加载" if exists else "缺失"
            )
            path_lines.append(f"  - {mark} `{source}`: `{path}` ({loaded})")

        # session rule 数(v0.5:session_rules 挂在 merged 上,不在 engine 上)
        session_count = len(merged.session_rules)
        merged_count = len(merged.rules)
        deny_count = sum(1 for r in merged.rules if r.decision == "deny")
        allow_count = merged_count - deny_count

        # 按 tool 分组,top 10
        from collections import defaultdict
        grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for r in merged.rules:
            grouped[r.tool].append((r.pattern, r.decision, r.source))
        # session rules 也加进去(显示在最前)
        for r in merged.session_rules:
            grouped[r.tool].insert(0, (r.pattern, r.decision, "session"))

        rule_lines: list[str] = []
        shown = 0
        for tool, entries in sorted(grouped.items()):
            if shown >= 10:
                break
            for pattern, decision, source in entries:
                if shown >= 10:
                    break
                icon = "✓" if decision == "allow" else "✗"
                rule_lines.append(
                    f"  - `{tool}({pattern})` {icon}{decision} "
                    f"[{source}]"
                )
                shown += 1

        if not rule_lines:
            rule_lines.append("  - (无规则)")

        lines = [
            "**v0.5 五层防御 — 当前权限**\n",
            f"- **mode**: `{current_mode}` {mode_hint}",
            f"- **三层 YAML 源**:",
            *path_lines,
            f"- **规则统计**: 共 {merged_count} 条 (allow={allow_count}, deny={deny_count}),"
            f" session={session_count}",
            f"- **Top 10 规则(按 tool 分组)**:",
            *rule_lines,
            f"- 会话级 auto_mode: `{self.auto_mode}`"
            f"(跳 Modal,不影响 v0.5 5 层)",
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

    def _current_permission_mode(self) -> str:
        """返回当前生效的 permission mode(展示/比较用)。

        优先级:App.session_mode > permissions_v5.mode > "default"。
        App 上没挂 session_mode / permissions_v5 时退化为 "default"。
        """
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        session_mode = getattr(app, "session_mode", None)
        if session_mode:
            return session_mode
        merged = getattr(app, "permissions_v5", None)
        if merged is not None:
            return merged.mode
        return "default"

    async def _handle_permissions_mode(self) -> None:
        """`/permissions mode` — 弹 ModeSelectScreen 切换 session_mode。

        设计:mode 切换不热更当前 Agent,只对下一个新建的 Agent 生效。
        """
        current = self._current_permission_mode()
        chosen = await self.app.push_screen(  # type: ignore[attr-defined]
            ModeSelectScreen(current_mode=current),
            wait_for_dismiss=True,
        )
        if chosen and chosen != current:
            app: BaoZiCodeApp = self.app  # type: ignore[assignment]
            app.session_mode = chosen  # type: ignore[attr-defined]
            self._append_info(
                f"权限模式已设置为 `{chosen}`。"
                f"下一条用户消息起生效(当前运行中的 Agent 不受影响)。"
            )
            self._update_status_bar(idle=True)
        elif chosen == current:
            self._append_info(f"权限模式已是 `{current}`,未变更。")

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
        total = usage.cache_read_tokens + usage.input_tokens
        hit_rate = round(usage.cache_read_tokens / total * 100, 1) if total > 0 else 0.0

        # v0.5:permission mode + threshold + session stats
        current_mode = self._current_permission_mode()
        merged = getattr(app, "permissions_v5", None)
        session_count = len(merged.session_rules) if merged is not None else 0
        threshold = agent_cfg.denial_warn_threshold
        deny_count = self._session_deny_count

        lines = [
            "**当前状态**\n",
            f"- mode: `{'plan' if self.plan_mode else 'full'}`（"
            f"{'只读工具' if self.plan_mode else '全部工具'}）",
            f"- perm_mode: `{current_mode}`（v0.5 五层防御档位）",
            f"- auto_mode: `{self.auto_mode}`（本会话 Modal 跳过开关）",
            f"- backend: `{app.config.backend}`",
            f"- model: `{app.config.active().model}`",
            f"- max_iterations: `{agent_cfg.max_iterations}`",
            f"- denial_warn_threshold: `{threshold}`（v0.5 同工具拒绝 ≥ 阈值会注入 reminder）",
            f"- session_rule_count: `{session_count}`（本次会话累积放行规则）",
            f"- session_deny_count: `{deny_count}`（本次会话累积拒绝次数）",
            "- v0.2 兼容字段(无 merged_permissions 时使用):",
            f"  - permissions.auto_allow: `{perms.auto_allow or '(空)'}`",
            f"  - permissions.deny: `{perms.deny or '(空)'}`",
            "- token 累计:",
            f"  - input: `{usage.input_tokens}`",
            f"  - output: `{usage.output_tokens}`",
            f"  - cache_read: `{usage.cache_read_tokens}`",
            f"  - cache_write: `{usage.cache_write_tokens}`",
            f"  - hit_rate: `{hit_rate}%`",
        ]
        # v0.7:compression 段(只在 compactions > 0 时显示)
        compactions = getattr(app, "compaction_telemetry", None)
        if compactions is not None and compactions.compaction_count > 0:
            last = (
                compactions.last_compact_at.isoformat(timespec="seconds")
                if compactions.last_compact_at
                else "(never)"
            )
            lines.append("- v0.7 compression:")
            lines.append(f"  - compactions: `{compactions.compaction_count}`")
            lines.append(f"  - tokens_saved: `{compactions.total_tokens_saved}`")
            lines.append(f"  - last_compact: `{last}`")
        self._append_info("\n".join(lines))

    async def _handle_mcp(self, args: str) -> None:
        """`/mcp [subcommand]` — v0.6:查看 MCP server 状态 / 重连 / 帮助。

        子命令:
        - (空)        — 显示 server 状态表(name / status / tools / error)
        - reconnect <name> — 重跑单 server 握手
        - help       — 列出可用子命令
        """
        app: BaoZiCodeApp = self.app  # type: ignore[assignment]
        manager = getattr(app, "mcp_manager", None)

        if args == "help":
            self._append_info(
                "**/mcp 子命令**\n\n"
                "- `/mcp` — 显示所有 MCP server 状态\n"
                "- `/mcp reconnect <name>` — 重连指定 server\n"
                "- `/mcp help` — 本帮助"
            )
            return

        if manager is None:
            self._append_info(
                "MCP 客户端未启动（app.mcp_manager 为 None）。"
                "请确认 config.yaml 里有 `mcp_servers` 配置块。"
            )
            return

        if args.startswith("reconnect "):
            name = args[len("reconnect "):].strip()
            if not name:
                self._append_info("用法: /mcp reconnect <name>")
                return
            self._append_info(f"[mcp] reconnecting {name!r}...")
            try:
                state = await manager.reconnect(name)
            except Exception as exc:  # noqa: BLE001
                self._append_info(f"[mcp] reconnect {name!r} failed: {exc}")
                return
            if state.status == "connected":
                tool_names = ", ".join(t.name for t in state.tools)
                self._append_info(
                    f"[mcp] {name!r}: {state.status} — "
                    f"{len(state.tools)} tools [{tool_names}]"
                )
            else:
                self._append_info(
                    f"[mcp] {name!r}: {state.status} — {state.error}"
                )
            return

        states = manager.states
        if not states:
            self._append_info("MCP: 未配置 server（config.yaml 里没有 mcp_servers 块）。")
            return

        lines = ["**MCP servers**\n"]
        for name, state in states.items():
            icon = {"connected": "●", "failed": "✗", "broken": "✗"}.get(
                state.status, "?"
            )
            tool_list = ", ".join(t.name for t in state.tools) if state.tools else "—"
            err = f" — {state.error}" if state.error else ""
            lines.append(
                f"- {icon} `{name}`: `{state.status}` · "
                f"{len(state.tools)} tools [{tool_list}]{err}"
            )
        lines.append(
            "\n输入 `/mcp reconnect <name>` 重连指定 server；"
            "`/mcp help` 看更多子命令。"
        )
        self._append_info("\n".join(lines))

    def _show_denial_for_retry(self, call: ToolCall) -> None:
        """v0.5:在 ToolCallCard 之前追加一条 🔧 提示卡,告诉用户(和 LLM 透过历史)
        此次调用与之前被拒的调用参数相同。

        设计意图:LLM 重试已拒调用是常见现象(参数微调 / 完全相同),给用户
        一条视觉提示区分"全新调用"和"重试已拒调用",也方便在 chat 历史里
        检索 retry pattern。
        """
        pattern_hint = ""
        try:
            from baozicode.tui.permission_modal import derive_glob_pattern
            pattern_hint = f" 上次若放行会存为 glob: `{derive_glob_pattern(call)}`"
        except Exception:
            pass
        text = (
            f"🔧 **(previously denied)** 工具 `{call.name}` 以相同参数再次被调用。"
            f"{pattern_hint}\n"
            f"建议:换工具 / 改参数 / 向用户询问 / 放弃这一步。"
        )
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        widget = Static(text, classes="info-message")
        scroll.mount(widget)
        scroll.scroll_end(animate=False)

    # ---- agent loop ----

    def _get_tools(self):
        if self._tools_cache is None:
            from baozicode.tools.registry import get_all_tools

            self._tools_cache = get_all_tools()
        return self._tools_cache

    async def _permission_callback(self, call: ToolCall) -> bool:
        """TUI 端 Modal 钩子,Agent 在每个非 auto_allowed 工具前 await 此函数。

        v0.5:Modal 返回 PermissionChoice 四档,本函数处理持久化逻辑。
        /auto 模式:不弹 Modal 直接放行(等同于 ONCE)。
        """
        if self.auto_mode:
            return True
        # 同一 call 重试 → 标题加 (previously denied)
        call_key = _call_deny_key(call)
        previously = call_key in self._previously_denied
        result = await self.app.push_screen(  # type: ignore[attr-defined]
            PermissionModal(call, previously_denied=previously),
            wait_for_dismiss=True,
        )
        # Modal 关闭/未选 → 默认拒绝
        if result is None:
            self._previously_denied.add(call_key)
            return False
        return self._apply_permission_choice(call, call_key, result)

    def _apply_permission_choice(
        self,
        call: ToolCall,
        call_key: tuple[str, str],
        choice: "PermissionChoice",
    ) -> bool:
        """根据用户在 Modal 选的档位执行持久化 / 拒绝 / 仅本次。

        返回 True = 放行;False = 拒绝。
        """
        from baozicode.permissions.persistence import append_rule_to_local_yaml
        from baozicode.tui.permission_modal import (
            PermissionChoice as PC,
            derive_glob_pattern,
        )

        if choice == PC.DENY:
            self._previously_denied.add(call_key)
            return False

        # 任何放行档都把"已拒"标记清掉
        self._previously_denied.discard(call_key)

        if choice == PC.ONCE:
            return True

        # SESSION / PERSISTENT:写一条 allow rule,pattern 是 glob 模糊形式
        pattern = derive_glob_pattern(call)
        from baozicode.permissions.types import PermissionRule

        rule = PermissionRule(
            tool=call.name,
            pattern=pattern,
            decision="allow",
        )
        if choice == PC.SESSION:
            # 写 RuleEngine session_rules
            engine = getattr(self.app, "permissions_engine", None)  # type: ignore[attr-defined]
            if engine is not None:
                engine.add_session_rule(rule)
                self._append_info(
                    f"已为当前会话放行: `{call.name}({pattern})`"
                )
            else:
                # 没有 engine 时(理论上不应该发生)退化为 ONCE
                self._append_info(
                    f"⚠ 无 permissions_engine 句柄,SESSION 退化为 ONCE"
                )
            return True

        if choice == PC.PERSISTENT:
            # 写 permissions.local.yaml
            try:
                from pathlib import Path
                project_root = getattr(self.app, "project_root", Path.cwd())  # type: ignore[attr-defined]
                append_rule_to_local_yaml(rule, project_root)
                self._append_info(
                    f"已永久放行(写入 `permissions.local.yaml`): "
                    f"`{call.name}({pattern})`"
                )
            except Exception as exc:  # noqa: BLE001
                self._append_error(
                    f"写 permissions.local.yaml 失败: {exc}。规则未生效。"
                )
            return True

        # 兜底(不应到这)
        return False

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
            config=app.config,
            max_iterations=app.config.active_agent().max_iterations,
            plan_mode=self.plan_mode,
            permission_callback=self._permission_callback,
            # v0.5:五层防御 — 传 merged 状态 + 稳定 engine + 当前 mode
            session_mode=app.effective_mode(),
            merged_permissions=getattr(app, "permissions_v5", None),
            permissions_engine=getattr(app, "permissions_engine", None),
            # v0.7:上下文压缩编排器 — `/compact` 走 request_compact + 自动按预算压缩
            compact_ctx=getattr(app, "compact_ctx", None),
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
            # v0.5:重试 previously denied 调用时弹 🔧 提示卡(在 ToolCallCard 之前)
            call_key = _call_deny_key(call)
            if call_key in self._previously_denied:
                self._show_denial_for_retry(call)
            card = ToolCallCard(call)
            await scroll.mount(card)
            scroll.scroll_end(animate=False)
            self._pending_calls[call.id] = (card, call_key)
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
            entry = self._pending_calls.pop(result.tool_call_id, None)
            if entry is not None:
                card, call_key = entry
                if result.is_error:
                    card.mark_denied(_short_error(result.content))
                    # v0.5:L1-L4 系统拒 / L5 user 拒 都计入 session_deny_count
                    if _is_deny_result(result.content):
                        self._session_deny_count += 1
                        self._previously_denied.add(call_key)
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
        # v0.5:权限 mode 段(strict/default/permissive),给用户一眼看到当前档位
        perm_mode = self._current_permission_mode()
        if idle:
            text = (
                f"● {mode} · {backend}/{model} · {auto} · {perm_mode} · idle"
            )
        else:
            text = (
                f"● {mode} · {backend}/{model} · {auto} · {perm_mode} · "
                f"{progress_text or '...'}"
            )
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


def _call_deny_key(call: ToolCall) -> tuple[str, str]:
    """生成 ToolCall 的去重 key — 用于 `previously_denied` 集合。

    key 形如 (tool_name, json.dumps(arguments, sort_keys=True))。
    """
    return (call.name, json.dumps(call.arguments, sort_keys=True, ensure_ascii=False))


def _is_deny_result(content: str) -> bool:
    """判断 tool_result.content 是否来自 v0.5 拒绝路径(L1-L4 / L5 user / v0.2 兼容)。

    用于 `_handle_event.tool_result` 决定是否累加 session_deny_count + 加入
    previously_denied 集合。普通工具执行错误(e.g. file not found、timeout)
    不算 deny。
    """
    if not content:
        return False
    # v0.5 L1-L4:由 permissions 管道产出
    if content.startswith("工具调用被 "):
        return True
    # v0.5 L5 user:"Tool {name} denied by user."
    if "denied by user" in content:
        return True
    # v0.5 L5 no-callback:"Tool {name} requires user confirmation (..., default deny)."
    if "default deny" in content:
        return True
    # v0.2 兼容:旧 deny 文案
    if "denied by permissions" in content:
        return True
    return False


__all__ = ["ChatScreen", "ModelSelectScreen", "StatusBar", "SLASH_COMMANDS"]
