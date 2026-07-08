"""Phase 1 — CommandRegistry 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.commands.registry import (
    CommandDef,
    CommandRegistry,
    CommandType,
)


def _noop_handler(args, ctx):
    """stub handler,供测试用。"""

    async def _h():
        from baozicode.commands.registry import LocalResult
        return LocalResult()

    return _h()


def test_register_minimal() -> None:
    """只填必需字段也能注册成功。"""
    reg = CommandRegistry()
    reg.register(CommandDef(name="foo", handler=_noop_handler))
    reg.freeze()
    assert reg.lookup("foo") is not None
    print("[OK] register minimal command")


def test_register_default_aliases_empty() -> None:
    """默认 aliases 是空 tuple。"""
    d = CommandDef(name="foo", handler=_noop_handler)
    assert d.aliases == ()
    assert d.params_hint is None
    assert d.hidden is False
    assert d.type == CommandType.LOCAL
    print("[OK] default field values")


def test_register_invalid_name_uppercase() -> None:
    """名字含大写 → ValueError。"""
    reg = CommandRegistry()
    with pytest.raises(ValueError, match="命令名不合法"):
        reg.register(CommandDef(name="Foo", handler=_noop_handler))
    print("[OK] uppercase name rejected")


def test_register_invalid_name_symbol() -> None:
    """名字含符号 → ValueError。"""
    reg = CommandRegistry()
    with pytest.raises(ValueError, match="命令名不合法"):
        reg.register(CommandDef(name="foo_bar", handler=_noop_handler))
    print("[OK] underscore rejected")


def test_register_invalid_name_start_digit() -> None:
    """名字以数字开头 → ValueError。"""
    reg = CommandRegistry()
    with pytest.raises(ValueError, match="命令名不合法"):
        reg.register(CommandDef(name="9foo", handler=_noop_handler))
    print("[OK] leading digit rejected")


def test_register_invalid_alias() -> None:
    """别名非法 → ValueError。"""
    reg = CommandRegistry()
    with pytest.raises(ValueError, match="命令名不合法"):
        reg.register(
            CommandDef(name="foo", aliases=("BAR",), handler=_noop_handler)
        )
    print("[OK] invalid alias rejected")


def test_freeze_alias_collision_panics() -> None:
    """两个 alias 撞名 → SystemExit,消息含双方名字。"""
    reg = CommandRegistry()
    reg.register(CommandDef(
        name="a", aliases=("shared",), handler=_noop_handler
    ))
    reg.register(CommandDef(
        name="b", aliases=("shared",), handler=_noop_handler
    ))
    with pytest.raises(SystemExit) as exc:
        reg.freeze()
    msg = str(exc.value)
    assert "shared" in msg
    assert "a" in msg and "b" in msg
    print("[OK] alias collision panics with both names")


def test_freeze_name_collides_with_alias() -> None:
    """A.name == B.aliases[0] → SystemExit。"""
    reg = CommandRegistry()
    reg.register(CommandDef(name="x", handler=_noop_handler))
    reg.register(CommandDef(name="y", aliases=("x",), handler=_noop_handler))
    with pytest.raises(SystemExit):
        reg.freeze()
    print("[OK] name-vs-alias collision panics")


def test_freeze_unique_ok() -> None:
    """所有名字唯一 → 不抛。"""
    reg = CommandRegistry()
    reg.register(CommandDef(name="a", handler=_noop_handler))
    reg.register(CommandDef(
        name="b", aliases=("c", "d"), handler=_noop_handler,
    ))
    reg.freeze()
    assert reg.lookup("a") is not None
    assert reg.lookup("c") is not None
    assert reg.lookup("d") is not None
    print("[OK] unique registration freezes cleanly")


def test_lookup_case_insensitive() -> None:
    """lookup 对输入走 lowercase。"""
    reg = CommandRegistry()
    reg.register(CommandDef(name="review", handler=_noop_handler))
    reg.freeze()
    assert reg.lookup("REVIEW") is not None
    assert reg.lookup("Review") is not None
    assert reg.lookup("review") is not None
    print("[OK] case-insensitive lookup")


def test_lookup_alias() -> None:
    """alias 命中返回主 def。"""
    reg = CommandRegistry()
    main = CommandDef(name="permission", aliases=("permissions",), handler=_noop_handler)
    reg.register(main)
    reg.freeze()
    assert reg.lookup("permissions") is main
    assert reg.lookup("permission") is main
    print("[OK] alias resolves to primary")


def test_lookup_missing() -> None:
    """未命中 → None。"""
    reg = CommandRegistry()
    reg.register(CommandDef(name="foo", handler=_noop_handler))
    reg.freeze()
    assert reg.lookup("nope") is None
    print("[OK] missing lookup returns None")


def test_register_after_freeze_raises() -> None:
    """freeze 后再 register → RuntimeError。"""
    reg = CommandRegistry()
    reg.register(CommandDef(name="foo", handler=_noop_handler))
    reg.freeze()
    with pytest.raises(RuntimeError, match="已 freeze"):
        reg.register(CommandDef(name="bar", handler=_noop_handler))
    print("[OK] register after freeze rejected")


def test_all_visible_excludes_hidden() -> None:
    """hidden 命令不出现在 all_visible() 中。"""
    reg = CommandRegistry()
    reg.register(CommandDef(name="foo", handler=_noop_handler))
    reg.register(CommandDef(
        name="secret", hidden=True, handler=_noop_handler,
    ))
    reg.freeze()
    visible = reg.all_visible()
    names = [d.name for d in visible]
    assert "foo" in names
    assert "secret" not in names
    # all_registered 保留 hidden
    all_names = [d.name for d in reg.all_registered()]
    assert "secret" in all_names
    print("[OK] hidden excluded from visible but kept in registered")


def test_register_preserves_order() -> None:
    """all_visible 保持 register 顺序。"""
    reg = CommandRegistry()
    for name in ("a", "b", "c"):
        reg.register(CommandDef(name=name, handler=_noop_handler))
    reg.freeze()
    assert [d.name for d in reg.all_visible()] == ["a", "b", "c"]
    print("[OK] register order preserved")


def test_freeze_idempotent() -> None:
    """反复 freeze 不出错。"""
    reg = CommandRegistry()
    reg.register(CommandDef(name="foo", handler=_noop_handler))
    reg.freeze()
    reg.freeze()  # 第二次 no-op
    assert reg.lookup("foo") is not None
    print("[OK] freeze is idempotent")
