"""BaoZiCode Textual App 入口。"""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from baozicode.agent import Agent
from baozicode.agent.events import UsageStats
from baozicode.config.schema import AppConfig, BackendName, BackendConfig
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import LLMClient
from baozicode.llm.factory import create_client
from baozicode.permissions import bootstrap as permissions_bootstrap
from baozicode.permissions.engine import RuleEngine
from baozicode.permissions.types import MergedPermissions, PermissionMode
from baozicode.tui.chat_screen import ChatScreen


class BaoZiCodeApp(App):
    """BaoZiCode 主应用。"""

    CSS_PATH = "tui/styles.tcss"
    BINDINGS = [Binding("ctrl+c", "quit", "Quit")]

    def __init__(
        self,
        config: AppConfig,
        *,
        project_root: Path | None = None,
    ) -> None:
        super().__init__()
        self.config: AppConfig = config
        self.conversation: ConversationManager = ConversationManager()
        self.llm_client: LLMClient = create_client(config)
        self._current_agent: Agent | None = None
        self.session_usage: UsageStats = UsageStats()
        self.plan_ready: bool = False

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

    def on_mount(self) -> None:
        self.push_screen(ChatScreen())

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
