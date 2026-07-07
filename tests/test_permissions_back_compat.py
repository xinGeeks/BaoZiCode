"""v0.2 → v0.5 向后兼容测试。

覆盖:
- 旧 `config.yaml:permissions: {auto_allow: [...], deny: [...]}` 仍然工作
  (Agent 走 _v2_executor 路径,无 merged_permissions)
- 同名工具在 v0.2 auto_allow 中 → 直接执行,不走 L5 Modal
- 同名 / glob 在 v0.2 deny 中 → is_error,不走 L5 Modal
- 同时声明 v0.2 permissions + v0.5 permissions_v5 → loader 记 deprecation warning
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import AsyncIterator

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import AgentEvent
from baozicode.agent.loop import Agent
from baozicode.config.schema import Permissions
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.tools.base import ToolCall
from baozicode.tools.registry import get_all_tools

sys.path.insert(0, str(Path(__file__).parent))
from _agent_helpers import make_minimal_config


# ---- mock LLM ----

class _ScriptedLLM(LLMClient):
    def __init__(self, responses: list[list[ContentDelta]]):
        self.responses = responses
        self.call_count = 0

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        script = self.responses[self.call_count - 1]
        for d in script:
            yield d


def _bash_use(call_id: str, command: str) -> ContentDelta:
    return ContentDelta(
        type="tool_use",
        text=ToolCall(id=call_id, name="Bash", arguments={"command": command}),
    )


def _read_use(call_id: str, file_path: str) -> ContentDelta:
    return ContentDelta(
        type="tool_use",
        text=ToolCall(id=call_id, name="Read", arguments={"file_path": file_path}),
    )


def _write_use(call_id: str, file_path: str) -> ContentDelta:
    return ContentDelta(
        type="tool_use",
        text=ToolCall(
            id=call_id, name="Write",
            arguments={"file_path": file_path, "content": "x"},
        ),
    )


def _text(s: str) -> ContentDelta:
    return ContentDelta(type="text", text=s)


def _usage() -> ContentDelta:
    from baozicode.agent.events import UsageStats
    return ContentDelta(type="usage", text=UsageStats(10, 5))


async def _drain(agent: Agent, text: str) -> list[AgentEvent]:
    out: list[AgentEvent] = []
    async for ev in agent.run(text):
        out.append(ev)
    return out


# ---- v0.2 auto_allow ----

async def test_v2_auto_allow_skips_modal(tmp_path: Path) -> None:
    """v0.2:Read 在 auto_allow → 直接执行,permission_callback 不被调。"""
    perms = Permissions(auto_allow=["Read"], deny=[])
    callback_called: list[ToolCall] = []

    async def callback(call: ToolCall) -> bool:
        callback_called.append(call)
        return True

    llm = _ScriptedLLM([
        [_read_use("c1", str(tmp_path / "f.txt")), _usage()],
        [_text("ok"), _usage()],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=perms,
        config=make_minimal_config(),
        permission_callback=callback,
        # merged_permissions 不传 → Agent 走 _v2_executor
    )

    events = await _drain(agent, "read file")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    assert len(tool_results) >= 1
    read_result = next(r for r in tool_results if r.tool_call_id == "c1")
    # Read 应被执行(可能因为文件不存在 is_error,但不应是 permission denied)
    assert "denied by permissions" not in read_result.content, (
        f"Read 不应被 v0.2 拒(在 auto_allow): {read_result.content}"
    )
    # callback 不应被调(auto_allow 直接放行)
    assert callback_called == []


# ---- v0.2 deny ----

async def test_v2_deny_short_circuits(tmp_path: Path) -> None:
    """v0.2:Bash 匹配 deny glob → is_error,permission_callback 不被调。"""
    perms = Permissions(auto_allow=[], deny=["Bash*"])
    callback_called: list[ToolCall] = []

    async def callback(call: ToolCall) -> bool:
        callback_called.append(call)
        return True

    llm = _ScriptedLLM([
        [_bash_use("c1", "echo hello"), _usage()],
        [_text("ok"), _usage()],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=perms,
        config=make_minimal_config(),
        permission_callback=callback,
    )

    events = await _drain(agent, "echo")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    assert len(tool_results) >= 1
    bash_result = next(r for r in tool_results if r.tool_call_id == "c1")
    assert bash_result.is_error
    assert "denied by permissions" in bash_result.content, (
        f"Bash 应被 v0.2 deny 拦: {bash_result.content}"
    )
    # callback 不应被调(deny 短路)
    assert callback_called == []


# ---- v0.2 未配置的工具 → 走 L5 Modal(默认) ----

async def test_v2_unmatched_falls_through_to_l5(tmp_path: Path) -> None:
    """v0.2:Write 不在 auto_allow 也不在 deny → 走 permission_callback。"""
    perms = Permissions(auto_allow=["Read"], deny=[])

    async def callback(call: ToolCall) -> bool:
        return False  # 用户拒

    llm = _ScriptedLLM([
        [_write_use("c1", str(tmp_path / "f.txt")), _usage()],
        [_text("ok"), _usage()],
    ])

    agent = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=perms,
        config=make_minimal_config(),
        permission_callback=callback,
    )

    events = await _drain(agent, "write file")
    tool_results = [e.payload for e in events if e.type == "tool_result"]

    assert len(tool_results) >= 1
    write_result = next(r for r in tool_results if r.tool_call_id == "c1")
    assert write_result.is_error
    # v0.2 user 拒的文案
    assert "denied by user" in write_result.content, (
        f"应被用户拒: {write_result.content}"
    )


# ---- Loader 端:同时声明 v0.2 + v0.5 → 记 deprecation warning ----

class TestDeprecationWarning:
    def test_v0_2_and_v0_5_coexist_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """v0.5 loader:同时存在 `permissions:` 和 `permissions_v5:` → 记 warning。"""
        from baozicode.config.loader import load_config

        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text(
            "backend: openai\n"
            "anthropic:\n  api_key: x\n  model: m\n"
            "openai:\n  api_key: x\n  model: m\n"
            "minimax:\n  api_key: x\n  model: m\n"
            "deepseek:\n  api_key: x\n  model: m\n"
            "permissions:\n"
            "  auto_allow: [Read]\n"
            "  deny: [Bash*]\n"
            "permissions_v5:\n"
            "  mode: default\n"
            "  rules: []\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="baozicode.config.loader"):
            config = load_config(str(config_yaml))

        # 两个块都被保留(loader 不强行丢弃)
        assert config.permissions is not None
        assert config.permissions.auto_allow == ["Read"]
        assert config.permissions_v5 is not None
        assert config.permissions_v5.mode == "default"

        # 至少一条 warning 提到"permissions"和"permissions_v5"
        deprecation_warnings = [
            r for r in caplog.records
            if "permissions" in r.getMessage().lower()
            and "v0.2" in r.getMessage().lower()
        ]
        assert len(deprecation_warnings) >= 1, (
            f"未发现 deprecation warning;caplog 记录: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_v0_2_only_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """只有 v0.2 `permissions:` → 不应触发 deprecation warning。"""
        from baozicode.config.loader import load_config

        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text(
            "backend: openai\n"
            "anthropic:\n  api_key: x\n  model: m\n"
            "openai:\n  api_key: x\n  model: m\n"
            "minimax:\n  api_key: x\n  model: m\n"
            "deepseek:\n  api_key: x\n  model: m\n"
            "permissions:\n"
            "  auto_allow: [Read]\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="baozicode.config.loader"):
            config = load_config(str(config_yaml))

        assert config.permissions is not None
        assert config.permissions_v5 is None
        # 不应有 v0.2/v0.5 相关的 warning
        deprecation_warnings = [
            r for r in caplog.records
            if "v0.2" in r.getMessage() and "permissions_v5" in r.getMessage()
        ]
        assert deprecation_warnings == [], (
            f"不应有 deprecation warning,但有: "
            f"{[r.getMessage() for r in deprecation_warnings]}"
        )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
