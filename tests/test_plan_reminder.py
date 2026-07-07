"""PlanModeReminder 节奏控制测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.prompt.reminder import PlanModeReminder


def _make(plan_mode: bool, interval: int = 5) -> PlanModeReminder:
    return PlanModeReminder(plan_mode=plan_mode, interval=interval)


def test_first_iteration_emits() -> None:
    r = _make(plan_mode=True)
    assert r.should_emit(1) is True


def test_normal_mode_never_emits() -> None:
    r = _make(plan_mode=False)
    for i in range(1, 20):
        assert r.should_emit(i) is False


def test_interval_5_emits_at_1_6_11_16() -> None:
    """interval=5 时按设计 cadence:1 (首轮) + 6, 11, 16, 21, ...

    Note: plan 原文的 assertions (5, 10, 15) 与设计文档/interval=3 测试不一致。
    设计是"从 2 开始算",即 (iteration - 1) % interval == 0。
    """
    r = _make(plan_mode=True, interval=5)
    assert r.should_emit(1) is True
    assert r.should_emit(6) is True
    assert r.should_emit(11) is True
    assert r.should_emit(16) is True
    assert r.should_emit(5) is False
    assert r.should_emit(7) is False
    assert r.should_emit(9) is False
    assert r.should_emit(10) is False


def test_interval_3_emits_at_1_4_7_10() -> None:
    r = _make(plan_mode=True, interval=3)
    # iteration 1 (首轮) + iteration 4, 7, 10 (interval=3, 从 2 开始算)
    assert r.should_emit(1) is True
    assert r.should_emit(4) is True
    assert r.should_emit(7) is True
    assert r.should_emit(10) is True
    assert r.should_emit(2) is False
    assert r.should_emit(3) is False
