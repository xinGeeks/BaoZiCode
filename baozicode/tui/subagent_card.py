"""v1.2 TUI — sub-Agent 卡片 widget。

`SubAgentCard(Static)` 是挂载在主对话滚动区下方的小卡片,展示单个 sub-Agent task
的实时状态:

- 折叠(默认):`[task-N] role · state · first 60 chars of last_text`
- 展开:完整 last_text,主对话可滚动看到它;按 `Enter` 切换

状态机由 ChatScreen 的 0.5s 轮询驱动 — 每次 card.refresh(task) 都重渲。
terminal 状态后 30s 自动从 DOM 移除(retention 复用 SubAgentManager 的窗口)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

if TYPE_CHECKING:
    from baozicode.agents.manager import TaskInfo, TaskState


def _first_line_preview(s: str, n: int = 60) -> str:
    """取首行或前 n 字符,折叠卡片用。"""
    if not s:
        return ""
    line = s.split("\n", 1)[0].strip()
    if len(line) > n:
        return line[: n - 1] + "…"
    return line


class SubAgentCard(Static):
    """单个 sub-Agent task 的展示卡片。

    Attributes:
        task_id: TaskInfo.task_id,用于 mount/refresh/dismount 匹配
        role_label: 显示名(definition 的 role 名 / fork 模式为 "fork")
        expanded: 折叠 / 展开状态,Enter 切换
    """

    DEFAULT_CSS = """
    SubAgentCard {
        height: auto;
        padding: 0 1;
        margin: 0 1;
        border: round $accent;
    }
    SubAgentCard.-running {
        border: round $accent;
    }
    SubAgentCard.-terminal {
        border: round $success;
    }
    SubAgentCard.-failed {
        border: round $error;
    }
    """

    def on_click(self, event) -> None:
        """点击展开 / 折叠 toggle。"""
        self.toggle_expanded()
        event.stop()

    def __init__(
        self,
        *,
        task_id: str,
        role_label: str,
        type_label: str,
    ) -> None:
        super().__init__(id=f"subagent-card-{task_id}")
        self._task_id = task_id
        self._role_label = role_label
        self._type_label = type_label
        self._state: TaskState | None = None  # type: ignore[assignment]
        self._last_text: str = ""
        self._expanded: bool = False

    @property
    def task_id(self) -> str:
        return self._task_id

    def toggle_expanded(self) -> None:
        """折叠 / 展开切换。"""
        self._expanded = not self._expanded
        self._render()

    def update_from_task(self, task: "TaskInfo") -> None:
        """TUI 轮询器调 — 用最新 TaskInfo 重渲。

        命名避 Textual Static 自身的 .refresh(layout=...) — 不重名避免冲突。

        终态会自动标 class(TUI 渲染颜色变化)。
        """
        self._state = task.state
        self._last_text = task.last_text or ""
        self._render()
        # 终态标记 class(border 颜色变化)
        if task.state in ("done", "canceled", "timeout"):
            self.add_class("-terminal")
            self.remove_class("-running", "-failed")
        elif task.state == "failed":
            self.add_class("-failed")
            self.remove_class("-running", "-terminal")
        elif task.state in ("running", "pending"):
            self.add_class("-running")
            self.remove_class("-terminal", "-failed")

    def _render(self) -> None:
        state = self._state or "pending"
        if self._expanded:
            body = self._last_text or "(尚无文本输出)"
            self.update(
                f"[bold]◉ {self._role_label}[/bold] "
                f"[dim]({self._type_label} · {state})[/dim]\n"
                f"{body}\n"
                f"[dim]task_id={self._task_id} · Enter 折叠[/dim]"
            )
        else:
            preview = _first_line_preview(self._last_text)
            if preview:
                head = (
                    f"[bold]◉ {self._role_label}[/bold] "
                    f"[dim]({self._type_label} · {state}) ·[/dim] "
                    f"{preview}"
                )
            else:
                head = (
                    f"[bold]◉ {self._role_label}[/bold] "
                    f"[dim]({self._type_label} · {state})[/dim]"
                )
            self.update(
                f"{head}\n"
                f"[dim]task_id={self._task_id} · Enter 展开[/dim]"
            )


__all__ = ["SubAgentCard"]
