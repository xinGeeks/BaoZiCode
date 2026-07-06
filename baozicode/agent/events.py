"""Agent 事件契约 — Agent 推给 TUI 的所有事件都用这一个 dataclass。

`type` 字段做判别:
- "text": LLM 文本片段,实时推给界面
- "tool_call": 一个 ToolCall 即将执行(已过 permission check)
- "tool_result": 一个 ToolResult 刚产生
- "usage": Token 用量(本轮 + 会话累计)
- "progress": 进度(iteration / max / phase)
- "done": Agent 结束,带 StopReason
- "error": 致命错误,TUI 渲染 ✗

`StopReason` 是 done 事件的 reason 字段,7 个值:
- COMPLETED: 模型本轮没说"要工具",任务完成
- MAX_ITERATIONS_REACHED: 迭代上限兜底
- USER_CANCELLED: 用户按 Esc/Ctrl+C
- UNKNOWN_TOOL_HALLUCINATION: 同一未知工具名连续 2 次
- DENIALS_EXCEEDED: 同一工具名累计被拒 3 次
- FAILED_TOOL_LOOP: 同 (name, error_hash) 连续 3 次失败
- STREAM_ERROR: LLM SDK 抛异常,被 Agent 捕获
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class StopReason(str, Enum):
    """Agent run 的终止原因。"""

    COMPLETED = "completed"
    MAX_ITERATIONS_REACHED = "max_iterations_reached"
    USER_CANCELLED = "user_cancelled"
    UNKNOWN_TOOL_HALLUCINATION = "unknown_tool_hallucination"
    DENIALS_EXCEEDED = "denials_exceeded"
    FAILED_TOOL_LOOP = "failed_tool_loop"
    STREAM_ERROR = "stream_error"


AgentEventType = Literal[
    "text", "tool_call", "tool_result", "usage", "progress", "done", "error"
]


@dataclass(frozen=True)
class UsageStats:
    """Token 用量统计。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "UsageStats") -> "UsageStats":
        return UsageStats(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


ProgressPhase = Literal["streaming", "tool_exec", "checking"]


@dataclass(frozen=True)
class Progress:
    """进度事件最小集。"""

    iteration: int
    max: int
    phase: ProgressPhase


@dataclass(frozen=True)
class AgentEvent:
    """Agent 推给 TUI 的事件。frozen 保证 hashable,避免下游意外改 payload。"""

    type: AgentEventType
    payload: Any = None

    @classmethod
    def text(cls, chunk: str) -> "AgentEvent":
        return cls(type="text", payload=chunk)

    @classmethod
    def tool_call(cls, call) -> "AgentEvent":
        """call 是 ToolCall 实例。"""
        return cls(type="tool_call", payload=call)

    @classmethod
    def tool_result(cls, result) -> "AgentEvent":
        """result 是 ToolResult 实例。"""
        return cls(type="tool_result", payload=result)

    @classmethod
    def usage(cls, this_turn: UsageStats, session_total: UsageStats) -> "AgentEvent":
        return cls(
            type="usage",
            payload={"this_turn": this_turn, "session_total": session_total},
        )

    @classmethod
    def progress(cls, iteration: int, max_iter: int, phase: ProgressPhase) -> "AgentEvent":
        return cls(
            type="progress",
            payload=Progress(iteration=iteration, max=max_iter, phase=phase),
        )

    @classmethod
    def done(cls, reason: StopReason) -> "AgentEvent":
        return cls(type="done", payload=reason)

    @classmethod
    def error(cls, message: str) -> "AgentEvent":
        return cls(type="error", payload=message)


__all__ = [
    "AgentEvent",
    "AgentEventType",
    "Progress",
    "ProgressPhase",
    "StopReason",
    "UsageStats",
]
