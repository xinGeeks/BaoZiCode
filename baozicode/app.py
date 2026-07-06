"""BaoZiCode Textual App 入口。"""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from baozicode.agent import Agent
from baozicode.agent.events import UsageStats
from baozicode.config.schema import AppConfig, BackendName, BackendConfig
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import LLMClient
from baozicode.llm.factory import create_client
from baozicode.tui.chat_screen import ChatScreen


class BaoZiCodeApp(App):
    """BaoZiCode 主应用。"""

    CSS_PATH = "tui/styles.tcss"
    BINDINGS = [Binding("ctrl+c", "quit", "Quit")]

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config: AppConfig = config
        self.conversation: ConversationManager = ConversationManager()
        self.llm_client: LLMClient = create_client(config)
        self._current_agent: Agent | None = None
        self.session_usage: UsageStats = UsageStats()
        self.plan_ready: bool = False

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
