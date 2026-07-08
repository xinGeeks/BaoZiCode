"""v0.7 上下文管理 — 两层 token 预算压缩 + 摘要 + 熔断 + /compact。

公开 API:
- `maybe_compact(messages, *, trigger, ctx)`:Agent 主循环入口,先跑 Layer 1
  offload,再决定是否跑 Layer 2 summary
- `OffloadEngine`:Layer 1 — 单 block / 单 message 聚合 offload 到磁盘
- `CompactEngine`:Layer 2 — 6 段结构化摘要 + 熔断
- `CompactionError`:连续失败熔断后抛的异常
- `estimate_message_tokens` / `estimate_messages_tokens`:token 估算(无 tiktoken)
- `ContextStorage`:磁盘 `.baozicode/context/<session>/` 读写
"""

from __future__ import annotations

from baozicode.context.estimator import (
    estimate_message_tokens,
    estimate_messages_tokens,
)
from baozicode.context.layer1 import OffloadEngine, build_preview
from baozicode.context.layer2 import CompactEngine
from baozicode.context.orchestrator import MaybeCompactContext, maybe_compact
from baozicode.context.schema import (
    CompactionError,
    CompactionResult,
    CompactionTelemetry,
    ContextConfig,
)
from baozicode.context.storage import ContextStorage

__all__ = [
    "CompactEngine",
    "CompactionError",
    "CompactionResult",
    "CompactionTelemetry",
    "ContextConfig",
    "ContextStorage",
    "MaybeCompactContext",
    "OffloadEngine",
    "build_preview",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "maybe_compact",
]