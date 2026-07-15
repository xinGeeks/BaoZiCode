"""v1-4-team-coordinator — coordinator_enabled 三锁门测试。

覆盖 `openspec/changes/v1-4-team-coordinator/specs/team-management/spec.md`
中 `coordinator_enabled triple-lock gate` requirement。
"""

from __future__ import annotations

from typing import Iterator

import pytest

from baozicode.config.schema import (
    AppConfig,
    BackendConfig,
    CoordinatorConfig,
    TeamsConfig,
)
from baozicode.teams import (
    Member,
    Team,
    check_coordinator_locks,
    coordinator_enabled,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_team(*, coordinator: bool = False) -> Team:
    return Team(
        name="devops",
        members={"alice": Member(name="alice", role="backend", backend="coroutine")},
        coordinator=coordinator,
    )


def _make_config(*, enabled: bool = True, env_var: str = "BAOZICODE_COORDINATOR") -> AppConfig:
    """最小可用 AppConfig(必须填 backend + anthropic + openai 等必填字段)。"""
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="claude-test"),
        openai=BackendConfig(api_key="x", model="gpt-test"),
        minimax=BackendConfig(api_key="x", model="m-test"),
        deepseek=BackendConfig(api_key="x", model="d-test"),
        teams=TeamsConfig(
            coordinator=CoordinatorConfig(enabled=enabled, env_var=env_var)
        ),
    )


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """确保 BAOZICODE_COORDINATOR 未设。"""
    monkeypatch.delenv("BAOZICODE_COORDINATOR", raising=False)
    monkeypatch.delenv("MY_COORD", raising=False)
    yield


# ---------------------------------------------------------------------------
# 三锁全命中
# ---------------------------------------------------------------------------


class TestAllLocksSet:
    """Requirement: All three locks set → True"""

    def test_all_locks_hit_returns_true(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config(enabled=True)
        team = _make_team(coordinator=True)
        assert coordinator_enabled(config, team) is True

    @pytest.mark.parametrize("env_value", ["1", "true", "TRUE", "Yes", "YES", "yes"])
    def test_truthy_env_values(
        self, env_value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", env_value)
        config = _make_config(enabled=True)
        team = _make_team(coordinator=True)
        assert coordinator_enabled(config, team) is True


# ---------------------------------------------------------------------------
# 各锁缺失
# ---------------------------------------------------------------------------


class TestConfigBlocks:
    """Requirement: Config disabled blocks"""

    def test_config_disabled_blocks(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config(enabled=False)
        team = _make_team(coordinator=True)
        assert coordinator_enabled(config, team) is False

    def test_config_block_missing_blocks(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config()
        config.teams.coordinator = None  # 整块省略
        team = _make_team(coordinator=True)
        assert coordinator_enabled(config, team) is False

    def test_teams_block_missing_blocks(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config()
        config.teams = None
        team = _make_team(coordinator=True)
        assert coordinator_enabled(config, team) is False

    def test_config_none_blocks(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        team = _make_team(coordinator=True)
        assert coordinator_enabled(None, team) is False  # type: ignore[arg-type]


class TestEnvVarBlocks:
    """Requirement: Env var missing/empty blocks"""

    def test_env_var_missing_blocks(
        self, clean_env: None
    ) -> None:
        config = _make_config(enabled=True)
        team = _make_team(coordinator=True)
        assert coordinator_enabled(config, team) is False

    @pytest.mark.parametrize("bad_value", ["", "0", "false", "FALSE", "no", "off"])
    def test_env_var_non_truthy_blocks(
        self, bad_value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", bad_value)
        config = _make_config(enabled=True)
        team = _make_team(coordinator=True)
        assert coordinator_enabled(config, team) is False

    def test_custom_env_var_name(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`env_var: MY_COORD` 时只查 MY_COORD,不查 BAOZICODE_COORDINATOR。"""
        monkeypatch.setenv("MY_COORD", "1")
        # BAOZICODE_COORDINATOR 未设(clean_env 已清)
        config = _make_config(enabled=True, env_var="MY_COORD")
        team = _make_team(coordinator=True)
        assert coordinator_enabled(config, team) is True


class TestTeamIntentBlocks:
    """Requirement: Team.coordinator False blocks"""

    def test_team_coordinator_false_blocks(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config(enabled=True)
        team = _make_team(coordinator=False)
        assert coordinator_enabled(config, team) is False


# ---------------------------------------------------------------------------
# check_coordinator_locks 报告
# ---------------------------------------------------------------------------


class TestCheckCoordinatorLocks:
    """Requirement: _check_coordinator_locks 返缺失锁列表。"""

    def test_all_locks_hit_returns_empty(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config(enabled=True)
        team = _make_team(coordinator=True)
        assert check_coordinator_locks(config, team) == []

    def test_config_disabled_reports(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config(enabled=False)
        team = _make_team(coordinator=True)
        assert "config.enabled" in check_coordinator_locks(config, team)

    def test_env_var_missing_reports(
        self, clean_env: None
    ) -> None:
        config = _make_config(enabled=True)
        team = _make_team(coordinator=True)
        assert "env_var" in check_coordinator_locks(config, team)

    def test_team_coordinator_false_reports(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config(enabled=True)
        team = _make_team(coordinator=False)
        assert "team.coordinator" in check_coordinator_locks(config, team)

    def test_all_three_missing_reports_all(
        self, clean_env: None
    ) -> None:
        config = _make_config(enabled=False)  # config.disabled
        team = _make_team(coordinator=False)  # team.coordinator=False
        missing = check_coordinator_locks(config, team)
        assert "config.enabled" in missing
        assert "env_var" in missing
        assert "team.coordinator" in missing

    def test_config_block_missing_reports(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config()
        config.teams.coordinator = None
        team = _make_team(coordinator=True)
        assert "config.enabled" in check_coordinator_locks(config, team)

    def test_teams_block_missing_reports(
        self, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config = _make_config()
        config.teams = None
        team = _make_team(coordinator=True)
        assert "config.teams" in check_coordinator_locks(config, team)