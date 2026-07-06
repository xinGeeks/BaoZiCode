"""工具调用卡片 — ToolCallCard + ToolResultCard,Static 组件。"""

from __future__ import annotations

import json

from textual.widgets import Static

from baozicode.tools.base import ToolCall, ToolResult

MAX_RESULT_DISPLAY_BYTES = 2_000


def _format_args(args: dict) -> str:
    """把 arguments dict 渲染成紧凑的单行/多行 JSON。"""
    if not args:
        return ""
    try:
        rendered = json.dumps(args, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        rendered = repr(args)
    return rendered


class ToolCallCard(Static):
    """🔧 + 工具名 + 参数(调用发生时挂载)。"""

    DEFAULT_CSS = """
    ToolCallCard {
        height: auto;
        margin: 1 0;
        padding: 0 1;
        background: $boost;
        border-left: thick $primary;
    }
    """

    def __init__(self, call: ToolCall, *, denied: bool = False, error: str | None = None) -> None:
        body = self._render_body(call, denied=denied, error=error)
        super().__init__(body, classes="tool-call-card")
        self.call = call

    def _render_body(self, call: ToolCall, *, denied: bool, error: str | None) -> str:
        icon = "✗" if denied else "🔧"
        header = f"{icon} {call.name}"
        if denied:
            header += "  **DENIED**"
        elif error:
            header += f"  ⚠ {error}"
        body = f"{header}\n```json\n{_format_args(call.arguments)}\n```"
        return body

    def mark_denied(self, reason: str) -> None:
        """就地更新为 DENIED 样式(agent loop 在拒绝时调用)。"""
        body = self._render_body(self.call, denied=True, error=reason)
        self.update(body)


class ToolResultCard(Static):
    """📄 + 结果内容(超长截断 + 提示)。"""

    DEFAULT_CSS = """
    ToolResultCard {
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
        background: $panel;
        border-left: thick $secondary;
    }
    """

    def __init__(self, result: ToolResult) -> None:
        body = self._render_body(result)
        super().__init__(body, classes="tool-result-card")
        self.result = result

    def _render_body(self, result: ToolResult) -> str:
        icon = "✗" if result.is_error else "📄"
        header = f"{icon} result"
        content = result.content
        if len(content.encode("utf-8")) > MAX_RESULT_DISPLAY_BYTES:
            truncated = content.encode("utf-8")[:MAX_RESULT_DISPLAY_BYTES].decode("utf-8", errors="replace")
            content = truncated + f"\n... [truncated: {MAX_RESULT_DISPLAY_BYTES} bytes shown]"
        return f"{header}\n```\n{content}\n```"


__all__ = ["ToolCallCard", "ToolResultCard"]