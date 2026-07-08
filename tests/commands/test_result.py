"""Phase 2 — CommandResult 联合类型测试。

CommandResult 是 Union[LocalResult, UiStateResult, PromptResult],
frozen dataclass 实例化 + match 模式匹配 + isinstance 区分。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.commands.registry import (
    CommandResult,
    LocalResult,
    PromptResult,
    UiStateResult,
)


def _classify(r: CommandResult) -> str:
    """模式匹配 tag 分类(模拟 dispatcher 路由路径)。"""
    match r:
        case LocalResult():
            return "local"
        case UiStateResult():
            return "ui_state"
        case PromptResult(text=t):
            return f"prompt:{t[:10]}"
        case _:
            return "unknown"


def test_local_result_match() -> None:
    """LocalResult 走 match 命中 'local' 分支。"""
    r = LocalResult()
    assert _classify(r) == "local"
    print("[OK] LocalResult matched")


def test_ui_state_result_match() -> None:
    """UiStateResult 走 match 命中 'ui_state' 分支。"""
    r = UiStateResult()
    assert _classify(r) == "ui_state"
    print("[OK] UiStateResult matched")


def test_prompt_result_match() -> None:
    """PromptResult 走 match 命中 'prompt:<text>' 分支。"""
    r = PromptResult(text="请审查 X")
    assert _classify(r) == "prompt:请审查 X"
    print("[OK] PromptResult matched with text")


def test_prompt_result_carries_text() -> None:
    """PromptResult 实例可读 .text 字段。"""
    r = PromptResult(text="hello world")
    assert r.text == "hello world"
    print("[OK] PromptResult.text field")


def test_isinstance_discrimination() -> None:
    """isinstance 三分类工作。"""
    assert isinstance(LocalResult(), LocalResult)
    assert isinstance(UiStateResult(), UiStateResult)
    assert isinstance(PromptResult(text="x"), PromptResult)
    assert not isinstance(LocalResult(), PromptResult)
    print("[OK] isinstance discrimination")


def test_results_are_frozen() -> None:
    """frozen dataclass 禁止修改字段。"""
    r = PromptResult(text="x")
    try:
        r.text = "y"  # type: ignore[misc]
    except Exception as exc:
        assert "frozen" in str(exc).lower() or "FrozenInstanceError" in type(exc).__name__
        print("[OK] PromptResult frozen")
        return
    raise AssertionError("frozen dataclass 应抛异常")


def test_results_construct_with_no_args() -> None:
    """LocalResult / UiStateResult 无参构造。"""
    assert LocalResult() is not None
    assert UiStateResult() is not None
    print("[OK] parameterless construction")


def test_local_and_ui_state_have_no_payload() -> None:
    """LocalResult / UiStateResult 没有 .text 字段(纯标志)。"""
    assert not hasattr(LocalResult(), "text")
    assert not hasattr(UiStateResult(), "text")
    print("[OK] local/ui_state carry no payload")
