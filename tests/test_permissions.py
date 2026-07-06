"""Permissions 模型 + ChatScreen 应用逻辑的单元测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.config.schema import AppConfig, BackendConfig, Permissions
from baozicode.tools.base import ToolCall


def _cfg(perms: Permissions | None) -> AppConfig:
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
        permissions=perms,
    )


def test_default_permissions_when_none() -> None:
    """permissions=None 时 active_permissions() 返回全默认。"""
    cfg = _cfg(None)
    p = cfg.active_permissions()
    assert p.auto_allow == []
    assert p.deny == []
    assert p.batch_confirm is False
    assert p.bash_locked_cwd is False
    print("[OK] default permissions when None")


def test_explicit_permissions_override() -> None:
    cfg = _cfg(Permissions(
        auto_allow=["Grep"],
        deny=["Bash"],
        batch_confirm=True,
        bash_locked_cwd=True,
    ))
    p = cfg.active_permissions()
    assert p.auto_allow == ["Grep"]
    assert p.deny == ["Bash"]
    assert p.batch_confirm is True
    assert p.bash_locked_cwd is True
    print("[OK] explicit permissions override defaults")


def test_unknown_permission_key_ignored() -> None:
    """v0.3+ 新增字段时不会破坏 v0.2 配置。"""
    cfg = _cfg(Permissions(
        auto_allow=["Read"],
        future_field_xyz="should be ignored",  # type: ignore[call-arg]
    ))
    p = cfg.active_permissions()
    assert p.auto_allow == ["Read"]
    assert not hasattr(p, "future_field_xyz")
    print("[OK] unknown permission key silently ignored")


def test_deny_exact_match() -> None:
    """deny=['Bash'] 直接匹配 Bash。"""
    from baozicode.tui.chat_screen import ChatScreen
    screen = ChatScreen()
    call = ToolCall(id="1", name="Bash", arguments={"command": "ls"})
    perms = Permissions(deny=["Bash"])
    assert screen._matches_deny(call, perms)
    print("[OK] deny exact match")


def test_deny_glob_match() -> None:
    """deny=['*sudo*'] 通过 fnmatch 匹配 arguments.command。"""
    from baozicode.tui.chat_screen import ChatScreen
    screen = ChatScreen()
    call = ToolCall(id="1", name="Bash", arguments={"command": "sudo rm -rf /"})
    perms = Permissions(deny=["*sudo*"])
    assert screen._matches_deny(call, perms)
    print("[OK] deny glob pattern match")


def test_deny_no_match() -> None:
    """deny 不命中时返回 False。"""
    from baozicode.tui.chat_screen import ChatScreen
    screen = ChatScreen()
    call = ToolCall(id="1", name="Read", arguments={"file_path": "/etc/passwd"})
    perms = Permissions(deny=["Bash"])
    assert not screen._matches_deny(call, perms)
    print("[OK] no deny match → False")


def test_auto_allow_match() -> None:
    from baozicode.tui.chat_screen import ChatScreen
    screen = ChatScreen()
    call = ToolCall(id="1", name="Grep", arguments={"pattern": "x"})
    perms = Permissions(auto_allow=["Grep"])
    assert screen._is_auto_allowed(call, perms)
    print("[OK] auto_allow match")


def test_auto_allow_no_match() -> None:
    from baozicode.tui.chat_screen import ChatScreen
    screen = ChatScreen()
    call = ToolCall(id="1", name="Bash", arguments={"command": "ls"})
    perms = Permissions(auto_allow=["Read"])
    assert not screen._is_auto_allowed(call, perms)
    print("[OK] no auto_allow match")


def test_low_risk_tool_risk() -> None:
    """Read/Grep/Glob/WebFetch 都是 low,Write/Edit/Bash 都是 high。"""
    from baozicode.tools.registry import get_tool

    assert get_tool("Read").risk == "low"
    assert get_tool("Grep").risk == "low"
    assert get_tool("Glob").risk == "low"
    assert get_tool("WebFetch").risk == "low"
    assert get_tool("Write").risk == "high"
    assert get_tool("Edit").risk == "high"
    assert get_tool("Bash").risk == "high"
    print("[OK] tool risk classifications")


if __name__ == "__main__":
    test_default_permissions_when_none()
    test_explicit_permissions_override()
    test_unknown_permission_key_ignored()
    test_deny_exact_match()
    test_deny_glob_match()
    test_deny_no_match()
    test_auto_allow_match()
    test_auto_allow_no_match()
    test_low_risk_tool_risk()
    print("\nAll permission tests passed.")