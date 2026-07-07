"""L4 PermissionMode + Agent session_mode 测试(v0.5)。

覆盖:
- apply() 三档行为
- allow/deny 透传(mode 不改变已确定决策)
- Agent.__init__ 接受 session_mode 参数
"""

from __future__ import annotations

import pytest

from baozicode.permissions.mode import apply
from baozicode.permissions.types import (
    PermissionDecision,
    PermissionMode,
)


# ---- apply() 基础 ----

class TestApplyStrict:
    def test_strict_fallthrough_denies(self) -> None:
        decision = apply(PermissionDecision.fallthrough(), "strict")
        assert decision.decision == "deny"
        assert decision.layer == "L4_mode"

    def test_strict_existing_allow_passes(self) -> None:
        existing = PermissionDecision(decision="allow", layer="L3_rule", reason="matched")
        decision = apply(existing, "strict")
        # 已有 allow,mode 不改变
        assert decision.decision == "allow"
        assert decision.layer == "L3_rule"

    def test_strict_existing_deny_passes(self) -> None:
        existing = PermissionDecision(decision="deny", layer="L1_blacklist", reason="hit")
        decision = apply(existing, "strict")
        assert decision.decision == "deny"
        assert decision.layer == "L1_blacklist"


class TestApplyDefault:
    def test_default_fallthrough_passes_through(self) -> None:
        decision = apply(PermissionDecision.fallthrough(), "default")
        assert decision.decision == "fallthrough"
        assert decision.layer == "none"

    def test_default_existing_allow_passes(self) -> None:
        existing = PermissionDecision(decision="allow", layer="L3_rule", reason="matched")
        decision = apply(existing, "default")
        assert decision.decision == "allow"
        assert decision.layer == "L3_rule"

    def test_default_existing_deny_passes(self) -> None:
        existing = PermissionDecision(decision="deny", layer="L2_sandbox", reason="escape")
        decision = apply(existing, "default")
        assert decision.decision == "deny"
        assert decision.layer == "L2_sandbox"


class TestApplyPermissive:
    def test_permissive_fallthrough_allows(self) -> None:
        decision = apply(PermissionDecision.fallthrough(), "permissive")
        assert decision.decision == "allow"
        assert decision.layer == "L4_mode"

    def test_permissive_existing_deny_passes(self) -> None:
        # L1 deny 不能被 L4 permissive 翻转(纵深防御)
        existing = PermissionDecision(decision="deny", layer="L1_blacklist", reason="rm -rf /")
        decision = apply(existing, "permissive")
        assert decision.decision == "deny"
        assert decision.layer == "L1_blacklist"

    def test_permissive_existing_allow_passes(self) -> None:
        existing = PermissionDecision(decision="allow", layer="L3_rule", reason="ok")
        decision = apply(existing, "permissive")
        assert decision.decision == "allow"
        assert decision.layer == "L3_rule"


# ---- 纵深防御保证:任何 mode 都不能让 L1 deny 变成 allow ----

class TestDeepDefenseInvariant:
    @pytest.mark.parametrize("mode", ["strict", "default", "permissive"])
    def test_l1_deny_unchanged_by_mode(self, mode: PermissionMode) -> None:
        """L1 硬拦截黑名单的 deny 不能被 mode 翻转。"""
        l1 = PermissionDecision(
            decision="deny", layer="L1_blacklist",
            reason="rm -rf /",
            matched_pattern="rm_rf_root",
        )
        decision = apply(l1, mode)
        assert decision.decision == "deny"
        assert decision.layer == "L1_blacklist"
        assert decision.matched_pattern == "rm_rf_root"

    @pytest.mark.parametrize("mode", ["strict", "default", "permissive"])
    def test_l2_deny_unchanged_by_mode(self, mode: PermissionMode) -> None:
        """L2 沙箱的 deny 不能被 mode 翻转。"""
        l2 = PermissionDecision(
            decision="deny", layer="L2_sandbox",
            reason="path escape",
            matched_pattern="/etc/passwd",
        )
        decision = apply(l2, mode)
        assert decision.decision == "deny"
        assert decision.layer == "L2_sandbox"


# ---- Agent session_mode 参数 ----

class TestAgentSessionModeParam:
    def test_agent_accepts_session_mode_none(self) -> None:
        """默认 None,backward compat。"""
        # 我们不构造真实的 LLMClient,只验证 __init__ 接受 session_mode
        from baozicode.agent.loop import Agent

        # 用最简 mock — 仅测 __init__ 的参数接收
        # 实际 LLM 调用不在这里跑
        from unittest.mock import MagicMock
        llm = MagicMock()
        agent = Agent(
            llm_client=llm,
            tools=[],
            conversation=MagicMock(),
            permissions=MagicMock(),
            config=MagicMock(),
            session_mode=None,
        )
        assert agent._session_mode is None

    def test_agent_strict_session_mode(self) -> None:
        from unittest.mock import MagicMock
        from baozicode.agent.loop import Agent

        llm = MagicMock()
        agent = Agent(
            llm_client=llm,
            tools=[],
            conversation=MagicMock(),
            permissions=MagicMock(),
            config=MagicMock(),
            session_mode="strict",
        )
        assert agent._session_mode == "strict"

    def test_agent_permissive_session_mode(self) -> None:
        from unittest.mock import MagicMock
        from baozicode.agent.loop import Agent

        llm = MagicMock()
        agent = Agent(
            llm_client=llm,
            tools=[],
            conversation=MagicMock(),
            permissions=MagicMock(),
            config=MagicMock(),
            session_mode="permissive",
        )
        assert agent._session_mode == "permissive"
