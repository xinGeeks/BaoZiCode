"""Permissions 模型 + Agent 应用逻辑的单元测试。"""

import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.loop import Agent
from baozicode.config.schema import AppConfig, BackendConfig, Permissions
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
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


class _NoopLLM(LLMClient):
    async def stream(self, messages: list[Message], system=None, tools=None) -> AsyncIterator[ContentDelta]:
        if False:
            yield ContentDelta(type="text", text="")


def _make_agent(perms: Permissions) -> Agent:
    return Agent(
        llm_client=_NoopLLM(),
        tools=[],
        conversation=ConversationManager(),
        permissions=perms,
        config=_cfg(perms),
    )


def test_default_permissions_when_none() -> None:
    """permissions=None 时 active_permissions() 返回全默认。"""
    cfg = _cfg(None)
    p = cfg.active_permissions()
    assert p.auto_allow == []
    assert p.deny == []
    assert p.batch_confirm is False
    assert p.bash_locked_cwd is False
    print("[OK] default permissions when none")


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
    agent = _make_agent(Permissions(deny=["Bash"]))
    call = ToolCall(id="1", name="Bash", arguments={"command": "ls"})
    assert agent._matches_deny(call)
    print("[OK] deny exact match")


def test_deny_glob_match() -> None:
    """deny=['*sudo*'] 通过 fnmatch 匹配 arguments.command。"""
    agent = _make_agent(Permissions(deny=["*sudo*"]))
    call = ToolCall(id="1", name="Bash", arguments={"command": "sudo rm -rf /"})
    assert agent._matches_deny(call)
    print("[OK] deny glob pattern match")


def test_deny_no_match() -> None:
    """deny 不命中时返回 False。"""
    agent = _make_agent(Permissions(deny=["Bash"]))
    call = ToolCall(id="1", name="Read", arguments={"file_path": "/etc/passwd"})
    assert not agent._matches_deny(call)
    print("[OK] no deny match → False")


def test_auto_allow_match() -> None:
    agent = _make_agent(Permissions(auto_allow=["Grep"]))
    call = ToolCall(id="1", name="Grep", arguments={"pattern": "x"})
    assert agent._is_auto_allowed(call)
    print("[OK] auto_allow match")


def test_auto_allow_no_match() -> None:
    agent = _make_agent(Permissions(auto_allow=["Read"]))
    call = ToolCall(id="1", name="Bash", arguments={"command": "ls"})
    assert not agent._is_auto_allowed(call)
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


def test_side_effect_classifications_v030() -> None:
    """v0.3:side_effect 与 risk 不完全等价。Read 等 4 个是 read-only,
    Write/Edit/Bash 是 side_effect=True。"""
    from baozicode.tools.registry import get_all_tools

    effects = {t.name: t.side_effect for t in get_all_tools()}
    assert effects == {
        "Read": False,
        "Write": True,
        "Edit": True,
        "Bash": True,
        "Grep": False,
        "Glob": False,
        "WebFetch": False,
    }
    print("[OK] side_effect classifications")


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
    test_side_effect_classifications_v030()
    print("\nAll permission tests passed.")
