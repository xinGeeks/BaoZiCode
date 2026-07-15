"""v1-4-team-coordinator — App.use_team coordinator kwarg 测试。

覆盖 `openspec/changes/v1-4-team-coordinator/specs/team-management/spec.md`
中 `App.use_team accepts coordinator kwarg` requirement。

测试用 shim 绕开 Textual `run_worker` 上下文(已知 limitation,
on_mount 需在 Textual app 里跑;pre-existing 5 failure 与本次无关)。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("BAOZICODE_COORDINATOR", raising=False)
    yield


def _make_app_with_teams(teams_dir: Path, *, coordinator_enabled: bool = True):
    """构造一个跳过 __init__ / on_mount / textual worker 的 App 实例。

    完整 BaoZiCodeApp 需要 Textual app 上下文(`run_worker`),但
    `use_team` 是同步方法,不依赖 event loop。用 `__new__` 跳过
    __init__ 的 textual 依赖,然后手动挂载必要字段。
    """
    from baozicode.app import BaoZiCodeApp
    from baozicode.config.schema import (
        AppConfig,
        BackendConfig,
        CoordinatorConfig,
        TeamsConfig,
    )
    from baozicode.teams import TeamsRegistry

    config = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="claude-test"),
        openai=BackendConfig(api_key="x", model="gpt-test"),
        minimax=BackendConfig(api_key="x", model="m-test"),
        deepseek=BackendConfig(api_key="x", model="d-test"),
        teams=TeamsConfig(
            coordinator=CoordinatorConfig(
                enabled=coordinator_enabled,
                env_var="BAOZICODE_COORDINATOR",
            )
        ),
    )
    app = BaoZiCodeApp.__new__(BaoZiCodeApp)
    app.config = config
    app.project_root = teams_dir.parent
    app.active_role = "subagent"
    app.active_coordinator = False
    app.active_team_name = None
    app.mailbox_notifier = None
    app.teams = TeamsRegistry(teams_dir)
    return app


def _create_team(
    teams_dir: Path,
    name: str,
    *,
    coordinator: bool = False,
) -> Path:
    from baozicode.teams import TeamsRegistry

    reg = TeamsRegistry(teams_dir)
    reg.create_team(name, coordinator=coordinator)
    return teams_dir / name


# ---------------------------------------------------------------------------
# use_team(coordinator=False) — 现有 Lead 路径
# ---------------------------------------------------------------------------


class TestUseTeamLeadPath:
    """Requirement: use_team 不传 coordinator → 现有 Lead 路径。"""

    def test_default_active_role_is_lead(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        _create_team(tmp_path, "devops")
        app = _make_app_with_teams(tmp_path)
        app.use_team("devops")
        assert app.active_role == "lead"
        assert app.active_coordinator is False

    def test_active_team_name_set(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        _create_team(tmp_path, "devops")
        app = _make_app_with_teams(tmp_path)
        app.use_team("devops")
        assert app.active_team_name == "devops"

    def test_mailbox_notifier_constructed(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        _create_team(tmp_path, "devops")
        app = _make_app_with_teams(tmp_path)
        app.use_team("devops")
        assert app.mailbox_notifier is not None


# ---------------------------------------------------------------------------
# use_team(coordinator=True) — 三锁命中走 coordinator
# ---------------------------------------------------------------------------


class TestUseTeamCoordinatorAllLocksHit:
    """Requirement: 三锁全命中 → active_role='coordinator'"""

    def test_all_locks_hit(
        self, tmp_path: Path, clean_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        _create_team(tmp_path, "devops", coordinator=True)
        app = _make_app_with_teams(tmp_path, coordinator_enabled=True)
        app.use_team("devops", coordinator=True)
        assert app.active_role == "coordinator"
        assert app.active_coordinator is True


# ---------------------------------------------------------------------------
# use_team(coordinator=True) — 三锁不全命中降级 Lead
# ---------------------------------------------------------------------------


class TestUseTeamCoordinatorLockMissing:
    """Requirement: 三锁不全命中 → 降级 Lead + stderr 报告"""

    def test_env_var_missing_demotes_to_lead(
        self, tmp_path: Path, clean_env: None, capsys: pytest.CaptureFixture
    ) -> None:
        # env var 未设(clean_env 已清)
        _create_team(tmp_path, "devops", coordinator=True)
        app = _make_app_with_teams(tmp_path, coordinator_enabled=True)
        app.use_team("devops", coordinator=True)
        assert app.active_role == "lead"
        assert app.active_coordinator is False
        captured = capsys.readouterr()
        assert "env_var" in captured.err

    def test_config_disabled_demotes_to_lead(
        self,
        tmp_path: Path,
        clean_env: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        _create_team(tmp_path, "devops", coordinator=True)
        app = _make_app_with_teams(tmp_path, coordinator_enabled=False)
        app.use_team("devops", coordinator=True)
        assert app.active_role == "lead"
        assert app.active_coordinator is False
        captured = capsys.readouterr()
        assert "config.enabled" in captured.err

    def test_team_coordinator_false_demotes_to_lead(
        self,
        tmp_path: Path,
        clean_env: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        _create_team(tmp_path, "devops", coordinator=False)
        app = _make_app_with_teams(tmp_path, coordinator_enabled=True)
        app.use_team("devops", coordinator=True)
        assert app.active_role == "lead"
        assert app.active_coordinator is False
        captured = capsys.readouterr()
        assert "team.coordinator" in captured.err


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


class TestUseTeamErrors:
    """Requirement: team 不存在 / teams disabled 抛 ValueError。"""

    def test_team_not_found(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        _create_team(tmp_path, "devops")
        app = _make_app_with_teams(tmp_path)
        with pytest.raises(ValueError, match="不存在"):
            app.use_team("ghost")

    def test_teams_disabled(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        from baozicode.app import BaoZiCodeApp
        from baozicode.config.schema import (
            AppConfig,
            BackendConfig,
        )

        config = AppConfig(
            backend="anthropic",
            anthropic=BackendConfig(api_key="x", model="claude-test"),
            openai=BackendConfig(api_key="x", model="gpt-test"),
            minimax=BackendConfig(api_key="x", model="m-test"),
            deepseek=BackendConfig(api_key="x", model="d-test"),
        )
        app = BaoZiCodeApp.__new__(BaoZiCodeApp)
        app.config = config
        app.teams = None  # 模拟 teams disabled
        app.active_role = "subagent"
        app.active_coordinator = False
        app.active_team_name = None
        app.mailbox_notifier = None
        with pytest.raises(ValueError, match="team system"):
            BaoZiCodeApp.use_team(app, "devops")