"""工具调度器 — 方案 B 实现 + 方案 C 扩展点。

D5 决策:
- 单一入口 `schedule(calls, executor) -> AsyncIterator[ToolResult]`
- `_split_batches(calls)` 是唯一分批逻辑(方案 C 可替换为 DAG 分析)
- 并发批用 asyncio.gather,串行批按 LLM 顺序逐个 yield
- 并发结果按 LLM 顺序排列,不打乱 conversation 入库顺序
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from baozicode.tools.base import ToolCall, ToolResult


@dataclass
class Batch:
    """一组同质 tool calls。"""

    parallel: bool
    calls: list[ToolCall] = field(default_factory=list)


def _split_batches(calls: list[ToolCall]) -> list[Batch]:
    """按 side_effect 切分 LLM 返回顺序的 tool calls。

    遍历 calls,把连续 side_effect=False 合并为 parallel=True 一批;
    遇到 side_effect=True 时,每个独立一批(sequential)。

    这是方案 C 的唯一扩展点 —— 替换此函数即可实现 DAG 拓扑排序。
    """
    if not calls:
        return []

    batches: list[Batch] = []
    current_parallel: bool | None = None
    current_calls: list[ToolCall] = []

    def flush() -> None:
        nonlocal current_parallel, current_calls
        if current_calls:
            batches.append(Batch(parallel=bool(current_parallel), calls=current_calls))
        current_parallel = None
        current_calls = []

    for call in calls:
        # 调用方需要在传入前把所有 ToolCall 都填充 side_effect 信息;
        # ToolDefinition 在 registry 里查(call 本身没有 side_effect 属性)。
        # 简化:从 call 自身无法判断 — 这里需要外层传入 name→side_effect 的映射。
        # 因此把 side_effect 判断延迟到 schedule 调用前 _annotate 调用处理。
        # 这里只按 parallel 累积;_annotate 阶段把 call 加上 side_effect 字段。
        is_se = bool(getattr(call, "_side_effect", False))
        if current_parallel is None:
            current_parallel = not is_se  # True 表示 parallel
            current_calls.append(call)
        elif (not is_se) == bool(current_parallel):
            current_calls.append(call)
        else:
            flush()
            current_parallel = not is_se
            current_calls.append(call)
    flush()
    return batches


def annotate_calls(
    calls: list[ToolCall], side_effect_map: dict[str, bool]
) -> None:
    """给每个 call 打上 _side_effect 标记,供 _split_batches 读取。

    避免 ToolCall dataclass 改字段(它是 LLM 流产物,带额外字段会污染协议)。
    """
    for c in calls:
        c._side_effect = side_effect_map.get(c.name, False)  # type: ignore[attr-defined]


async def schedule(
    calls: list[ToolCall],
    executor: Callable[[ToolCall], Awaitable[ToolResult]],
) -> AsyncIterator[ToolResult]:
    """调度一批 tool calls,按 LLM 顺序逐个 yield ToolResult。

    `executor` 是单个 tool 的执行包装(权限检查 + execute_tool_call + 错误兜底)。
    """
    if not calls:
        return

    batches = _split_batches(calls)
    for batch in batches:
        if batch.parallel:
            results = await asyncio.gather(
                *(executor(c) for c in batch.calls),
                return_exceptions=True,
            )
            # 按 LLM 顺序 yield(并发完成顺序无所谓)
            for call, result in zip(batch.calls, results):
                if isinstance(result, Exception):
                    yield ToolResult(
                        tool_call_id=call.id,
                        content=f"Tool execution failed: {result}",
                        is_error=True,
                    )
                else:
                    yield result
        else:
            # 串行:逐个 await
            for call in batch.calls:
                try:
                    yield await executor(call)
                except Exception as exc:  # noqa: BLE001
                    yield ToolResult(
                        tool_call_id=call.id,
                        content=f"Tool execution failed: {exc}",
                        is_error=True,
                    )


__all__ = ["Batch", "annotate_calls", "schedule"]
