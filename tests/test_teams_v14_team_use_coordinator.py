"""v1-4-team-coordinator — CLI `team use --coordinator` 扩展测试。

覆盖 `openspec/changes/v1-4-team-coordinator/specs/team-management/spec.md`
中 `baozicode team use --coordinator flag` requirement。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator

import pytest

from baozicode.teams import TeamsRegistry
from baozicode.teams import cli as cli_mod
from baozicode.teams.cli import EXIT_NOT_FOUND, EXIT_OK


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("BAOZICODE_COORDINATOR", raising=False)
    yield


def _bootstrap_teams_dir(tmp_path: Path, *, team_coordinator: bool = False) -> Path:
    """建一个 teams 目录 + devops team(可指定 coordinator 字段)。"""
    teams_dir = tmp_path / "teams"
    reg = TeamsRegistry(teams_dir)
    reg.create_team("devops", coordinator=team_coordinator)
    return teams_dir


def _write_config(
    tmp_path: Path,
    *,
    coordinator_enabled: bool = True,
    teams_dir: Path | None = None,
) -> Path:
    """写一份 YAML 配置文件。"""
    import yaml

    config_path = tmp_path / "config.yaml"
    td = teams_dir or (tmp_path / "teams")
    data = {
        "backend": "anthropic",
        "anthropic": {"api_key": "x", "model": "claude-test"},
        "openai": {"api_key": "x", "model": "gpt-test"},
        "minimax": {"api_key": "x", "model": "m-test"},
        "deepseek": {"api_key": "x", "model": "d-test"},
        "teams": {
            "enabled": True,
            "dir": str(td),
            "coordinator": {
                "enabled": coordinator_enabled,
                "env_var": "BAOZICODE_COORDINATOR",
            },
        },
    }
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return config_path


def _make_namespace(
    *,
    name: str = "devops",
    coordinator: bool = False,
    config_path: Path | None = None,
    teams_dir: Path | None = None,
    scope: str = "user",
) -> argparse.Namespace:
    return argparse.Namespace(
        name=name,
        coordinator=coordinator,
        config=str(config_path) if config_path else None,
        teams_dir=str(teams_dir) if teams_dir else None,
        scope=scope,
    )


def _run_cli_via_main(argv: list[str]) -> tuple[int, str, str]:
    """跑 cli_mod.main(argv) 并捕获 stdout/stderr + exit code。"""
    out, err = io.StringIO(), io.StringIO()
    real_stdin = sys.stdin
    sys.stdin = io.StringIO()  # 喂空
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = cli_mod.main(argv)
    finally:
        sys.stdin = real_stdin
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# argparse 解析 — Namespace 直接测试
# ---------------------------------------------------------------------------


class TestArgparseParsing:
    """Requirement: --coordinator flag 解析。"""

    def test_coordinator_flag_true(self, tmp_path: Path, clean_env: None) -> None:
        config_path = _write_config(tmp_path, coordinator_enabled=False)
        teams_dir = _bootstrap_teams_dir(tmp_path, team_coordinator=False)
        ns = _make_namespace(
            name="ghost",
            coordinator=True,
            config_path=config_path,
            teams_dir=teams_dir,
        )
        code = cli_mod._cmd_use(ns)
        # team 不存在,但 coordinator=True → 仍走 coordinator 检查路径
        assert code == EXIT_NOT_FOUND

    def test_default_coordinator_false(self, tmp_path: Path, clean_env: None) -> None:
        teams_dir = _bootstrap_teams_dir(tmp_path, team_coordinator=False)
        ns = _make_namespace(
            name="ghost", coordinator=False, teams_dir=teams_dir
        )
        code = cli_mod._cmd_use(ns)
        assert code == EXIT_NOT_FOUND


# ---------------------------------------------------------------------------
# 完整 _cmd_use 行为
# ---------------------------------------------------------------------------


class TestCmdUseCoordinatorBehavior:
    """Requirement: 三锁门在 _cmd_use 行为。"""

    def test_team_not_found_exits_3(
        self, tmp_path: Path, clean_env: None, capsys: pytest.CaptureFixture
    ) -> None:
        teams_dir = _bootstrap_teams_dir(tmp_path)
        ns = _make_namespace(
            name="ghost", coordinator=True, teams_dir=teams_dir
        )
        code = cli_mod._cmd_use(ns)
        assert code == EXIT_NOT_FOUND
        captured = capsys.readouterr()
        assert "TeamNotFound" in captured.err

    def test_all_locks_hit_prints_coordinator(
        self,
        tmp_path: Path,
        clean_env: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config_path = _write_config(
            tmp_path, coordinator_enabled=True
        )
        teams_dir = _bootstrap_teams_dir(tmp_path, team_coordinator=True)
        ns = _make_namespace(
            name="devops",
            coordinator=True,
            config_path=config_path,
            teams_dir=teams_dir,
        )
        code = cli_mod._cmd_use(ns)
        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert "mode=coordinator" in captured.out
        assert "降级" not in captured.err

    def test_lock_missing_demotes_to_lead(
        self,
        tmp_path: Path,
        clean_env: None,
        capsys: pytest.CaptureFixture,
    ) -> None:
        # env var 未设(clean_env 已清)
        config_path = _write_config(
            tmp_path, coordinator_enabled=True
        )
        teams_dir = _bootstrap_teams_dir(tmp_path, team_coordinator=True)
        ns = _make_namespace(
            name="devops",
            coordinator=True,
            config_path=config_path,
            teams_dir=teams_dir,
        )
        code = cli_mod._cmd_use(ns)
        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert "mode=lead" in captured.out
        assert "env_var" in captured.err
        assert "降级" in captured.err

    def test_config_disabled_demotes(
        self,
        tmp_path: Path,
        clean_env: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config_path = _write_config(
            tmp_path, coordinator_enabled=False
        )
        teams_dir = _bootstrap_teams_dir(tmp_path, team_coordinator=True)
        ns = _make_namespace(
            name="devops",
            coordinator=True,
            config_path=config_path,
            teams_dir=teams_dir,
        )
        code = cli_mod._cmd_use(ns)
        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert "mode=lead" in captured.out
        assert "config.enabled" in captured.err

    def test_team_coordinator_false_demotes(
        self,
        tmp_path: Path,
        clean_env: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config_path = _write_config(
            tmp_path, coordinator_enabled=True
        )
        teams_dir = _bootstrap_teams_dir(tmp_path, team_coordinator=False)
        ns = _make_namespace(
            name="devops",
            coordinator=True,
            config_path=config_path,
            teams_dir=teams_dir,
        )
        code = cli_mod._cmd_use(ns)
        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert "mode=lead" in captured.out
        assert "team.coordinator" in captured.err

    def test_no_coordinator_flag_unchanged(
        self,
        tmp_path: Path,
        clean_env: None,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """现有路径 — 不传 --coordinator → mode=lead,无降级报告。"""
        teams_dir = _bootstrap_teams_dir(tmp_path, team_coordinator=False)
        ns = _make_namespace(
            name="devops", coordinator=False, teams_dir=teams_dir
        )
        code = cli_mod._cmd_use(ns)
        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert "mode=lead" in captured.out
        assert "降级" not in captured.err


# ---------------------------------------------------------------------------
# 顶层 main() 集成
# ---------------------------------------------------------------------------


class TestMainIntegration:
    """通过 cli_mod.main() 跑完整流程。"""

    def test_main_use_with_coordinator_flag(
        self,
        tmp_path: Path,
        clean_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAOZICODE_COORDINATOR", "1")
        config_path = _write_config(
            tmp_path, coordinator_enabled=True
        )
        teams_dir = _bootstrap_teams_dir(tmp_path, team_coordinator=True)

        code, out, err = _run_cli_via_main(
            [
                "team", "--teams-dir", str(teams_dir),
                "--config", str(config_path),
                "use", "--coordinator", "devops",
            ]
        )
        assert code == EXIT_OK
        assert "mode=coordinator" in out

    def test_main_use_missing_team_exits_3(
        self,
        tmp_path: Path,
        clean_env: None,
    ) -> None:
        teams_dir = _bootstrap_teams_dir(tmp_path)
        code, out, err = _run_cli_via_main(
            [
                "team", "--teams-dir", str(teams_dir),
                "use", "ghost",
            ]
        )
        assert code == EXIT_NOT_FOUND
        assert "TeamNotFound" in err