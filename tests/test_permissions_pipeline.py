"""5 层防御流水线测试(v0.5)。

测试 `permissions.check()` 端到端:
- L1 黑名单
- L2 沙箱
- L3 规则
- L4 mode
- L5 user fallthrough

以及 short-circuit 行为。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.permissions import check
from baozicode.permissions.types import MergedPermissions, PermissionRule
from baozicode.tools.base import ToolCall


def _bash(command: str) -> ToolCall:
    return ToolCall(id="t", name="Bash", arguments={"command": command})


def _read(path: str) -> ToolCall:
    return ToolCall(id="t", name="Read", arguments={"file_path": path})


@pytest.fixture
def sandbox_root(tmp_path: Path) -> MergedPermissions:
    merged = MergedPermissions()
    merged.real_root = tmp_path.resolve()
    return merged


# ---- L1 短路 ----

class TestL1ShortCircuit:
    def test_l1_blacklist_denies_immediately(self, sandbox_root: MergedPermissions) -> None:
        decision = check(_bash("rm -rf /"), sandbox_root)
        assert decision.decision == "deny"
        assert decision.layer == "L1_blacklist"

    def test_l1_denies_even_with_permissive_mode(
        self, sandbox_root: MergedPermissions
    ) -> None:
        sandbox_root.mode = "permissive"
        decision = check(_bash("rm -rf /"), sandbox_root)
        # 纵深防御:L1 deny 不能被 L4 翻转
        assert decision.decision == "deny"


# ---- L2 沙箱 ----

class TestL2Sandbox:
    def test_l2_denies_path_outside_sandbox(
        self, sandbox_root: MergedPermissions
    ) -> None:
        # /usr/local/secret.txt 不在 L1 文本层列表里(只有 /etc/passwd 等),
        # 应该被 L2 沙箱拦
        decision = check(_read("/usr/local/secret.txt"), sandbox_root)
        assert decision.decision == "deny"
        assert decision.layer == "L2_sandbox"

    def test_l2_can_be_disabled(self, sandbox_root: MergedPermissions) -> None:
        sandbox_root.path_sandbox_enabled = False
        # 关 L2 后,/usr/local/secret.txt 应该继续往下走(L3 无规则 → L4 兜底)
        decision = check(_read("/usr/local/secret.txt"), sandbox_root)
        # 默认 mode=default → fallthrough
        assert decision.decision == "fallthrough"


# ---- L3 规则 ----

class TestL3Rules:
    def test_l3_deny_short_circuits(self, sandbox_root: MergedPermissions) -> None:
        sandbox_root.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="deny", source="user_global",
        ))
        decision = check(_bash("git status"), sandbox_root)
        assert decision.decision == "deny"
        assert decision.layer == "L3_rule"

    def test_l3_allow_returns_allow(self, sandbox_root: MergedPermissions) -> None:
        sandbox_root.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="allow", source="user_global",
        ))
        decision = check(_bash("git status"), sandbox_root)
        assert decision.decision == "allow"
        assert decision.layer == "L3_rule"


# ---- L4 mode ----

class TestL4Mode:
    def test_strict_mode_denies_fallthrough(
        self, sandbox_root: MergedPermissions
    ) -> None:
        sandbox_root.mode = "strict"
        # safe call → L1 fallthrough,L2 fallthrough,L3 无规则 → L4 strict → deny
        decision = check(_bash("git status"), sandbox_root)
        assert decision.decision == "deny"
        assert decision.layer == "L4_mode"

    def test_permissive_mode_allows_fallthrough(
        self, sandbox_root: MergedPermissions
    ) -> None:
        sandbox_root.mode = "permissive"
        decision = check(_bash("git status"), sandbox_root)
        assert decision.decision == "allow"
        assert decision.layer == "L4_mode"

    def test_default_mode_falls_through(
        self, sandbox_root: MergedPermissions
    ) -> None:
        sandbox_root.mode = "default"
        decision = check(_bash("git status"), sandbox_root)
        # default → fallthrough(给 L5 user 留机会)
        assert decision.decision == "fallthrough"


# ---- 短路优先级 ----

class TestShortCircuitOrder:
    def test_l1_deny_wins_over_l3_allow(self, sandbox_root: MergedPermissions) -> None:
        # L3 允许 rm -rf /,L1 仍然 deny
        sandbox_root.rules.append(PermissionRule(
            tool="Bash", pattern="*", decision="allow", source="user_global",
        ))
        decision = check(_bash("rm -rf /"), sandbox_root)
        assert decision.decision == "deny"
        assert decision.layer == "L1_blacklist"

    def test_l2_deny_wins_over_l3_allow(self, sandbox_root: MergedPermissions) -> None:
        sandbox_root.rules.append(PermissionRule(
            tool="Read", pattern="*", decision="allow", source="user_global",
        ))
        # 用 /usr/local/secret.txt(L1 不拦,L2 拦)
        decision = check(_read("/usr/local/secret.txt"), sandbox_root)
        assert decision.decision == "deny"
        assert decision.layer == "L2_sandbox"

    def test_l3_deny_wins_over_l4_strict(self, sandbox_root: MergedPermissions) -> None:
        # L3 deny 应在 L4 之前命中(实际上 L3 先于 L4 跑)
        sandbox_root.mode = "strict"
        sandbox_root.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="allow", source="user_global",
        ))
        decision = check(_bash("git status"), sandbox_root)
        # L3 allow → 直接 return,根本走不到 L4
        assert decision.decision == "allow"
        assert decision.layer == "L3_rule"


# ---- None ctx ----

class TestNoneCtx:
    def test_none_ctx_only_runs_l1(self) -> None:
        decision = check(_bash("git status"), None)
        # L1 不命中 → fallthrough(L2-L4 跳过)
        assert decision.decision == "fallthrough"

    def test_none_ctx_l1_still_works(self) -> None:
        decision = check(_bash("rm -rf /"), None)
        assert decision.decision == "deny"
        assert decision.layer == "L1_blacklist"
