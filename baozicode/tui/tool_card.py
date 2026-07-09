"""工具调用卡片 — ToolCallCard + ToolResultCard,Static 组件。"""

from __future__ import annotations

import json

from textual.widgets import Static

from baozicode.tools.base import ExecutionStatus, ToolCall, ToolResult

MAX_RESULT_DISPLAY_BYTES = 2_000

# v1.2:ToolResult.execution_status → widget CSS class 映射
# 5 种 status 各自一色,None 走默认(向后兼容 v1.0 旧 ToolResult)。
# 颜色具体值在 styles.tcss 配,这里只锁语义 class 名。
EXEC_STATUS_CLASS: dict[ExecutionStatus, str] = {
    "block_l1": "-block-l1",
    "block_hook_pre": "-block-hook-pre",
    "block_permission": "-block-permission",
    "executed_success": "-executed-success",
    "executed_failed": "-executed-failed",
}


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
        # v1.2:按 execution_status 加语义 CSS class(None 走默认,向后兼容)
        extra_class = EXEC_STATUS_CLASS.get(result.execution_status, "")
        classes = "tool-result-card"
        if extra_class:
            classes = f"{classes} {extra_class}"
        super().__init__(body, classes=classes)
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