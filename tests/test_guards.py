"""三层 stop guards 的纯函数测试 — 不依赖 LLM/工具,直接喂 GuardState。

v0.5 变化:
- `check_deny_threshold` 退化为 no-op(D7:deny 不再终止 Agent Loop)
- `record_denial` 改为 `record_denial_warn`,语义不变
- 新 `should_inject_denial_reminder(tool_name, state, threshold)` 替代终止检查
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import StopReason
from baozicode.agent.guards import (
    GuardState,
    check_deny_threshold,
    check_failed_loop,
    check_unknown_tool,
    error_signature,
    record_denial,
    record_denial_warn,
    should_inject_denial_reminder,
)
from baozicode.tools.base import ToolCall


def _call(name: str) -> ToolCall:
    return ToolCall(id=f"id-{name}", name=name, arguments={})


def test_error_signature_is_stable_and_short() -> None:
    """同错误消息应得同一 signature;不同消息得到不同 signature;长度固定 16 字符。"""
    s1 = error_signature("Tool execution failed: PermissionError")
    s2 = error_signature("Tool execution failed: PermissionError")
    s3 = error_signature("Tool execution failed: TimeoutError")
    assert s1 == s2
    assert s1 != s3
    assert len(s1) == 16
    print("[OK] error_signature: stable per message, 16 chars hex")


def test_check_unknown_tool_passes_for_known_name() -> None:
    """未知工具名第一次出现不直接终止,留给模型一次纠错机会。"""
    state = GuardState()
    reason = check_unknown_tool(_call("FakeTool"), state)
    assert reason is None
    assert state.recent_unknown == ["FakeTool"]
    print("[OK] first unknown tool: pass, recorded")


def test_check_unknown_tool_hallucinates_twice_same_name() -> None:
    """同一未知工具连续 2 次 → 终止。"""
    state = GuardState()
    assert check_unknown_tool(_call("FakeTool"), state) is None
    reason = check_unknown_tool(_call("FakeTool"), state)
    assert reason == StopReason.UNKNOWN_TOOL_HALLUCINATION
    print("[OK] 2nd consecutive unknown tool: terminates")


def test_check_unknown_tool_two_different_names_both_pass() -> None:
    """连 2 次不同未知工具不算幻觉(可能是模型在探索)。"""
    state = GuardState()
    assert check_unknown_tool(_call("FakeToolA"), state) is None
    reason = check_unknown_tool(_call("FakeToolB"), state)
    assert reason is None
    assert state.recent_unknown == ["FakeToolA", "FakeToolB"]
    print("[OK] 2 different unknown tools: no hallucination yet")


def test_record_denial_warn_increments_count() -> None:
    """v0.5 新名 `record_denial_warn`,语义同 v0.4 `record_denial`。"""
    state = GuardState()
    record_denial_warn(state, "Bash")
    record_denial_warn(state, "Bash")
    record_denial_warn(state, "Edit")
    assert state.deny_counts == {"Bash": 2, "Edit": 1}
    print("[OK] record_denial_warn: increments per-name")


def test_record_denial_alias_works() -> None:
    """v0.4 兼容:`record_denial` 仍然可用,内部转调 `record_denial_warn`。"""
    state = GuardState()
    record_denial(state, "Bash")
    record_denial(state, "Bash")
    assert state.deny_counts == {"Bash": 2}
    print("[OK] record_denial: v0.4 alias still works")


def test_check_deny_threshold_is_noop_in_v5() -> None:
    """v0.5:`check_deny_threshold` 永远返回 None(deny 不再终止)。"""
    state = GuardState()
    for _ in range(10):
        record_denial_warn(state, "Bash")
    # 即使 10 次拒绝,也不返回终止 reason
    assert check_deny_threshold(_call("Bash"), state) is None
    print("[OK] check_deny_threshold: no-op in v0.5 (deny no longer terminates)")


def test_should_inject_denial_reminder_below_threshold() -> None:
    state = GuardState()
    for _ in range(4):
        record_denial_warn(state, "Bash")
    assert should_inject_denial_reminder("Bash", state, threshold=5) is False
    print("[OK] should_inject_denial_reminder: 4 < 5 → False")


def test_should_inject_denial_reminder_at_threshold() -> None:
    state = GuardState()
    for _ in range(5):
        record_denial_warn(state, "Bash")
    assert should_inject_denial_reminder("Bash", state, threshold=5) is True
    print("[OK] should_inject_denial_reminder: 5 ≥ 5 → True")


def test_should_inject_denial_reminder_above_threshold() -> None:
    state = GuardState()
    for _ in range(10):
        record_denial_warn(state, "Bash")
    assert should_inject_denial_reminder("Bash", state, threshold=5) is True
    print("[OK] should_inject_denial_reminder: 10 ≥ 5 → True")


def test_should_inject_denial_reminder_per_tool() -> None:
    """不同工具的拒绝计数独立。"""
    state = GuardState()
    for _ in range(5):
        record_denial_warn(state, "Bash")
    # Edit 只有 0 次 → False
    assert should_inject_denial_reminder("Edit", state, threshold=5) is False
    # Bash 5 次 → True
    assert should_inject_denial_reminder("Bash", state, threshold=5) is True
    print("[OK] should_inject_denial_reminder: per-tool counts independent")


def test_should_inject_denial_reminder_custom_threshold() -> None:
    """threshold 可由调用方传入(默认 5)。"""
    state = GuardState()
    record_denial_warn(state, "Bash")
    # threshold=1,1 次就够
    assert should_inject_denial_reminder("Bash", state, threshold=1) is True
    # threshold=2,1 次不够
    assert should_inject_denial_reminder("Bash", state, threshold=2) is False
    print("[OK] should_inject_denial_reminder: custom threshold respected")


def test_check_failed_loop_signature_groups_distinct_messages() -> None:
    """同 (name, hash) 才算同一循环;不同错误消息不算。"""
    state = GuardState()
    err1 = "Tool execution failed: file not found"
    err2 = "Tool execution failed: timeout"

    # 同错误连续 3 次 → 触发
    reason = check_failed_loop(_call("Read"), state, err1)
    assert reason is None
    reason = check_failed_loop(_call("Read"), state, err1)
    assert reason is None
    reason = check_failed_loop(_call("Read"), state, err1)
    assert reason == StopReason.FAILED_TOOL_LOOP
    print("[OK] failed_loop: 3 consecutive identical errors trigger")

    # 重置后,改错误信息,3 次也不算
    state2 = GuardState()
    assert check_failed_loop(_call("Read"), state2, err1) is None
    assert check_failed_loop(_call("Read"), state2, err2) is None
    assert check_failed_loop(_call("Read"), state2, err1) is None
    print("[OK] failed_loop: different error messages don't accumulate")


def test_check_failed_loop_window_evicts_old_failures() -> None:
    """recent_failures deque 只有 maxlen=10,旧条目自动淘汰。"""
    state = GuardState()
    # 灌 12 个不同失败,只保留最后 10
    for i in range(12):
        state.recent_failures.append(("Read", f"hash{i}"))
    assert len(state.recent_failures) == 10
    assert state.recent_failures[0] == ("Read", "hash2")
    print("[OK] recent_failures deque evicts at maxlen=10")


if __name__ == "__main__":
    test_error_signature_is_stable_and_short()
    test_check_unknown_tool_passes_for_known_name()
    test_check_unknown_tool_hallucinates_twice_same_name()
    test_check_unknown_tool_two_different_names_both_pass()
    test_record_denial_warn_increments_count()
    test_record_denial_alias_works()
    test_check_deny_threshold_is_noop_in_v5()
    test_should_inject_denial_reminder_below_threshold()
    test_should_inject_denial_reminder_at_threshold()
    test_should_inject_denial_reminder_above_threshold()
    test_should_inject_denial_reminder_per_tool()
    test_should_inject_denial_reminder_custom_threshold()
    test_check_failed_loop_signature_groups_distinct_messages()
    test_check_failed_loop_window_evicts_old_failures()
    print("\nAll guards tests passed.")
