"""Phase 4 — builtin.py 元数据 + 命令矩阵测试。

`build_builtin_defs(get_handler)` 接受一个 handler lookup 函数,返回 10 个
`CommandDef`(handler 引用由 lookup 决定)。这允许 tui/chat_screen 提供
绑定具体实现的 getter,而 builtin.py 不依赖 textual。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.commands.builtin import build_builtin_defs
from baozicode.commands.registry import (
    CommandDef,
    CommandRegistry,
    CommandResult,
    CommandType,
    LocalResult,
    PromptResult,
    UiStateResult,
)


def _noop_handler(args, ctx):
    """stub handler 占位。"""

    async def _h():
        return LocalResult()

    return _h()


def _make_handler_map() -> dict[str, object]:
    """构造一份 hash → stub 的字典,getter 走它。"""
    return {
        "help": _noop_handler,
        "compact": _noop_handler,
        "clear": _noop_handler,
        "plan": _noop_handler,
        "do": _noop_handler,
        "session": _noop_handler,
        "memory": _noop_handler,
        "permission": _noop_handler,
        "status": _noop_handler,
        "review": _noop_handler,
    }


def _getter(handlers: dict[str, object]):
    """构造 get_handler 闭包。"""

    def _g(name: str):
        return handlers[name]

    return _g


def test_register_all_freezes_cleanly() -> None:
    """10 个命令注册 + freeze 不抛。"""
    reg = CommandRegistry()
    for d in build_builtin_defs(_getter(_make_handler_map())):
        reg.register(d)
    reg.freeze()
    print("[OK] 10 builtins register + freeze cleanly")


def test_all_ten_resolvable() -> None:
    """10 个主名都能查到。"""
    reg = CommandRegistry()
    for d in build_builtin_defs(_getter(_make_handler_map())):
        reg.register(d)
    reg.freeze()
    expected = {"help", "compact", "clear", "plan", "do",
                "session", "memory", "permission", "status", "review"}
    found = {d.name for d in reg.all_visible()}
    assert found == expected, f"missing/extra: {expected ^ found}"
    print(f"[OK] all 10 names resolvable: {sorted(found)}")


def test_permissions_alias_resolves_to_permission() -> None:
    """/permissions alias → 主名 permission。"""
    reg = CommandRegistry()
    for d in build_builtin_defs(_getter(_make_handler_map())):
        reg.register(d)
    reg.freeze()
    by_alias = reg.lookup("permissions")
    by_primary = reg.lookup("permission")
    assert by_alias is by_primary
    assert by_primary is not None
    assert by_primary.name == "permission"
    assert ("permissions",) in by_primary.aliases or "permissions" in by_primary.aliases
    print("[OK] /permissions alias → /permission")


def test_command_type_matrix() -> None:
    """每个命令的 type 与命名矩阵一致。"""
    reg = CommandRegistry()
    for d in build_builtin_defs(_getter(_make_handler_map())):
        reg.register(d)
    reg.freeze()
    expected_types = {
        "help": CommandType.LOCAL,
        "compact": CommandType.UI_STATE,
        "clear": CommandType.UI_STATE,
        "plan": CommandType.UI_STATE,
        "do": CommandType.UI_STATE,
        "session": CommandType.UI_STATE,
        "memory": CommandType.LOCAL,
        "permission": CommandType.UI_STATE,
        "status": CommandType.LOCAL,
        "review": CommandType.PROMPT,
    }
    for name, expected_type in expected_types.items():
        d = reg.lookup(name)
        assert d is not None, f"no def for {name}"
        assert d.type == expected_type, (
            f"{name} expected type={expected_type}, got {d.type}"
        )
    print("[OK] type matrix matches spec")


def test_no_builtins_hidden() -> None:
    """v0.9 没有 hidden=True 的命令。"""
    reg = CommandRegistry()
    for d in build_builtin_defs(_getter(_make_handler_map())):
        reg.register(d)
    reg.freeze()
    for d in reg.all_registered():
        assert not d.hidden, f"{d.name} should not be hidden"
    print("[OK] no hidden builtins")


def test_lookup_case_insensitive_for_builtins() -> None:
    """大小写不敏感对所有 10 个命令工作。"""
    reg = CommandRegistry()
    for d in build_builtin_defs(_getter(_make_handler_map())):
        reg.register(d)
    reg.freeze()
    for name in ("HELP", "Compact", "STATUS", "ReViEw"):
        assert reg.lookup(name) is not None, f"case mismatch for {name}"
    print("[OK] case-insensitive lookup for builtins")


def test_stub_handlers_run() -> None:
    """stub handler 真实可调用 + 返 LocalResult。"""
    import asyncio

    reg = CommandRegistry()
    handlers = _make_handler_map()
    for d in build_builtin_defs(_getter(handlers)):
        reg.register(d)
    reg.freeze()

    class _StubCtx:
        app = object()
        config = object()
        def show_info(self, t): pass
        def show_error(self, t): pass
        def send_to_agent(self, t): pass
        def switch_mode(self, m): pass
        def get_token_usage(self): return None
        def refresh_status(self): pass
        def push_modal(self, s): return None

    help_def = reg.lookup("help")
    r1 = asyncio.run(help_def.handler("", _StubCtx()))
    assert isinstance(r1, LocalResult)
    print("[OK] stub handlers return LocalResult")


def test_no_duplicate_registration() -> None:
    """重复注册同一个 name → freeze panic。"""
    reg = CommandRegistry()
    for d in build_builtin_defs(_getter(_make_handler_map())):
        reg.register(d)
    # 手动再注册一个 'help' → 应撞名
    reg.register(CommandDef(name="help", handler=_noop_handler))
    with pytest.raises(SystemExit):
        reg.freeze()
    print("[OK] duplicate registration panics")


def test_getter_invoked_once_per_name() -> None:
    """get_handler 应该被调 10 次(每个 def 一次)。"""
    calls: list[str] = []

    def _g(name: str):
        calls.append(name)
        return _noop_handler

    defs = build_builtin_defs(_g)
    assert len(defs) == 10
    assert len(calls) == 10
    assert set(calls) == {
        "help", "compact", "clear", "plan", "do",
        "session", "memory", "permission", "status", "review",
    }
    print("[OK] getter invoked once per built-in")
