"""Phase 3 — CommandContext Protocol + 导入审计测试。

`CommandContext` 是 Protocol,运行时检查通过;真正的 TextualCommandContext
在 tui/chat_screen.py 里构造(Phase 6),但能独立验证:
1. 任何 duck-typed 类满足 Protocol
2. `baozicode.commands.context` 的运行时 import 集纯净
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.commands.context import CommandContext


class _StubContext:
    """满足 CommandContext Protocol 的最小桩类,用于验证接口契约。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.app = object()
        self.config = object()

    def show_info(self, text: str) -> None:
        self.calls.append(("show_info", (text,)))

    def show_error(self, text: str) -> None:
        self.calls.append(("show_error", (text,)))

    def send_to_agent(self, text: str) -> None:
        self.calls.append(("send_to_agent", (text,)))

    def switch_mode(self, new_mode) -> None:
        self.calls.append(("switch_mode", (new_mode,)))

    def get_token_usage(self):
        self.calls.append(("get_token_usage", ()))
        return None

    def refresh_status(self) -> None:
        self.calls.append(("refresh_status", ()))

    def push_modal(self, screen):
        self.calls.append(("push_modal", (screen,)))
        return None


def test_stub_satisfies_protocol() -> None:
    """_StubContext 实例 is CommandContext。"""
    stub = _StubContext()
    assert isinstance(stub, CommandContext)
    print("[OK] stub satisfies CommandContext protocol")


def test_stub_app_property() -> None:
    """app 属性可读。"""
    stub = _StubContext()
    assert stub.app is not None
    print("[OK] app property accessible")


def test_stub_config_property() -> None:
    """config 属性可读。"""
    stub = _StubContext()
    assert stub.config is not None
    print("[OK] config property accessible")


def test_show_info_records_call() -> None:
    """show_info 接收 text 字符串。"""
    stub = _StubContext()
    stub.show_info("hello")
    assert stub.calls == [("show_info", ("hello",))]
    print("[OK] show_info records call")


def test_show_error_records_call() -> None:
    """show_error 接收 text 字符串。"""
    stub = _StubContext()
    stub.show_error("oops")
    assert stub.calls == [("show_error", ("oops",))]
    print("[OK] show_error records call")


def test_send_to_agent_records_call() -> None:
    """send_to_agent 接收 text 字符串。"""
    stub = _StubContext()
    stub.send_to_agent("review this")
    assert stub.calls == [("send_to_agent", ("review this",))]
    print("[OK] send_to_agent records call")


def test_switch_mode_with_string() -> None:
    """switch_mode 接受字符串或 None。"""
    stub = _StubContext()
    stub.switch_mode("strict")
    stub.switch_mode(None)
    assert ("switch_mode", ("strict",)) in stub.calls
    assert ("switch_mode", (None,)) in stub.calls
    print("[OK] switch_mode accepts mode or None")


def test_refresh_status_callable() -> None:
    """refresh_status 不接参。"""
    stub = _StubContext()
    stub.refresh_status()
    assert ("refresh_status", ()) in stub.calls
    print("[OK] refresh_status callable")


def test_get_token_usage_returns() -> None:
    """get_token_usage 返回一个对象(此处 None 占位)。"""
    stub = _StubContext()
    out = stub.get_token_usage()
    assert out is None
    print("[OK] get_token_usage returns sentinel")


def test_push_modal_accepts_screen_and_returns() -> None:
    """push_modal 接受 screen 对象 + 返回值(此处 None)。"""
    stub = _StubContext()
    out = stub.push_modal(object())
    assert out is None
    print("[OK] push_modal callable")


def test_protocol_runtime_checkable() -> None:
    """缺一个方法的类不满足 Protocol。"""
    class _Partial:
        def show_info(self, text): pass
        # 缺 show_error / send_to_agent / ...

    assert not isinstance(_Partial(), CommandContext)
    print("[OK] partial implementation rejected")


def test_commands_context_imports_clean() -> None:
    """commands/context.py 的运行时 import 不应包含业务模块。"""
    import importlib
    import sys

    # 先 import 该模块,使其加入 sys.modules
    mod = importlib.import_module("baozicode.commands.context")
    forbidden = {
        "baozicode.agent",
        "baozicode.tui",
        "baozicode.prompt",
        "baozicode.sessions",
        "baozicode.memory",
    }
    # 检查 sys.modules 里任何 forbidden 模块是不是从 context 触发的
    # (简化版:只要这些模块也已 import,但只检查 context 自己的 deps)
    # 用 mod.__dict__ 看 globals 里的 import 来源
    src = Path(mod.__file__).read_text(encoding="utf-8")
    for f in forbidden:
        # 禁止 'import baozicode.xxx' 直接出现在源码
        assert f"import {f}" not in src, (
            f"context.py 直接 import 业务模块: {f}"
        )
        # TYPE_CHECKING block 是允许的(运行时不会触发)
    print("[OK] context.py 源码无业务模块直接 import")


def test_commands_package_exports_public_api() -> None:
    """baozicode.commands 包导出 7 个公开类型 + 2 个延迟加载 helper。"""
    import baozicode.commands as pkg

    for name in [
        "CommandContext",
        "CommandDef",
        "CommandRegistry",
        "CommandResult",
        "CommandType",
        "LocalResult",
        "UiStateResult",
        "PromptResult",
    ]:
        assert name in pkg.__all__, f"missing {name} in __all__"
    # 延迟加载 helper 应在 __getattr__ 路径上
    assert callable(pkg.__getattr__)
    print("[OK] commands package public API export")
