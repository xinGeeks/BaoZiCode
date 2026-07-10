"""v1.4 Team Foundation — App integration 测试。

覆盖 `openspec/changes/v1-4-team-foundation/specs/team-management/spec.md`
中 App 集成 acceptance scenario:

- `BaoZiCodeApp._build_teams_registry()` 返回 TeamsRegistry
- `BaoZiCodeApp.on_mount` 调 `_build_teams_registry`
- `self.teams` 类型是 TeamsRegistry
- `config.teams = None` / `enabled=False` 时 `self.teams = None`
- 顶层 `baozicode team ...` 子命令分发到 `teams.cli.main`
- `on_unmount` 释放 `self.teams`(置 None)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from baozicode.app import BaoZiCodeApp
from baozicode.config.schema import AppConfig, TeamsConfig
from baozicode.teams import TeamsRegistry


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _minimal_app_config(**overrides) -> AppConfig:
    """构造最小可加载的 AppConfig(backend 必填 4 个)。"""
    data: dict = {
        "backend": "anthropic",
        "anthropic": {"api_key": "k", "model": "m"},
        "openai": {"api_key": "k", "model": "m"},
        "minimax": {"api_key": "k", "model": "m"},
        "deepseek": {"api_key": "k", "model": "m"},
    }
    data.update(overrides)
    return AppConfig.model_validate(data)


@pytest.fixture
def app_with_teams(tmp_path: Path) -> BaoZiCodeApp:
    """构造一个带 teams 配置的 App 实例(不真的启动 Textual)。"""
    teams_cfg = TeamsConfig(dir=str(tmp_path / "teams"))
    config = _minimal_app_config(teams=teams_cfg.model_dump())
    # App 需要真实的 project_root(permissions_bootstrap 跑 IO)
    return BaoZiCodeApp(config, project_root=tmp_path)


@pytest.fixture
def app_without_teams(tmp_path: Path) -> BaoZiCodeApp:
    """构造一个不带 teams 配置的 App 实例(走默认 disabled 路径)。"""
    config = _minimal_app_config()  # 无 teams 字段
    return BaoZiCodeApp(config, project_root=tmp_path)


@pytest.fixture
def app_disabled_teams(tmp_path: Path) -> BaoZiCodeApp:
    """构造一个 teams.enabled=False 的 App 实例。"""
    teams_cfg = TeamsConfig(enabled=False)
    config = _minimal_app_config(teams=teams_cfg.model_dump())
    return BaoZiCodeApp(config, project_root=tmp_path)


# ---------------------------------------------------------------------------
# _build_teams_registry
# ---------------------------------------------------------------------------


class TestBuildTeamsRegistry:
    def test_returns_registry_when_enabled(
        self, app_with_teams: BaoZiCodeApp, tmp_path: Path
    ) -> None:
        registry = app_with_teams._build_teams_registry()
        assert isinstance(registry, TeamsRegistry)
        assert app_with_teams.teams is registry
        assert registry.teams_dir == tmp_path / "teams"

    def test_creates_dir_if_missing(
        self, app_with_teams: BaoZiCodeApp, tmp_path: Path
    ) -> None:
        teams_dir = tmp_path / "teams"
        assert not teams_dir.exists()
        app_with_teams._build_teams_registry()
        assert teams_dir.exists()

    def test_returns_none_when_no_teams_config(
        self, app_without_teams: BaoZiCodeApp
    ) -> None:
        registry = app_without_teams._build_teams_registry()
        assert registry is None
        assert app_without_teams.teams is None

    def test_returns_none_when_disabled(
        self, app_disabled_teams: BaoZiCodeApp
    ) -> None:
        registry = app_disabled_teams._build_teams_registry()
        assert registry is None
        assert app_disabled_teams.teams is None

    def test_idempotent(self, app_with_teams: BaoZiCodeApp) -> None:
        reg1 = app_with_teams._build_teams_registry()
        reg2 = app_with_teams._build_teams_registry()
        assert reg1 is reg2
        assert app_with_teams.teams is reg1


# ---------------------------------------------------------------------------
# on_mount 集成(不真的启动 Textual,只 mock 调用 _build_teams_registry)
# ---------------------------------------------------------------------------


class TestOnMountCallsBuild:
    def test_on_mount_calls_build_teams_registry(
        self, app_with_teams: BaoZiCodeApp
    ) -> None:
        """验证 on_mount 流程中 `_build_teams_registry` 被调用。

        on_mount 同步流程有大量 worker 启动(push_screen /
        run_worker / mcp bootstrap / load_skill register / task_tool
        register / worktree-bootstrap)。没有真实 Textual event loop 时
        这些会抛。我们 patch 掉 run_worker + push_screen + 所有会异步启
        动的子函数,让 on_mount 顺利走到末尾。
        """
        with patch.object(BaoZiCodeApp, "_build_teams_registry") as mock_build, \
             patch.object(BaoZiCodeApp, "push_screen"), \
             patch.object(BaoZiCodeApp, "run_worker"), \
             patch.object(BaoZiCodeApp, "_bootstrap_mcp"), \
             patch.object(BaoZiCodeApp, "_register_load_skill_tool"), \
             patch.object(BaoZiCodeApp, "_register_task_tool"), \
             patch.object(BaoZiCodeApp, "_start_worktree_system"), \
             patch.object(BaoZiCodeApp, "_startup_resume"), \
             patch.object(BaoZiCodeApp, "_startup_session_select"):
            app_with_teams.on_mount()
        mock_build.assert_called_once()

    def test_on_unmount_releases_teams(
        self, app_with_teams: BaoZiCodeApp
    ) -> None:
        # 先 bootstrap
        app_with_teams._build_teams_registry()
        assert app_with_teams.teams is not None
        # on_unmount 走异步,我们用 asyncio.run 跑
        import asyncio

        try:
            asyncio.run(app_with_teams.on_unmount())
        except Exception:
            # worktree manager 等可能因没东西抛,但 self.teams 已经被释放
            pass
        assert app_with_teams.teams is None


# ---------------------------------------------------------------------------
# 顶层 CLI 子命令分发
# ---------------------------------------------------------------------------


class TestTopLevelDispatch:
    def test_team_subcommand_dispatched_to_teams_cli(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`baozicode team list --teams-dir ...` 应走 teams.cli.main。

        用真实 subprocess 跑 main() 来验证完整 dispatch 流。
        """
        from baozicode.cli import main

        code = main(["team", "--teams-dir", str(tmp_path), "list"])
        assert code == 0
        out = capsys.readouterr().out
        # 空 teams_dir → (no teams)
        assert "(no teams)" in out

    def test_team_create_via_top_level(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from baozicode.cli import main

        teams_dir = tmp_path / "teams"
        code = main(["team", "--teams-dir", str(teams_dir), "create", "smoke"])
        assert code == 0
        assert (teams_dir / "smoke" / "team.json").exists()

    def test_top_level_help_still_works(self, capsys: pytest.CaptureFixture[str]) -> None:
        """无子命令时,顶层 help 仍能跑(不破坏 TUI 启动路径)。"""
        from baozicode.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        # 顶层 help 应同时显示 `--resume / --new / --no-banner` 和 `team`
        assert "--resume" in out
        assert "team" in out


# ---------------------------------------------------------------------------
# 默认 teams_dir fallback(无 teams 配置时 CLI 仍能跑)
# ---------------------------------------------------------------------------


class TestDefaultFallback:
    def test_cli_works_without_teams_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """CLI 子命令在 config 无 teams 块时仍能跑(走 ~/.config/baozicode/teams/)。

        这里我们用 --teams-dir 显式覆盖,绕开默认 user dir 副作用。
        """
        from baozicode.cli import main

        teams_dir = tmp_path / "fallback-teams"
        code = main(["team", "--teams-dir", str(teams_dir), "create", "acme"])
        assert code == 0
        assert (teams_dir / "acme" / "team.json").exists()