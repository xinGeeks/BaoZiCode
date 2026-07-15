"""v1-4-team-coordinator — Team.coordinator schema 字段测试。

覆盖 `openspec/changes/v1-4-team-coordinator/specs/team-management/spec.md`
中 schema 相关 acceptance scenario:

- `Team.coordinator` 默认 False(向后兼容)
- `Team.from_dict` 缺字段默认 False
- `Team.to_dict` 输出 `"coordinator": true/false`
- `Team.__post_init__` 校验非 bool 抛 ValueError
- `TeamsRegistry.create_team(..., coordinator=...)` 接 kwarg
- `CoordinatorConfig` 配置层
- `TeamsConfig.coordinator: CoordinatorConfig | None`
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from baozicode.config.schema import AppConfig, CoordinatorConfig, TeamsConfig
from baozicode.teams import Member, Team, TeamsRegistry


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_member(name: str = "alice") -> Member:
    return Member(name=name, role="backend", backend="coroutine")


# ---------------------------------------------------------------------------
# Team.coordinator field
# ---------------------------------------------------------------------------


class TestTeamCoordinatorField:
    """Requirement: Team.coordinator intent field"""

    def test_default_is_false(self) -> None:
        team = Team(name="devops", members={"alice": _make_member()})
        assert team.coordinator is False

    def test_explicit_true(self) -> None:
        team = Team(
            name="devops", members={"alice": _make_member()}, coordinator=True
        )
        assert team.coordinator is True

    def test_explicit_false(self) -> None:
        team = Team(
            name="devops", members={"alice": _make_member()}, coordinator=False
        )
        assert team.coordinator is False

    @pytest.mark.parametrize("bad_value", ["yes", 1, 0, None, "true", 1.0])
    def test_non_bool_raises(self, bad_value: object) -> None:
        with pytest.raises(ValueError, match="coordinator"):
            Team(  # type: ignore[arg-type]
                name="devops",
                members={"alice": _make_member()},
                coordinator=bad_value,  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Team.from_dict / to_dict round-trip
# ---------------------------------------------------------------------------


class TestTeamCoordinatorRoundTrip:
    """Requirement: JSON 序列化含 coordinator / 缺字段默认 False。"""

    def test_to_dict_includes_coordinator_true(self) -> None:
        team = Team(
            name="devops", members={"alice": _make_member()}, coordinator=True
        )
        data = team.to_dict()
        assert data["coordinator"] is True

    def test_to_dict_includes_coordinator_false(self) -> None:
        team = Team(name="devops", members={"alice": _make_member()})
        data = team.to_dict()
        assert data["coordinator"] is False

    def test_from_dict_missing_field_defaults_false(self) -> None:
        """向后兼容 — 旧 team.json 无 coordinator 字段 → False。"""
        data = {
            "schema_version": "1.0",
            "name": "devops",
            "lead": "lead",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "members": {"alice": _make_member().to_dict()},
            "metadata": {},
        }
        team = Team.from_dict(data)
        assert team.coordinator is False

    def test_from_dict_explicit_true(self) -> None:
        data = {
            "schema_version": "1.0",
            "name": "devops",
            "lead": "lead",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "members": {},
            "metadata": {},
            "coordinator": True,
        }
        team = Team.from_dict(data)
        assert team.coordinator is True

    def test_full_round_trip(self) -> None:
        team = Team(
            name="devops",
            members={"alice": _make_member()},
            coordinator=True,
            metadata={"project": "acme"},
        )
        data = team.to_dict()
        restored = Team.from_dict(data)
        assert restored.coordinator is True
        assert restored.metadata == {"project": "acme"}


# ---------------------------------------------------------------------------
# TeamsRegistry.create_team coordinator kwarg
# ---------------------------------------------------------------------------


class TestCreateTeamWithCoordinator:
    """Requirement: TeamsRegistry.create_team(..., coordinator=False) 接新 kwarg。"""

    def test_create_team_default_false(self, tmp_path: Path) -> None:
        reg = TeamsRegistry(tmp_path)
        store = reg.create_team("devops")
        assert store.show().coordinator is False

    def test_create_team_true(self, tmp_path: Path) -> None:
        reg = TeamsRegistry(tmp_path)
        store = reg.create_team("devops", coordinator=True)
        assert store.show().coordinator is True

    def test_create_team_persists_to_team_json(self, tmp_path: Path) -> None:
        """持久化验证 — 重启读回仍保留 coordinator=True。"""
        reg = TeamsRegistry(tmp_path)
        reg.create_team("devops", coordinator=True)
        team_json = tmp_path / "devops" / "team.json"
        assert team_json.exists()
        data = json.loads(team_json.read_text(encoding="utf-8"))
        assert data["coordinator"] is True

    def test_old_team_json_without_field_loads_false(self, tmp_path: Path) -> None:
        """向后兼容 — 手动写一份无 coordinator 字段的 team.json,load 默认 False。"""
        team_dir = tmp_path / "devops"
        team_dir.mkdir(parents=True)
        (team_dir / "team.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "name": "devops",
                    "lead": "lead",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "members": {},
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        reg = TeamsRegistry(tmp_path)
        store = reg.get("devops")
        assert store is not None
        assert store.show().coordinator is False


# ---------------------------------------------------------------------------
# CoordinatorConfig
# ---------------------------------------------------------------------------


class TestCoordinatorConfig:
    """Requirement: CoordinatorConfig 子配置块。"""

    def test_default_disabled(self) -> None:
        cfg = CoordinatorConfig()
        assert cfg.enabled is False
        assert cfg.env_var == "BAOZICODE_COORDINATOR"
        assert "Read" in cfg.allowed_tools
        assert "team_dispatch" in cfg.allowed_tools

    def test_custom_env_var(self) -> None:
        cfg = CoordinatorConfig(env_var="MY_COORD")
        assert cfg.env_var == "MY_COORD"

    def test_custom_allowed_tools(self) -> None:
        cfg = CoordinatorConfig(allowed_tools=["Read", "Grep"])
        assert cfg.allowed_tools == ["Read", "Grep"]

    def test_enabled_true(self) -> None:
        cfg = CoordinatorConfig(enabled=True)
        assert cfg.enabled is True


class TestTeamsConfigCoordinator:
    """Requirement: TeamsConfig.coordinator 嵌套 None 默认。"""

    def test_default_none(self) -> None:
        cfg = TeamsConfig()
        assert cfg.coordinator is None

    def test_set_coordinator(self) -> None:
        cfg = TeamsConfig(coordinator=CoordinatorConfig(enabled=True))
        assert cfg.coordinator is not None
        assert cfg.coordinator.enabled is True

    def test_yaml_loads_nested_coordinator(self) -> None:
        """YAML `teams.coordinator.enabled: true` 嵌套块解析。"""
        yaml_text = """
teams:
  enabled: true
  coordinator:
    enabled: true
    env_var: MY_COORD
    allowed_tools:
      - Read
      - Grep
"""
        import yaml

        data = yaml.safe_load(yaml_text)
        teams_cfg = TeamsConfig.model_validate(data["teams"])
        assert teams_cfg.coordinator is not None
        assert teams_cfg.coordinator.enabled is True
        assert teams_cfg.coordinator.env_var == "MY_COORD"
        assert teams_cfg.coordinator.allowed_tools == ["Read", "Grep"]