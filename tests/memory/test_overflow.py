"""v0.8 memory/overflow.py 测试 — 三态机 + 状态转换。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncIterator

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.config.schema import MemoryConfig
from baozicode.llm.base import ContentDelta, LLMClient
from baozicode.memory import MemoryOverflowHandler, MemoryStore, OverflowAction, OverflowState


@pytest.fixture
def small_thresholds(tmp_project_root: Path) -> MemoryConfig:
    """小阈值便于测试快速触发 AUTO_COMPRESS。

    MemoryConfig 有 min 校验(≥ 50/1024/25), 用 model_construct 绕过。
    """
    return MemoryConfig.model_construct(
        enabled=True,
        user_dir=tmp_project_root / "user_memory",
        project_dir=tmp_project_root / "project_memory",
        index_max_lines=10,
        index_max_bytes=512,
        warning_lines=5,
        warning_bytes=256,
        recent_turns_for_update=5,
        auto_compress_per_session=1,
    )


@pytest.fixture
def store(tmp_project_root: Path, small_thresholds: MemoryConfig) -> MemoryStore:
    return MemoryStore(small_thresholds.project_dir, scope="project")


class _NullLLM(LLMClient):
    """不返回任何 token, 用于 _auto_compress 触发失败路径时测试。"""
    async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
        if False:
            yield ContentDelta(type="text", text="")  # pragma: no cover


class _ScriptedLLM(LLMClient):
    def __init__(self, response: str, fail: bool = False) -> None:
        self.response = response
        self.fail = fail

    async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
        if self.fail:
            raise RuntimeError("simulated LLM error")
        if self.response:
            yield ContentDelta(type="text", text=self.response)


# ---- state machine ----


def test_initial_state_is_normal(small_thresholds: MemoryConfig) -> None:
    """handler 初始状态 = NORMAL。"""
    h = MemoryOverflowHandler(small_thresholds, _NullLLM())
    assert h.state == OverflowState.NORMAL
    print("[OK] 初始 NORMAL")


def test_empty_store_returns_noop(store: MemoryStore, small_thresholds: MemoryConfig) -> None:
    """空 store → NORMAL → NOOP。"""
    h = MemoryOverflowHandler(small_thresholds, _NullLLM())
    action = h.check_and_act(store)
    assert action == OverflowAction.NOOP
    assert h.state == OverflowState.NORMAL
    print("[OK] 空 store → NOOP")


def _write_index_with_lines(store: MemoryStore, n_entries: int) -> None:
    """直接写 MEMORY.md, 让 index 有 n_entries 行(每条 3 行)— 绕过 rewrite_index 上限。"""
    lines = ["# Memory Index (project)", ""]
    for i in range(n_entries):
        lines.append(f"## [project] n{i} — note {i}")
        lines.append(f"one liner {i}")
        lines.append("")
    content = "\n".join(lines)
    (store.root / "MEMORY.md").write_text(content, encoding="utf-8")


def test_warn_threshold_triggers_warn_state(
    store: MemoryStore, small_thresholds: MemoryConfig
) -> None:
    """索引在 warn / max 区间 → WARN。"""
    # 3 entries = 9 行, 在 warn_lines=5 之上、max_lines=10 之下(差不多临界)
    # 用 4 entries = 12 行,但我们设 max_lines=10,那已经是 AUTO_COMPRESS
    # 用 2 entries = 6 行 → WARN (>= warn_lines=5 且 < max_lines=10)
    _write_index_with_lines(store, 2)

    h = MemoryOverflowHandler(small_thresholds, _NullLLM())
    action = h.check_and_act(store)
    assert action == OverflowAction.WARN
    assert h.state == OverflowState.WARN
    print("[OK] 接近上限 → WARN")


async def test_max_threshold_triggers_auto_compress(
    store: MemoryStore, small_thresholds: MemoryConfig
) -> None:
    """索引 ≥ max → AUTO_COMPRESS, 调度 _auto_compress。"""
    # 4 entries = 12 行 → >= max_lines=10 → AUTO_COMPRESS
    _write_index_with_lines(store, 4)

    runner_called: list[MemoryStore] = []
    async def runner(s):
        runner_called.append(s)

    h = MemoryOverflowHandler(small_thresholds, _NullLLM())
    action = h.check_and_act(store, auto_compress_runner=runner)
    assert action == OverflowAction.AUTO_COMPRESS_SCHEDULED
    assert h.state == OverflowState.AUTO_COMPRESS
    # 给 task 跑完的宽限
    import asyncio
    await asyncio.sleep(0.05)
    assert len(runner_called) == 1
    assert runner_called[0] is store
    print("[OK] 超上限 → AUTO_COMPRESS_SCHEDULED + 调度 _auto_compress")


def test_second_auto_compress_in_same_session_returns_human_needed(
    store: MemoryStore, small_thresholds: MemoryConfig
) -> None:
    """本 session 已用过自动压缩 → 第二次触发 → HUMAN_NEEDED。"""
    _write_index_with_lines(store, 4)  # 12 行 → AUTO_COMPRESS

    h = MemoryOverflowHandler(small_thresholds, _NullLLM())
    # 第一次: AUTO_COMPRESS
    h.check_and_act(store)
    assert h.state == OverflowState.AUTO_COMPRESS
    # 第二次: limiter 满 → HUMAN_NEEDED
    action2 = h.check_and_act(store)
    assert action2 == OverflowAction.HUMAN_NEEDED
    assert h.state == OverflowState.HUMAN_NEEDED
    print("[OK] 第二次同 session → HUMAN_NEEDED")


def test_reset_session_limiter_clears_counter(small_thresholds: MemoryConfig) -> None:
    """新 session 应重置 limiter。"""
    h = MemoryOverflowHandler(small_thresholds, _NullLLM())
    h._session_limiter = 1  # noqa: SLF001
    h.reset_session_limiter()
    assert h._session_limiter == 0  # noqa: SLF001
    print("[OK] reset_session_limiter → limiter=0")


# ---- _auto_compress ----


async def test_auto_compress_successful_state_returns_to_normal(
    store: MemoryStore, small_thresholds: MemoryConfig
) -> None:
    """_auto_compress 实际未删 → state HUMAN_NEEDED(因为状态没回到 NORMAL/WARN)。"""
    _write_index_with_lines(store, 4)  # 满到 AUTO_COMPRESS

    response = '''```json
{
  "operations": []
}
```'''  # 空 ops → 不删
    h = MemoryOverflowHandler(small_thresholds, _ScriptedLLM(response))
    await h._auto_compress(store)  # noqa: SLF001
    assert h.state == OverflowState.HUMAN_NEEDED
    print("[OK] _auto_compress 无 ops → HUMAN_NEEDED")


async def test_auto_compress_llm_error_state_human_needed(
    store: MemoryStore, small_thresholds: MemoryConfig
) -> None:
    """_auto_compress LLM 抛异常 → state=HUMAN_NEEDED。"""
    _write_index_with_lines(store, 4)  # AUTO_COMPRESS 区间
    h = MemoryOverflowHandler(small_thresholds, _ScriptedLLM("", fail=True))
    await h._auto_compress(store)  # noqa: SLF001
    assert h.state == OverflowState.HUMAN_NEEDED
    print("[OK] _auto_compress LLM 异常 → HUMAN_NEEDED")


async def test_auto_compress_invalid_json_human_needed(
    store: MemoryStore, small_thresholds: MemoryConfig
) -> None:
    """_auto_compress LLM 输出无 fenced JSON → state=HUMAN_NEEDED。"""
    _write_index_with_lines(store, 4)
    h = MemoryOverflowHandler(small_thresholds, _ScriptedLLM("I can't help with that."))
    await h._auto_compress(store)  # noqa: SLF001
    assert h.state == OverflowState.HUMAN_NEEDED
    print("[OK] _auto_compress 解析失败 → HUMAN_NEEDED")


async def test_auto_compress_with_real_delete_brings_back_to_normal(
    store: MemoryStore, small_thresholds: MemoryConfig
) -> None:
    """_auto_compress 实际删一些 notes → state 回到 NORMAL。"""
    _write_index_with_lines(store, 4)  # AUTO_COMPRESS 区间

    def apply(ops):
        # applier 只删前 2 条 + 改写 index 到 1 条
        for op in ops[:2]:
            if op.get("action") == "delete":
                slug = op.get("slug")
                path = store.root / f"{slug}.md"
                if path.exists():
                    path.unlink()
        _write_index_with_lines(store, 1)

    response = '''```json
{
  "operations": [
    {"action": "delete", "slug": "n0"},
    {"action": "delete", "slug": "n1"}
  ]
}
```'''
    h = MemoryOverflowHandler(small_thresholds, _ScriptedLLM(response))
    await h._auto_compress(store, operations_applier=apply)  # noqa: SLF001
    assert h.state == OverflowState.NORMAL
    print("[OK] _auto_compress 实际删 → 回到 NORMAL")


# ---- parse_fenced_json helper ----


def test_parse_fenced_json_valid() -> None:
    from baozicode.memory.overflow import _parse_fenced_json
    text = '''```json
{"operations": []}
```'''
    result = _parse_fenced_json(text)
    assert result == {"operations": []}


def test_parse_fenced_json_no_block() -> None:
    from baozicode.memory.overflow import _parse_fenced_json
    assert _parse_fenced_json("no fence here") is None


def test_parse_fenced_json_invalid_json() -> None:
    from baozicode.memory.overflow import _parse_fenced_json
    text = '''```json
not valid json
```'''
    assert _parse_fenced_json(text) is None


def test_parse_fenced_json_empty() -> None:
    from baozicode.memory.overflow import _parse_fenced_json
    assert _parse_fenced_json("") is None
    assert _parse_fenced_json(None) is None  # type: ignore[arg-type]
