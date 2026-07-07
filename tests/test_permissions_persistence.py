"""L3 local YAML 持久化测试(v0.5)。

覆盖:
- `local_yaml_path` 路径正确
- `load_local_yaml`:不存在/损坏/顶层非 dict 都返回默认空 dict
- `append_rule_to_local_yaml`:写读 roundtrip,dedup 幂等
- 原子写:写一半失败(模拟)不会破坏目标文件
- `has_rule`:key 一致即可(decision/pattern 相同即视为已存在)
- `remove_local_yaml`:删除 + 不存在返回 False
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from baozicode.permissions.persistence import (
    LOCAL_YAML_NAME,
    append_rule_to_local_yaml,
    has_rule,
    load_local_yaml,
    local_yaml_path,
    remove_local_yaml,
)
from baozicode.permissions.types import PermissionRule


# ---- 路径 ----

class TestLocalYamlPath:
    def test_path_inside_dotbaozicode(self, tmp_path: Path) -> None:
        path = local_yaml_path(tmp_path)
        assert path == tmp_path / ".baozicode" / LOCAL_YAML_NAME
        assert path.name == "permissions.local.yaml"

    def test_path_resolves_under_project_root(self, tmp_path: Path) -> None:
        # 验证路径在 tmp_path 下,不被 symlink 等拐走
        path = local_yaml_path(tmp_path)
        assert path.parent.parent == tmp_path


# ---- load ----

class TestLoadLocalYaml:
    def test_missing_file_returns_default(self, tmp_path: Path) -> None:
        data = load_local_yaml(tmp_path)
        assert data == {"mode": "default", "rules": []}

    def test_existing_file_round_trips(self, tmp_path: Path) -> None:
        target = local_yaml_path(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "mode: strict\nrules:\n  - tool: Bash\n    pattern: 'rm *'\n    decision: deny\n",
            encoding="utf-8",
        )
        data = load_local_yaml(tmp_path)
        assert data["mode"] == "strict"
        assert len(data["rules"]) == 1
        assert data["rules"][0]["tool"] == "Bash"

    def test_corrupt_yaml_returns_default(self, tmp_path: Path) -> None:
        target = local_yaml_path(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("mode: default\nrules:\n  - oops: : :: bad", encoding="utf-8")
        data = load_local_yaml(tmp_path)
        # 解析失败 → 默认空
        assert data == {"mode": "default", "rules": []}

    def test_non_dict_top_level_returns_default(self, tmp_path: Path) -> None:
        target = local_yaml_path(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- just a list\n- not a dict\n", encoding="utf-8")
        data = load_local_yaml(tmp_path)
        assert data == {"mode": "default", "rules": []}

    def test_missing_rules_field_added(self, tmp_path: Path) -> None:
        target = local_yaml_path(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("mode: default\n", encoding="utf-8")
        data = load_local_yaml(tmp_path)
        # 缺 rules 字段要补成空 list
        assert data["rules"] == []
        assert data["mode"] == "default"


# ---- append + dedup ----

class TestAppendRuleToLocalYaml:
    def test_creates_file_if_missing(self, tmp_path: Path) -> None:
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        append_rule_to_local_yaml(rule, tmp_path)
        target = local_yaml_path(tmp_path)
        assert target.is_file()
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert data["rules"][0] == {
            "tool": "Bash", "pattern": "git *", "decision": "allow",
        }

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        target = local_yaml_path(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "mode: default\nrules:\n  - tool: Bash\n    pattern: 'git *'\n    decision: allow\n",
            encoding="utf-8",
        )
        append_rule_to_local_yaml(
            PermissionRule(tool="Bash", pattern="npm test *", decision="allow"),
            tmp_path,
        )
        data = load_local_yaml(tmp_path)
        assert len(data["rules"]) == 2
        patterns = [r["pattern"] for r in data["rules"]]
        assert "git *" in patterns
        assert "npm test *" in patterns

    def test_dedup_skips_exact_duplicate(self, tmp_path: Path) -> None:
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        append_rule_to_local_yaml(rule, tmp_path)
        # 再加同一条 → 应该 no-op(幂等)
        append_rule_to_local_yaml(rule, tmp_path)
        data = load_local_yaml(tmp_path)
        assert len(data["rules"]) == 1

    def test_dedup_different_decision_not_skipped(self, tmp_path: Path) -> None:
        # 同 (tool, pattern) 但 decision 不同 → 应当作两条不同 rule 写入
        allow = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        deny = PermissionRule(tool="Bash", pattern="git *", decision="deny")
        append_rule_to_local_yaml(allow, tmp_path)
        append_rule_to_local_yaml(deny, tmp_path)
        data = load_local_yaml(tmp_path)
        assert len(data["rules"]) == 2
        decisions = [r["decision"] for r in data["rules"]]
        assert "allow" in decisions
        assert "deny" in decisions

    def test_creates_dotbaozicode_dir_if_missing(self, tmp_path: Path) -> None:
        # tmp_path/.baozicode/ 不存在 → 应自动创建
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        append_rule_to_local_yaml(rule, tmp_path)
        assert (tmp_path / ".baozicode").is_dir()
        assert local_yaml_path(tmp_path).is_file()


# ---- 原子写:失败时目标文件不动 ----

class TestAtomicWrite:
    def test_atomic_write_does_not_leave_tmp(self, tmp_path: Path) -> None:
        """成功写后,不应残留 .tmp 文件。"""
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        append_rule_to_local_yaml(rule, tmp_path)
        parent = local_yaml_path(tmp_path).parent
        tmps = list(parent.glob("*.tmp"))
        assert tmps == [], f"残留 tmp 文件: {tmps}"

    def test_corrupt_existing_file_does_not_lose_data(self, tmp_path: Path) -> None:
        """已存在的 local YAML 内容是合法 YAML,append 不会损坏它。"""
        target = local_yaml_path(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        original = (
            "mode: strict\n"
            "rules:\n"
            "  - tool: Bash\n"
            "    pattern: 'rm *'\n"
            "    decision: deny\n"
        )
        target.write_text(original, encoding="utf-8")
        append_rule_to_local_yaml(
            PermissionRule(tool="Bash", pattern="git *", decision="allow"),
            tmp_path,
        )
        # 重新读,原有 rule 必须还在
        data = load_local_yaml(tmp_path)
        assert data["mode"] == "strict"
        assert any(
            r.get("pattern") == "rm *" and r.get("decision") == "deny"
            for r in data["rules"]
        )
        # 新加的也要在
        assert any(
            r.get("pattern") == "git *" and r.get("decision") == "allow"
            for r in data["rules"]
        )


# ---- has_rule ----

class TestHasRule:
    def test_detects_exact_match(self) -> None:
        data = {
            "mode": "default",
            "rules": [
                {"tool": "Bash", "pattern": "git *", "decision": "allow"},
            ],
        }
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        assert has_rule(data, rule) is True

    def test_misses_different_pattern(self) -> None:
        data = {
            "mode": "default",
            "rules": [
                {"tool": "Bash", "pattern": "git *", "decision": "allow"},
            ],
        }
        rule = PermissionRule(tool="Bash", pattern="npm *", decision="allow")
        assert has_rule(data, rule) is False

    def test_misses_different_decision(self) -> None:
        data = {
            "mode": "default",
            "rules": [
                {"tool": "Bash", "pattern": "git *", "decision": "allow"},
            ],
        }
        rule = PermissionRule(tool="Bash", pattern="git *", decision="deny")
        assert has_rule(data, rule) is False

    def test_skips_invalid_entries(self) -> None:
        # rules 里掺杂坏数据,has_rule 不能炸
        data = {
            "mode": "default",
            "rules": [
                "not a dict",
                {"tool": "Bash"},  # 缺 pattern / decision
                {"tool": "Bash", "pattern": "git *", "decision": "allow"},
            ],
        }
        rule = PermissionRule(tool="Bash", pattern="git *", decision="allow")
        assert has_rule(data, rule) is True


# ---- remove ----

class TestRemoveLocalYaml:
    def test_remove_existing_returns_true(self, tmp_path: Path) -> None:
        target = local_yaml_path(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("mode: default\nrules: []\n", encoding="utf-8")
        assert remove_local_yaml(tmp_path) is True
        assert not target.exists()

    def test_remove_missing_returns_false(self, tmp_path: Path) -> None:
        # 文件不存在 → no-op,返回 False
        assert remove_local_yaml(tmp_path) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
