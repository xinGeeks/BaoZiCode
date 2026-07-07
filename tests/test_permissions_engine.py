"""L3 RuleEngine + loader + persistence 测试(v0.5)。

覆盖:
- 单层规则命中
- 同 (tool, pattern) 多层重复 → 高优先级胜出
- deny 优先级 > allow(同优先级下 deny 短路)
- session > local > project > user_global 优先级
- mode 解析优先级
- 缺失 / 损坏的 YAML 静默跳过
- persistence 原子写 + dedup
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.permissions.engine import RuleEngine
from baozicode.permissions.loader import load_permissions_layers
from baozicode.permissions.persistence import (
    append_rule_to_local_yaml,
    has_rule,
    load_local_yaml,
    local_yaml_path,
    remove_local_yaml,
)
from baozicode.permissions.types import (
    MergedPermissions,
    PermissionRule,
)
from baozicode.tools.base import ToolCall


def _bash_call(command: str) -> ToolCall:
    return ToolCall(id="t", name="Bash", arguments={"command": command})


def _read_call(path: str) -> ToolCall:
    return ToolCall(id="t", name="Read", arguments={"file_path": path})


def _write_call(path: str) -> ToolCall:
    return ToolCall(id="t", name="Write", arguments={"file_path": path, "content": "x"})


# ---- RuleEngine:基础匹配 ----

class TestRuleEngineBasic:
    def test_no_rules_fallthrough(self) -> None:
        engine = RuleEngine()
        decision = engine.check(_bash_call("git status"))
        assert decision.decision == "fallthrough"

    def test_tool_mismatch_fallthrough(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Read", pattern="*.py", decision="allow", source="user_global",
        ))
        decision = engine.check(_bash_call("git status"))
        assert decision.decision == "fallthrough"

    def test_simple_allow(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="allow", source="user_global",
        ))
        decision = engine.check(_bash_call("git status"))
        assert decision.decision == "allow"
        assert decision.layer == "L3_rule"

    def test_simple_deny(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="rm *", decision="deny", source="user_global",
        ))
        decision = engine.check(_bash_call("rm -rf /var/log"))
        assert decision.decision == "deny"
        assert decision.layer == "L3_rule"

    def test_pattern_no_match_fallthrough(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="allow", source="user_global",
        ))
        decision = engine.check(_bash_call("npm install"))
        assert decision.decision == "fallthrough"

    def test_glob_special_chars(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="pytest tests/test_*.py", decision="allow",
            source="user_global",
        ))
        decision = engine.check(_bash_call("pytest tests/test_foo.py"))
        assert decision.decision == "allow"

    def test_question_mark_glob(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="c?t", decision="allow", source="user_global",
        ))
        decision = engine.check(_bash_call("cat"))
        assert decision.decision == "allow"

    def test_pattern_matches_multiple_args(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Write", pattern="*.py", decision="allow", source="user_global",
        ))
        # file_path = "*.py" → match;content 不是 str
        decision = engine.check(_write_call("foo.py"))
        assert decision.decision == "allow"

    def test_pattern_does_not_match_content(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Write", pattern="dangerous_content", decision="deny",
            source="user_global",
        ))
        # content="x" 不命中 pattern → fallthrough
        decision = engine.check(_write_call("foo.py"))
        assert decision.decision == "fallthrough"


# ---- RuleEngine:deny 短路 vs allow 候选 ----

class TestRuleEngineDenyVeto:
    def test_deny_short_circuits_even_if_allow_below(self) -> None:
        """user_global 有 allow,project 有 deny → deny 胜。"""
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="allow", source="user_global",
        ))
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git status", decision="deny", source="project",
        ))
        decision = engine.check(_bash_call("git status"))
        assert decision.decision == "deny"

    def test_deny_vetoes_session_allow(self) -> None:
        """D5 设计:任何 deny 都优先于 allow,无论层。"""
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git status", decision="deny", source="project",
        ))
        engine.add_session_rule(PermissionRule(
            tool="Bash", pattern="git *", decision="allow",
        ))
        decision = engine.check(_bash_call("git status"))
        # deny-veto:project 的 deny 拦截,即使 session 想 allow
        assert decision.decision == "deny"

    def test_deny_vetoes_local_allow(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="rm *", decision="deny", source="user_global",
        ))
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="rm *", decision="allow", source="local",
        ))
        decision = engine.check(_bash_call("rm build/"))
        # deny-veto:user_global 的 deny 拦截
        assert decision.decision == "deny"

    def test_same_layer_deny_overrides_allow(self) -> None:
        """同层内,deny 声明顺序在前即短路(忽略后续 allow)。"""
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="allow", source="user_global",
        ))
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git status", decision="deny", source="user_global",
        ))
        decision = engine.check(_bash_call("git status"))
        assert decision.decision == "deny"


# ---- RuleEngine:session rules 优先(无 deny 冲突时) ----

class TestRuleEngineSessionPriority:
    def test_session_rule_beats_merged_when_no_deny(self) -> None:
        engine = RuleEngine()
        # user_global 有 allow,无 deny;session 也有 allow
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="allow", source="user_global",
        ))
        engine.add_session_rule(PermissionRule(
            tool="Bash", pattern="git *", decision="allow",
        ))
        decision = engine.check(_bash_call("git status"))
        # 无 deny 冲突,session allow 触发
        assert decision.decision == "allow"

    def test_deny_in_user_global_vetoes_session_allow(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="deny", source="user_global",
        ))
        engine.add_session_rule(PermissionRule(
            tool="Bash", pattern="git *", decision="allow",
        ))
        decision = engine.check(_bash_call("git status"))
        # deny-veto → DENY(session allow 救不了)
        assert decision.decision == "deny"

    def test_session_rule_count(self) -> None:
        engine = RuleEngine()
        assert engine.session_rule_count() == 0
        engine.add_session_rule(PermissionRule(tool="Bash", pattern="git *", decision="allow"))
        engine.add_session_rule(PermissionRule(tool="Bash", pattern="npm *", decision="allow"))
        assert engine.session_rule_count() == 2


# ---- RuleEngine:debug ----

class TestRuleEngineDebug:
    def test_list_all_sorted(self) -> None:
        engine = RuleEngine()
        engine.merged.rules.append(PermissionRule(
            tool="Bash", pattern="git *", decision="allow", source="user_global",
        ))
        engine.add_session_rule(PermissionRule(
            tool="Bash", pattern="npm *", decision="allow",
        ))
        rules = engine.list_all()
        assert len(rules) == 2
        # session 在前
        assert rules[0].pattern == "npm *"
        assert rules[1].pattern == "git *"


# ---- Loader:三层合并 ----

class TestLoader:
    def test_no_files_returns_defaults(self, tmp_path: Path) -> None:
        merged = load_permissions_layers(tmp_path)
        assert merged.rules == []
        assert merged.mode == "default"
        assert merged.sources_loaded == []

    def test_loads_user_global_only(self, tmp_path: Path, monkeypatch) -> None:
        # 把 home 指向 tmp_path/.config/baozicode
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        perms_yaml = fake_home / ".config" / "baozicode" / "permissions.yaml"
        perms_yaml.parent.mkdir(parents=True)
        perms_yaml.write_text(
            "mode: strict\n"
            "rules:\n"
            "  - tool: Bash\n"
            "    pattern: 'git *'\n"
            "    decision: allow\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        merged = load_permissions_layers(tmp_path)
        assert merged.mode == "strict"
        assert len(merged.rules) == 1
        assert merged.rules[0].source == "user_global"

    def test_loads_project_yaml(self, tmp_path: Path, monkeypatch) -> None:
        # 隔离 user_global
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        project_yaml = tmp_path / ".baozicode" / "permissions.yaml"
        project_yaml.parent.mkdir(parents=True)
        project_yaml.write_text(
            "rules:\n"
            "  - tool: Read\n"
            "    pattern: '*.py'\n"
            "    decision: allow\n",
            encoding="utf-8",
        )
        merged = load_permissions_layers(tmp_path)
        assert len(merged.rules) == 1
        assert merged.rules[0].source == "project"
        assert str(project_yaml) in merged.sources_loaded[0]

    def test_loads_local_yaml(self, tmp_path: Path, monkeypatch) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        local_yaml = tmp_path / ".baozicode" / "permissions.local.yaml"
        local_yaml.parent.mkdir(parents=True)
        local_yaml.write_text(
            "rules:\n"
            "  - tool: Write\n"
            "    pattern: 'foo.py'\n"
            "    decision: allow\n",
            encoding="utf-8",
        )
        merged = load_permissions_layers(tmp_path)
        assert len(merged.rules) == 1
        assert merged.rules[0].source == "local"

    def test_three_layers_merged_with_correct_sources(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        # 三层各放一条
        (fake_home / ".config" / "baozicode").mkdir(parents=True)
        (fake_home / ".config" / "baozicode" / "permissions.yaml").write_text(
            "rules:\n  - tool: Bash\n    pattern: 'git *'\n    decision: allow\n",
            encoding="utf-8",
        )
        (tmp_path / ".baozicode").mkdir()
        (tmp_path / ".baozicode" / "permissions.yaml").write_text(
            "rules:\n  - tool: Bash\n    pattern: 'npm *'\n    decision: allow\n",
            encoding="utf-8",
        )
        (tmp_path / ".baozicode" / "permissions.local.yaml").write_text(
            "rules:\n  - tool: Bash\n    pattern: 'pytest *'\n    decision: allow\n",
            encoding="utf-8",
        )
        merged = load_permissions_layers(tmp_path)
        assert len(merged.rules) == 3
        sources = {r.pattern: r.source for r in merged.rules}
        assert sources["git *"] == "user_global"
        assert sources["npm *"] == "project"
        assert sources["pytest *"] == "local"

    def test_local_mode_beats_user_global_mode(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (fake_home / ".config" / "baozicode").mkdir(parents=True)
        (fake_home / ".config" / "baozicode" / "permissions.yaml").write_text(
            "mode: strict\nrules: []\n", encoding="utf-8",
        )
        (tmp_path / ".baozicode").mkdir()
        (tmp_path / ".baozicode" / "permissions.local.yaml").write_text(
            "mode: permissive\nrules: []\n", encoding="utf-8",
        )
        merged = load_permissions_layers(tmp_path)
        assert merged.mode == "permissive"  # local 胜

    def test_malformed_yaml_skipped(self, tmp_path: Path, monkeypatch) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (tmp_path / ".baozicode").mkdir()
        (tmp_path / ".baozicode" / "permissions.yaml").write_text(
            "this is: not: valid: yaml: : :",  # 严重损坏
            encoding="utf-8",
        )
        merged = load_permissions_layers(tmp_path)
        assert merged.rules == []
        assert merged.sources_loaded == []

    def test_rule_missing_fields_skipped(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        (tmp_path / ".baozicode").mkdir()
        (tmp_path / ".baozicode" / "permissions.yaml").write_text(
            "rules:\n"
            "  - tool: Bash\n"
            "    pattern: 'git *'\n"
            "    decision: allow\n"
            "  - pattern: 'npm *'\n"      # 缺 tool
            "    decision: allow\n"
            "  - tool: Bash\n"             # 缺 decision
            "    pattern: 'pytest *'\n",
            encoding="utf-8",
        )
        merged = load_permissions_layers(tmp_path)
        # 只第 1 条合法
        assert len(merged.rules) == 1
        assert merged.rules[0].pattern == "git *"

    def test_real_root_resolved(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        merged = load_permissions_layers(tmp_path)
        assert merged.real_root == tmp_path.resolve()


# ---- Persistence ----

class TestPersistence:
    def test_load_missing_returns_default(self, tmp_path: Path) -> None:
        data = load_local_yaml(tmp_path)
        assert data == {"mode": "default", "rules": []}

    def test_append_creates_file(self, tmp_path: Path) -> None:
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        append_rule_to_local_yaml(rule, tmp_path)
        path = local_yaml_path(tmp_path)
        assert path.is_file()
        data = load_local_yaml(tmp_path)
        assert len(data["rules"]) == 1
        assert data["rules"][0]["tool"] == "Bash"

    def test_append_dedup(self, tmp_path: Path) -> None:
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        append_rule_to_local_yaml(rule, tmp_path)
        append_rule_to_local_yaml(rule, tmp_path)
        data = load_local_yaml(tmp_path)
        assert len(data["rules"]) == 1

    def test_append_distinct_dedup_by_tuple(self, tmp_path: Path) -> None:
        # 不同 decision 不 dedup
        rule1 = PermissionRule(tool="Bash", pattern="rm *", decision="deny")
        rule2 = PermissionRule(tool="Bash", pattern="rm *", decision="allow")
        append_rule_to_local_yaml(rule1, tmp_path)
        append_rule_to_local_yaml(rule2, tmp_path)
        data = load_local_yaml(tmp_path)
        assert len(data["rules"]) == 2

    def test_atomic_write_does_not_corrupt_on_existing(
        self, tmp_path: Path
    ) -> None:
        # 先写一条
        rule1 = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        append_rule_to_local_yaml(rule1, tmp_path)
        # 再追加一条
        rule2 = PermissionRule(tool="Bash", pattern="npm *", decision="allow")
        append_rule_to_local_yaml(rule2, tmp_path)
        data = load_local_yaml(tmp_path)
        assert len(data["rules"]) == 2

    def test_remove_local_yaml(self, tmp_path: Path) -> None:
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        append_rule_to_local_yaml(rule, tmp_path)
        assert local_yaml_path(tmp_path).is_file()
        assert remove_local_yaml(tmp_path) is True
        assert not local_yaml_path(tmp_path).is_file()
        # 第二次删 → False
        assert remove_local_yaml(tmp_path) is False

    def test_load_corrupted_yaml_returns_default(self, tmp_path: Path) -> None:
        path = local_yaml_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage: : : ::", encoding="utf-8")
        data = load_local_yaml(tmp_path)
        assert data == {"mode": "default", "rules": []}

    def test_has_rule_helper(self, tmp_path: Path) -> None:
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        append_rule_to_local_yaml(rule, tmp_path)
        data = load_local_yaml(tmp_path)
        assert has_rule(data, rule) is True
        other = PermissionRule(tool="Bash", pattern="rm *", decision="deny")
        assert has_rule(data, other) is False
