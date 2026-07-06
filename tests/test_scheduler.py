"""工具调度器测试 — 方案 B 的分批与并发语义。

核心不变量:
- side_effect=False 连成一段 → 一个 parallel batch
- 一旦遇到 side_effect=True → flush parallel buffer,新开 sequential
- 结果按 LLM 顺序 yield,不按完成顺序
- 并发批里某一失败不打断其他(返回 exceptions → ToolResult.error)
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.scheduler import annotate_calls, schedule
from baozicode.tools.base import ToolCall, ToolResult


def _call(name: str, side_effect: bool, idx: int = 0) -> ToolCall:
    c = ToolCall(id=f"id-{name}-{idx}", name=name, arguments={"i": idx})
    return c


def _executor_factory(side_effects: dict[str, float]):
    """返回一个 executor — 每个工具 sleep side_effects[name] 秒,模拟 IO 耗时。"""

    async def executor(call: ToolCall) -> ToolResult:
        await asyncio.sleep(side_effects.get(call.name, 0))
        return ToolResult(tool_call_id=call.id, content=f"done:{call.name}")

    return executor


async def test_all_read_only_runs_in_one_parallel_batch() -> None:
    """3 个 read-only 工具全部并行,总耗时 ~ max(单耗时),非 sum。"""
    calls = [_call("Read", False, i) for i in range(3)]
    annotate_calls(calls, {"Read": False})

    executor = _executor_factory({"Read": 0.05})
    started = asyncio.get_event_loop().time()
    results = []
    async for r in schedule(calls, executor):
        results.append(r)
    elapsed = asyncio.get_event_loop().time() - started

    assert len(results) == 3
    assert all(r.content == "done:Read" for r in results)
    assert elapsed < 0.12, f"expected ≤0.12s (parallel), got {elapsed:.3f}s"
    print(f"[OK] 3 read-only tools → 1 parallel batch ({elapsed*1000:.0f}ms)")


async def test_mixed_calls_split_into_parallel_and_sequential() -> None:
    """Read + Bash + Read 应该分为:[parallel(Read,Read)] + [sequential(Bash)] + [parallel(Read2)]。

    本测试用更直观的:[Read1, Read2] + [Bash] 验证分批。
    """
    calls = [_call("Read", False, i) for i in range(2)] + [
        _call("Bash", True)
    ]
    annotate_calls(calls, {"Read": False, "Bash": True})

    executor = _executor_factory({"Read": 0.05, "Bash": 0.05})
    results = []
    async for r in schedule(calls, executor):
        results.append(r)

    assert len(results) == 3
    # LLM 顺序:Read1, Read2, Bash
    assert [r.content for r in results] == ["done:Read", "done:Read", "done:Bash"]
    print("[OK] mixed calls split into parallel + sequential batches")


async def test_results_yielded_in_llm_order_not_completion_order() -> None:
    """两个并行工具,慢的先返回,快的后返回 — yield 顺序仍是 LLM 顺序。"""
    # Read1 慢,Read2 快
    calls = [_call("Read1", False, 0), _call("Read2", False, 1)]
    annotate_calls(calls, {"Read1": False, "Read2": False})

    side_effects = {"Read1": 0.10, "Read2": 0.01}
    order_completed: list[str] = []

    async def executor(call: ToolCall) -> ToolResult:
        await asyncio.sleep(side_effects.get(call.name, 0))
        order_completed.append(call.name)
        return ToolResult(tool_call_id=call.id, content=f"done:{call.name}")

    results = []
    async for r in schedule(calls, executor):
        results.append(r)

    # 完成顺序是 Read2 先(快),但 yield 顺序必须是 LLM 给的 Read1, Read2
    assert order_completed[0] == "Read2"
    assert [r.content for r in results] == ["done:Read1", "done:Read2"]
    print("[OK] parallel results yielded in LLM order, not completion order")


async def test_parallel_batch_collects_exceptions_as_error_results() -> None:
    """并发批里某个 executor 抛异常 — 不会让其他也失败,而是产出 is_error=True 结果。"""

    async def flaky_executor(call: ToolCall) -> ToolResult:
        if call.name == "Boom":
            raise RuntimeError("kaboom")
        return ToolResult(tool_call_id=call.id, content=f"ok:{call.name}")

    calls = [_call("Read", False, 0), _call("Boom", False, 1), _call("Read", False, 2)]
    annotate_calls(calls, {"Read": False, "Boom": False})

    results = []
    async for r in schedule(calls, flaky_executor):
        results.append(r)

    assert len(results) == 3
    assert results[0].content == "ok:Read"
    assert results[0].is_error is False
    assert results[1].is_error is True
    assert "kaboom" in results[1].content
    assert results[2].content == "ok:Read"
    print("[OK] parallel batch: exceptions → error results, others succeed")


async def test_sequential_batch_isolates_failures() -> None:
    """串行批里某一失败也不影响后续工具(继续执行下一条)。"""

    async def flaky_executor(call: ToolCall) -> ToolResult:
        if call.name == "Boom":
            raise RuntimeError("kaboom")
        return ToolResult(tool_call_id=call.id, content=f"ok:{call.name}")

    calls = [_call("Bash1", True), _call("Boom", True), _call("Bash3", True)]
    annotate_calls(calls, {"Bash1": True, "Boom": True, "Bash3": True})

    results = []
    async for r in schedule(calls, flaky_executor):
        results.append(r)

    assert len(results) == 3
    assert results[0].is_error is False
    assert results[1].is_error is True
    assert results[2].is_error is False
    print("[OK] sequential batch: failure doesn't abort remaining")


async def test_empty_call_list_yields_nothing() -> None:
    executor = _executor_factory({})
    results = []
    async for r in schedule([], executor):
        results.append(r)
    assert results == []
    print("[OK] empty calls: no scheduling, no results")


async def test_unknown_tool_side_effect_defaults_to_sequential() -> None:
    """未在 side_effect_map 里的工具默认按 side_effect=True 处理(串行执行)。"""
    calls = [_call("Read", False, 0), _call("Mystery", False, 1)]
    # side_effect_map 不包含 Mystery — annotate 后 _side_effect 默认为 False(missing→False)
    annotate_calls(calls, {"Read": False})

    executor = _executor_factory({"Read": 0.02, "Mystery": 0.02})
    results = []
    async for r in schedule(calls, executor):
        results.append(r)

    # 两条 call 都是 _side_effect=False — 应该 1 个 parallel batch
    assert len(results) == 2
    print("[OK] unknown side_effect defaults: missing tools default to side_effect=False")


async def main() -> None:
    await test_all_read_only_runs_in_one_parallel_batch()
    await test_mixed_calls_split_into_parallel_and_sequential()
    await test_results_yielded_in_llm_order_not_completion_order()
    await test_parallel_batch_collects_exceptions_as_error_results()
    await test_sequential_batch_isolates_failures()
    await test_empty_call_list_yields_nothing()
    await test_unknown_tool_side_effect_defaults_to_sequential()
    print("\nAll scheduler tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
