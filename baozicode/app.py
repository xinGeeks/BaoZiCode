"""BaoZiCode Textual App 入口。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from textual.app import App
from textual.binding import Binding

from baozicode.agent import Agent
from baozicode.agent.events import UsageStats
from baozicode.config.schema import AppConfig, BackendName, BackendConfig
from baozicode.conversation.manager import ConversationManager
from baozicode.context import (
    CompactionTelemetry,
    ContextConfig,
    ContextStorage,
    MaybeCompactContext,
    maybe_compact,
)
from baozicode.instructions import LoadedInstructions, bootstrap as instructions_bootstrap
from baozicode.llm.base import LLMClient
from baozicode.llm.factory import create_client
from baozicode.mcp.manager import McpClientManager
from baozicode.permissions import bootstrap as permissions_bootstrap
from baozicode.permissions.engine import RuleEngine
from baozicode.permissions.types import MergedPermissions, PermissionMode
from baozicode.skills.bootstrap import SkillSet, bootstrap_skills
from baozicode.sessions import (
    SessionArchiver,
    SessionMeta,
    bootstrap as sessions_bootstrap,
    format_session_id,
    load_session as sessions_load_session,
    migrate_uuid_context_dirs,
)
from baozicode.tui.chat_screen import ChatScreen

log = logging.getLogger(__name__)


class BaoZiCodeApp(App):
    """BaoZiCode 主应用。"""

    CSS_PATH = "tui/styles.tcss"
    BINDINGS = [Binding("ctrl+c", "quit", "Quit")]

    def __init__(
        self,
        config: AppConfig,
        *,
        project_root: Path | None = None,
        mcp_manager: McpClientManager | None = None,
        pending_session_selection: bool = False,
        resume_target: str | None = None,
    ) -> None:
        super().__init__()
        self.config: AppConfig = config
        self.conversation: ConversationManager = ConversationManager()
        self.llm_client: LLMClient = create_client(config)
        self._current_agent: Agent | None = None
        self.session_usage: UsageStats = UsageStats()
        self.plan_ready: bool = False
        # v0.8:SessionArchiver(在 __init__ 末尾 bootstrap 后注入到 conversation)

        # ---- v0.5:五层防御启动 ----
        # 默认以 cwd 作为 project_root(CLI 应在项目根启动)
        # 三层 YAML 加载:local / project / user_global,合并到 MergedPermissions
        self.project_root: Path = (
            project_root.resolve() if project_root else Path.cwd().resolve()
        )
        merged = permissions_bootstrap(self.project_root, config)
        self.permissions_v5: MergedPermissions = merged
        # RuleEngine 持有 session_rules 通道 — 每次 check() 内部会从
        # merged.session_rules 读取,所以单例 engine 即可,无需每调用构造。
        self.permissions_engine: RuleEngine = RuleEngine(merged=merged)
        # /permissions mode 设置后,Agent 在下次构造时使用该 mode
        # 已在跑的 Agent 不受影响(其 __init__ 时刻已捕获 mode)
        self.session_mode: PermissionMode | None = None

        # ---- v0.7 → v0.8:session_id 从 uuid4 hex 改成 YYYYMMDD-HHMMSS-xxxx ----
        # 提前到 hooks 之前:hook audit log 路径含 session_id,必须先分配。
        # 启动时先把 `.baozicode/context/` 下所有 v0.7 uuid 目录迁到新格式,
        # 再为当前 session 分配新 ID — 避免新 ID 撞到刚迁好的目录名。
        context_root = self.project_root / ".baozicode" / "context"
        migrate_uuid_context_dirs(context_root)
        self._session_id: str = self._allocate_session_id(context_root)
        self.context_storage: ContextStorage = ContextStorage(
            project_root=self.project_root, session_id=self._session_id
        )
        self.compaction_telemetry: CompactionTelemetry = CompactionTelemetry()
        # 编排器 ctx — Agent 启动时拿一份;每次 /compact 也可以直接拿
        self.compact_ctx: MaybeCompactContext = MaybeCompactContext(
            llm=self.llm_client,
            storage=self.context_storage,
            config=self._build_context_config(trigger="auto"),
            telemetry=self.compaction_telemetry,
        )

        # ---- v1.1:Hooks lifecycle 加载 ----
        # 在 permissions 之后立即跑,传给 Agent 让它在 v1.1 pipeline 用。
        # 任何 HookValidationError → SystemExit(在 load_hooks 调用处接住)。
        # audit_log_path 给定时构造 HookAuditLog:JSONL 写到
        # `<project>/.baozicode/hooks/<session>.audit.jsonl`,100MB 启动期 rotate。
        self.hook_dispatcher: Any = None
        try:
            from baozicode.hooks import load_hooks as hooks_load, HookValidationError
            audit_log_path = (
                self.project_root
                / ".baozicode" / "hooks"
                / f"{self._session_id}.audit.jsonl"
            )
            self.hook_dispatcher = hooks_load(
                config, agent=None, audit_log_path=audit_log_path
            )
        except HookValidationError as exc:
            print(f"ERROR: hooks validation failed:\n{exc}", file=__import__("sys").stderr)
            raise SystemExit(1)

        # ---- v0.8:三层 BaoZiCode.md 加载 ----
        # 启动时扫三层(全无 → 打印 stderr banner),concat + @include 解析后
        # 存到 self.instructions;Agent 构造时取 self.instructions.concatenated。
        self.instructions: LoadedInstructions = instructions_bootstrap(
            self.project_root, config
        )

        # ---- v0.6:MCP 客户端 ----
        # 如果 caller(CLI)已 bootstrap,直接接过来;否则由 on_mount worker 异步 bootstrap
        self.mcp_manager: McpClientManager | None = mcp_manager

        # ---- v0.8:SessionArchiver + sessions 列表 ----
        # sessions.bootstrap 跑过期清理、构造当前 session 的 archiver、列已有 sessions
        archiver, sessions_meta = sessions_bootstrap(
            self.project_root, self.config, self._session_id
        )
        self.archiver: SessionArchiver | None = archiver
        self.sessions_meta: list[SessionMeta] = sessions_meta
        # 把 archiver 接到 conversation(让每条 add_* 透明写 JSONL)
        if archiver is not None:
            self.conversation.set_archiver(archiver)

        # ---- v0.8:启动时 session 选择 / resume_target ----
        # pending_session_selection=True → on_mount 弹选择器(CLI 无 --resume + 有 sessions)
        # resume_target 已有值 → 立刻 resume 该 session(覆盖默认的新会话)
        self.pending_session_selection: bool = pending_session_selection
        self.resume_target: str | None = resume_target

        # ---- v0.9:command registry(空,frozen 等 ChatScreen 注入 handler)----
        from baozicode.commands.registry import CommandRegistry
        self._command_registry: CommandRegistry | None = CommandRegistry()
        self._command_ctx = None  # 由 ChatScreen 注入

        # ---- v1.0:Skills bootstrap ----
        # 启动期扫三级 builtin/user/project Skill 目录 + 构造 SkillSet。
        # 默认 `independent_runner=None` —— chat_screen 启动后注入实际 sub-Agent
        # 编排器(独立模式 Skill 才会用到 runner)。
        # ToolRegistry 用模块级单例(get_default_tool_registry)拿 tool 列表;
        # 默认走 `valid_tools` 校验,任何 allowed-tools 引用不存在的 tool → SystemExit。
        # SkillsConfig 是 v1.0 新增的可选块 — 提供路径覆盖 / kill switch;
        # None 时走 bootstrap 全部默认。
        from baozicode.tools.registry import get_default_tool_registry
        self.skills: SkillSet = bootstrap_skills(
            self.project_root,
            tool_registry=get_default_tool_registry(),
            skills_config=config.skills,
        )
        # 把 load_skill tool 注册到 ToolRegistry —— `tool_type="internal"` 确保
        # 即便 Skill 收窄了白名单,LLM 仍能调用它加载 Skill。
        # 异步注册:App.__init__ 同步上下文 → 推到 on_mount 异步注册(load_skill
        # 在 ChatScreen 第一条消息前一定就绪)。
        self._load_skill_tool_registered: bool = False

    def on_mount(self) -> None:
        # v0.8:CLI 启动时若要求弹 session 选择器,先在 ChatScreen 之前 push
        if self.resume_target is not None:
            # 直接 resume 目标 session(由 CLI 已验证存在)
            self.run_worker(self._startup_resume(self.resume_target), exclusive=True, name="startup-resume")
        elif self.pending_session_selection and self.sessions_meta:
            # 有现存 session,弹 StartupSessionScreen 询问:续哪个 / 新开 / 取消
            self.run_worker(self._startup_session_select(), exclusive=True, name="startup-session")
        self.push_screen(ChatScreen())
        if self.mcp_manager is None and self.config.mcp_servers:
            self.run_worker(self._bootstrap_mcp(), exclusive=True, name="mcp-bootstrap")
        # v1.0:把 load_skill tool 注册到 ToolRegistry(异步,首次 mount 跑一次)
        if not self._load_skill_tool_registered:
            self.run_worker(
                self._register_load_skill_tool(),
                exclusive=True,
                name="skill-load-skill-register",
            )

    async def _startup_session_select(self) -> None:
        """v0.8 启动 session 选择流程:弹 StartupSessionScreen,根据返回值执行动作。

        - session_id string → app.resume_session(id)
        - NEW_SESSION      → app.start_new_session()
        - None(取消)        → 保持默认新 session,log 一下
        """
        from baozicode.tui.startup_session_screen import NEW_SESSION, StartupSessionScreen

        chosen = await self.push_screen(
            StartupSessionScreen(current_session_id=self._session_id),
            wait_for_dismiss=True,
        )
        if chosen is None:
            import logging
            logging.getLogger("baozicode.app").info(
                "session selection cancelled, starting fresh session"
            )
            return
        if chosen == NEW_SESSION:
            self.start_new_session()
            return
        try:
            await self.resume_session(chosen)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("baozicode.app").warning(
                "startup resume failed for %s: %s: %s",
                chosen, type(exc).__name__, exc,
            )

    async def _startup_resume(self, target_id: str) -> None:
        """CLI --resume 触发的启动 resume,失败仅 stderr 警告,不阻塞 TUI。"""
        try:
            await self.resume_session(target_id)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("baozicode.app").warning(
                "startup resume failed for %s: %s: %s",
                target_id, type(exc).__name__, exc,
            )

    async def _bootstrap_mcp(self) -> None:
        """在后台跑 MCP bootstrap;失败降级,只 banner 提示。"""
        from baozicode.mcp import bootstrap as mcp_bootstrap

        try:
            self.mcp_manager = await mcp_bootstrap(self.config)
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("baozicode.app").warning(
                "MCP bootstrap failed: %s: %s", type(exc).__name__, exc,
            )
            self.mcp_manager = None

    async def _register_load_skill_tool(self) -> None:
        """v1.0:把 load_skill tool 注册到模块级 ToolRegistry 单例。

        `tool_type="internal"` 保证白名单收窄时它仍可用。
        注册幂等:重复调只会因撞名抛 ValueError,捕获并标记已注册。
        """
        import logging
        from baozicode.skills.loader import LOAD_SKILL_TOOL
        from baozicode.tools import registry as tool_registry

        try:
            await tool_registry.register_tool(
                LOAD_SKILL_TOOL, self.skills.loader.execute,
                source_label="Skill",
            )
        except ValueError as exc:
            # 撞名(已注册过 / 与 builtin 冲突)—— 视为已注册即可
            logging.getLogger("baozicode.app").debug(
                "load_skill tool 注册跳过: %s", exc,
            )
        finally:
            self._load_skill_tool_registered = True

    async def on_unmount(self) -> None:
        """App 关闭时清理 MCP 客户端 + 上下文压缩文件。"""
        if self.mcp_manager is not None:
            try:
                await self.mcp_manager.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self.mcp_manager = None
        # v0.7:清掉本 session 的 offload 文件,避免泄漏到下次启动
        try:
            self.context_storage.cleanup()
        except Exception:  # noqa: BLE001
            pass

    def _build_context_config(self, *, trigger: str) -> ContextConfig:
        """从 AppConfig 派生一次 ContextConfig(每次 maybe_compact 复用同一份,只在 trigger 切换时重建)。"""
        from baozicode.config.schema import CompactionConfig

        compaction = (
            self.config.active_agent().compaction
            if self.config.active_agent()
            else CompactionConfig()
        )
        return ContextConfig.build(
            context_window_tokens=self.config.effective_context_window(),
            trigger=trigger,  # type: ignore[arg-type]
            compaction=compaction,
        )

    async def run_compact_now(self) -> tuple[bool, str]:
        """v0.7:由 TUI /compact 在 Agent 空闲时直接调用 — 手动触发 Layer 1 + Layer 2。

        返回 `(triggered, status_text)`:
        - `triggered=True, status_text="已压缩:X → Y tokens"`
        - `triggered=False, status_text="..."`(说明未触发或失败)
        """
        from baozicode.context import CompactionResult

        ctx = self.compact_ctx
        ctx.config = self._build_context_config(trigger="manual")
        try:
            new_msgs, result = await maybe_compact(
                self.conversation.to_list(),
                trigger="manual",
                ctx=ctx,
            )
        except Exception as exc:
            return False, f"[compact] failed: {exc}"
        if result.triggered:
            self.conversation.set_messages(new_msgs)
            return True, f"[已压缩:{result.tokens_before} → {result.tokens_after} tokens]"
        return False, f"[compact] 未触发(失败原因:{result.failure_kind})"

    def switch_backend(self, target: BackendName) -> None:
        """切换到 `target` 后端。已经是目标则 no-op。"""
        if target == self.config.backend:
            return
        self.config = self.config.model_copy(update={"backend": target})
        self.llm_client = create_client(self.config)

    def available_backends(self) -> list[tuple[BackendName, BackendConfig]]:
        """返回 4 个后端的列表，用于 /model 选择器。"""
        return self.config.all_backends()

    def current_agent(self) -> Agent:
        """返回当前活跃 Agent 实例(per-run 重建)。"""
        return self._current_agent  # type: ignore[return-value]

    def effective_mode(self) -> PermissionMode:
        """返回当前生效的 PermissionMode(用于 Agent.__init__ 的 session_mode 参数)。

        优先级:`self.session_mode` > `self.permissions_v5.mode`。
        """
        if self.session_mode:
            return self.session_mode
        return self.permissions_v5.mode

    # ---- v0.8:sessions / memory 句柄(TUI slash 命令用)----

    @property
    def session_id(self) -> str:
        """当前 session 的 ID(YYYYMMDD-HHMMSS-xxxx)。"""
        return self._session_id

    def sessions_list(self) -> list[SessionMeta]:
        """返回 sessions 列表(mtime desc),含当前 session。"""
        return list(self.sessions_meta)

    async def resume_session(self, session_id: str) -> SessionMeta:
        """加载 `<sessions_root>/<session_id>.jsonl` 替换当前 conversation。

        - 调 sessions.load_session,处理坏行 / orphan / 超 token / time_gap
        - 替换 `self.conversation` 内容(不重建 manager,保留 in-memory 状态)
        - 切换 archiver 到旧 sid(后续 add_* 继续写到旧 JSONL,实现"续接")
        - 失败 → 抛 ValueError(TUI 弹错误)

        Returns: 恢复的 SessionMeta(用于 /status 显示)
        """
        from baozicode.sessions import list_sessions as sessions_list_sessions
        from baozicode.sessions.archive import SessionArchiver

        sessions_root = self._resolve_sessions_root()
        # 找一个匹配的 SessionMeta
        all_meta = sessions_list_sessions(sessions_root)
        target_meta = next((m for m in all_meta if m.id == session_id), None)
        if target_meta is None:
            raise ValueError(f"session 不存在: {session_id}")

        result = await sessions_load_session(
            session_id, sessions_root,
            context_storage=self.context_storage,
            llm=self.llm_client,
            compact_ctx=self.compact_ctx,
            time_gap_threshold_hours=self.config.active_agent().time_gap_threshold_hours,
        )
        # 替换 conversation 内容(不重建 manager,保留其他状态)
        self.conversation.set_messages(result.messages)
        # 切 session_id 并替换 archiver — 后续 add_* 写到旧 JSONL
        self._session_id = session_id
        if self.config.sessions.enabled:
            new_archiver = SessionArchiver(sessions_root, session_id=session_id)
            # 关旧 archiver 刷盘(在 set_archiver 之前)
            if self.archiver is not None:
                try:
                    self.archiver.close()
                except Exception:  # noqa: BLE001
                    pass
            self.archiver = new_archiver
            self.conversation.set_archiver(new_archiver)
        return target_meta

    def start_new_session(self) -> str:
        """分配新 session_id,清空 conversation,关闭旧 archiver。

        Returns: 新 session_id
        """
        # 关旧 archiver(刷盘)
        if self.archiver is not None:
            try:
                self.archiver.close()
            except Exception:  # noqa: BLE001
                pass
        # 重新走 sessions.bootstrap 流程:分配新 sid、构造新 archiver
        context_root = self.project_root / ".baozicode" / "context"
        new_sid = self._allocate_session_id(context_root)
        archiver, sessions_meta = sessions_bootstrap(
            self.project_root, self.config, new_sid
        )
        self._session_id = new_sid
        self.archiver = archiver
        self.sessions_meta = sessions_meta
        if archiver is not None:
            self.conversation.set_archiver(archiver)
        # 清空 conversation(不触发 archiver,clear() 内部已处理)
        self.conversation.clear()
        # 重置 token 计数
        self.session_usage = UsageStats()
        return new_sid

    def memory_status(self) -> dict[str, object]:
        """返回两层 memory 的状态摘要(供 /memory 命令展示)。

        Returns: {user: {count, last_updated, lines, bytes},
                  project: {count, last_updated, lines, bytes},
                  enabled: bool}
        """
        from baozicode.memory import bootstrap as memory_bootstrap

        result: dict[str, object] = {
            "enabled": self.config.memory.enabled,
            "user": {"count": 0, "lines": 0, "bytes": 0, "last_updated": None},
            "project": {"count": 0, "lines": 0, "bytes": 0, "last_updated": None},
        }
        if not self.config.memory.enabled:
            return result
        try:
            user_store, project_store = memory_bootstrap(self.project_root, self.config)
        except Exception:  # noqa: BLE001
            return result

        for label, store in (("user", user_store), ("project", project_store)):
            try:
                idx = store.read_index()
                entries = idx.entries
                last = max(
                    (e for e in entries),
                    key=lambda e: e.slug,  # 占位,没真实时间字段
                    default=None,
                )
                result[label] = {
                    "count": len(entries),
                    "lines": idx.total_lines,
                    "bytes": idx.total_bytes,
                    "last_updated": None,  # 索引里没时间字段,留 None
                }
            except Exception:  # noqa: BLE001
                pass
        return result

    def _resolve_sessions_root(self) -> Path:
        """解析 sessions 目录的绝对路径。"""
        sessions_dir = Path(self.config.sessions.dir)
        if not sessions_dir.is_absolute():
            sessions_dir = self.project_root / sessions_dir
        return sessions_dir

    def _allocate_session_id(self, context_root: Path) -> str:
        """分配一个当前未使用的 session_id,撞到已有目录时重掷一次。

        通常 4 字符随机 hex 足以在同一秒内避免冲突;若还是撞上,重试一轮。
        """
        for _ in range(8):
            sid = format_session_id(datetime.now())
            if not (context_root / sid).exists():
                return sid
        # 极端 fallback:加毫秒后缀
        sid = format_session_id(datetime.now())
        log.warning(
            "session_id: 8 次重试仍撞名,fallback 至 %s (碰撞)", sid
        )
        return sid
