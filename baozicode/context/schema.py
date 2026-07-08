"""v0.7 上下文压缩数据模型 — schema + 配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from baozicode.config.schema import CompactionConfig

__all__ = [
    "CompactionError",
    "CompactionResult",
    "CompactionTelemetry",
    "ContextConfig",
    "CompactionOutcome",
]


class CompactionError(RuntimeError):
    """Layer-2 摘要连续失败 N 次熔断后抛出。

    Agent 主循环捕获后 yield `AgentEvent.done(StopReason.COMPACTION_FAILED)`。
    """


@dataclass
class CompactionResult:
    """`maybe_compact` 单次调用的结果(单轮 LLM 调用级别)。"""

    triggered: bool
    tokens_before: int
    tokens_after: int
    failure_kind: Literal["none", "compact_error", "below_threshold"] = "none"


@dataclass
class CompactionTelemetry:
    """v0.7:整 session 的压缩统计,供 /status 展示 + CompactionEngine 累加用。

    累加语义:每成功一次 Layer-2 摘要 → `compaction_count += 1`,
    `total_tokens_saved += max(0, before - after)`,
    `last_compact_at = utcnow()`。
    """

    compaction_count: int = 0
    total_tokens_saved: int = 0
    last_compact_at: datetime | None = None


@dataclass
class ContextConfig:
    """maybe_compact 内部用的扁平配置(从 AppConfig 派生)。

    把 AgentConfig.compaction + 后端 context_window + reserve_tokens 三个
    来源打包成一个对象,避免 maybe_compact 的签名 5+ 参数。
    """

    context_window_tokens: int
    reserve_tokens: int
    per_block_threshold: int
    per_message_threshold: int
    recent_window_min_messages: int
    recent_window_tokens: int
    max_summary_tokens: int
    max_consecutive_failures: int

    @classmethod
    def build(
        cls,
        *,
        context_window_tokens: int,
        trigger: Literal["auto", "manual"],
        compaction: CompactionConfig,
    ) -> "ContextConfig":
        """从 AppConfig 派生本次调用的 ContextConfig。

        - `reserve_tokens`:auto 走 `reserve_tokens_auto`,manual 走 `reserve_tokens_manual`
        """
        reserve = (
            compaction.reserve_tokens_auto
            if trigger == "auto"
            else compaction.reserve_tokens_manual
        )
        return cls(
            context_window_tokens=context_window_tokens,
            reserve_tokens=reserve,
            per_block_threshold=compaction.per_block_threshold,
            per_message_threshold=compaction.per_message_threshold,
            recent_window_min_messages=compaction.recent_window_min_messages,
            recent_window_tokens=compaction.recent_window_tokens,
            max_summary_tokens=compaction.max_summary_tokens,
            max_consecutive_failures=compaction.max_consecutive_failures,
        )


CompactionOutcome = Literal["no_action", "layer1_only", "layer2_summary"]
