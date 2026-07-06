"""Agent 包 — v0.3 引入,把 agent 循环从 TUI 抽出来。

模块:
- events: AgentEvent 契约、StopReason 枚举、UsageStats / Progress dataclass
- collector: StreamCollector + TurnSnapshot — 流式收集器,完整 turn 是 Agent 决策唯一可信源
- guards: 三层 stop guards(unknown_tool / denials / failed_loop)
- scheduler: 工具并发调度(方案 B + 方案 C 扩展点)
- loop: Agent 主类,async generator 驱动事件流
"""

from baozicode.agent.events import (
    AgentEvent,
    Progress,
    StopReason,
    UsageStats,
)
from baozicode.agent.collector import StreamCollector, TurnSnapshot
from baozicode.agent.loop import Agent

__all__ = [
    "Agent",
    "AgentEvent",
    "Progress",
    "StopReason",
    "StreamCollector",
    "TurnSnapshot",
    "UsageStats",
]
