"""v0.7 maybe_compact 编排器 — Layer 1 (offload) + Layer 2 (summary) 的调度入口。

设计:
- 始终先跑 Layer 1(廉价,幂等,降低单条消息体积)
- 估算 token → 若仍 > `context_window - reserve_tokens` → 跑 Layer 2
- `trigger="auto"` 走 `reserve_tokens_auto=13K`,`trigger="manual"` 走 `reserve_tokens_manual=3K`
- Layer 2 抛 `CompactionError` → 不改 messages,`result.triggered=False, failure_kind="compact_error"`
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from baozicode.context.layer1 import OffloadEngine
from baozicode.context.schema import CompactionResult, CompactionTelemetry, ContextConfig
from baozicode.context.storage import ContextStorage
from baozicode.llm.base import LLMClient, Message

__all__ = ["MaybeCompactContext", "maybe_compact"]

log = logging.getLogger(__name__)


@dataclass
class MaybeCompactContext:
    """`maybe_compact` 所需的依赖集合 — 避免传 5+ 参数。

    Agent 启动时构造一次,Agent Loop 每轮迭代复用。
    """

    llm: LLMClient
    storage: ContextStorage
    config: ContextConfig
    telemetry: CompactionTelemetry


async def maybe_compact(
    messages: list[Message],
    *,
    trigger: Literal["auto", "manual"],
    ctx: MaybeCompactContext,
    hook_dispatcher: Any | None = None,
) -> tuple[list[Message], CompactionResult]:
    """v0.7 主入口:Layer 1 + 条件 Layer 2。

    返回 `(new_messages, result)`:
    - Layer 1 必跑(offload oversized blocks)
    - 若 post-Layer-1 token 数 > `context_window - reserve_tokens` → 跑 Layer 2
    - Layer 2 失败 → 返回原 messages + `CompactionResult(triggered=False, failure_kind="compact_error")`

    v1.2:若传 `hook_dispatcher`(Agent 的 HookDispatcher 实例),入口 fire
    `system.compaction` 事件,`payload = {"trigger": trigger, "tokens_before": <len(messages)>}`,
    让用户 hook 进压缩时机。fire 失败仅 log.warning,不打断压缩(fail-open)。
    `hook_dispatcher=None` 时跳过(v0.7-v1.1 向后兼容)。
    """
    from baozicode.context.estimator import estimate_messages_tokens
    from baozicode.context.layer2 import CompactEngine

    # v1.2:system.compaction fire(入口,失败 fail-open)
    if hook_dispatcher is not None:
        try:
            hook_dispatcher.run("system.compaction", {
                "trigger": trigger,
                "tokens_before": len(messages),
            })
        except Exception as exc:
            log.warning("hook system.compaction 异常,继续: %s", exc)

    # Layer 1(总是跑 — 幂等,无副作用,返回新 messages)
    offload_engine = OffloadEngine(storage=ctx.storage, config=ctx.config)
    after_l1 = offload_engine.offload(messages)
    tokens_before = estimate_messages_tokens(messages)
    tokens_after_l1 = estimate_messages_tokens(after_l1)
    budget = ctx.config.context_window_tokens - ctx.config.reserve_tokens

    if tokens_after_l1 <= budget:
        # 不需要 Layer 2 — 但仍返回 Layer 1 offload 后的 messages(让 caller 替换)
        return after_l1, CompactionResult(
            triggered=False,
            tokens_before=tokens_before,
            tokens_after=tokens_after_l1,
            failure_kind="below_threshold",
        )

    # Layer 2
    compact_engine = CompactEngine(llm=ctx.llm, config=ctx.config, telemetry=ctx.telemetry)
    tokens_before = tokens_after_l1
    try:
        new_messages, tokens_after_l2 = await compact_engine.compact(after_l1)
    except Exception:
        return messages, CompactionResult(
            triggered=False,
            tokens_before=tokens_before,
            tokens_after=tokens_before,
            failure_kind="compact_error",
        )
    return new_messages, CompactionResult(
        triggered=True,
        tokens_before=tokens_before,
        tokens_after=tokens_after_l2,
        failure_kind="none",
    )