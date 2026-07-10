"""v1.4 Team Foundation — CLI 端到端测试。

用 `subprocess.run(["python", "-m", "baozicode", "team", ...])` 真实跑
顶层 CLI,验证:

- 退出码(0 / 2 / 3 / 4)
- stdout / stderr 分离
- 磁盘副作用(team 目录 + team.json)
- 全链路 create → list → show → destroy 顺序可跑
- `baozicode --help` 不带 team 子命令时仍能显示(不破坏 TUI 老路径)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(*args: str, teams_dir: Path | None = None, timeout: int = 15) -> subprocess.CompletedProcess:
    """跑 `python -m baozicode team ...` 子进程。

    Args:
        *args: 顶层参数(`team <action> ...`)
        teams_dir: 显式 `--teams-dir` 覆盖
        timeout: 子进程超时(秒)

    Returns:
        `subprocess.CompletedProcess` 实例(returncode / stdout / stderr)
    """
    cmd = [sys.executable, "-m", "baozicode", *args]
    if teams_dir is not None:
        # 把 --teams-dir 插到 team 之后,这样 argparse 子 parser 收到
        cmd = [sys.executable, "-m", "baozicode", "team",
               "--teams-dir", str(teams_dir), *args[1:]]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 5 子命令 happy path
# ---------------------------------------------------------------------------


class TestCreateEndToEnd:
    def test_create_returns_zero_and_writes_team_json(
        self, tmp_path: Path
    ) -> None:
        teams_dir = tmp_path / "teams"
        result = _run_cli("team", "create", "devops", teams_dir=teams_dir)
        assert result.returncode == 0
        assert "Created team 'devops'" in result.stdout

        team_json = teams_dir / "devops" / "team.json"
        assert team_json.exists()
        data = json.loads(team_json.read_text(encoding="utf-8"))
        assert data["name"] == "devops"
        assert data["lead"] == "lead"


class TestListEndToEnd:
    def test_list_empty_shows_marker(self, tmp_path: Path) -> None:
        teams_dir = tmp_path / "teams"
        result = _run_cli("team", "list", teams_dir=teams_dir)
        assert result.returncode == 0
        assert "(no teams)" in result.stdout

    def test_list_after_creates_alphabetical(self, tmp_path: Path) -> None:
        teams_dir = tmp_path / "teams"
        for n in ["zulu", "alpha", "mike"]:
            r = _run_cli("team", "create", n, teams_dir=teams_dir)
            assert r.returncode == 0

        result = _run_cli("team", "list", teams_dir=teams_dir)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["alpha", "mike", "zulu"]


class TestShowEndToEnd:
    def test_show_pretty_json(self, tmp_path: Path) -> None:
        teams_dir = tmp_path / "teams"
        _run_cli("team", "create", "devops", "--lead", "alice", teams_dir=teams_dir)

        result = _run_cli("team", "show", "devops", teams_dir=teams_dir)
        assert result.returncode == 0
        # JSON 解析 + 有缩进
        parsed = json.loads(result.stdout)
        assert parsed["name"] == "devops"
        assert parsed["lead"] == "alice"
        assert "\n" in result.stdout  # pretty


class TestUseEndToEnd:
    def test_use_existing_team(self, tmp_path: Path) -> None:
        teams_dir = tmp_path / "teams"
        _run_cli("team", "create", "devops", teams_dir=teams_dir)

        result = _run_cli("team", "use", "devops", teams_dir=teams_dir)
        assert result.returncode == 0
        assert "Activated team 'devops'" in result.stdout


class TestDestroyEndToEnd:
    def test_destroy_with_yes(self, tmp_path: Path) -> None:
        teams_dir = tmp_path / "teams"
        _run_cli("team", "create", "devops", teams_dir=teams_dir)

        result = _run_cli("team", "destroy", "devops", "--yes", teams_dir=teams_dir)
        assert result.returncode == 0
        assert "Destroyed team 'devops'" in result.stdout
        assert not (teams_dir / "devops").exists()

    def test_destroy_missing_with_force(self, tmp_path: Path) -> None:
        teams_dir = tmp_path / "teams"
        result = _run_cli(
            "team", "destroy", "ghost", "--yes", "--force", teams_dir=teams_dir
        )
        assert result.returncode == 0
        assert "Warn: TeamNotFound" in result.stderr

    def test_destroy_missing_without_force(self, tmp_path: Path) -> None:
        teams_dir = tmp_path / "teams"
        result = _run_cli(
            "team", "destroy", "ghost", "--yes", teams_dir=teams_dir
        )
        assert result.returncode == 3
        assert "Error: TeamNotFound" in result.stderr


# ---------------------------------------------------------------------------
# 错误退出码
# ---------------------------------------------------------------------------


class TestErrorExitCodes:
    def test_invalid_name_exits_2(self, tmp_path: Path) -> None:
        result = _run_cli("team", "create", "BadName", teams_dir=tmp_path / "teams")
        assert result.returncode == 2
        assert "Error: TeamNameBadChar" in result.stderr

    def test_duplicate_exits_4(self, tmp_path: Path) -> None:
        teams_dir = tmp_path / "teams"
        _run_cli("team", "create", "devops", teams_dir=teams_dir)
        result = _run_cli("team", "create", "devops", teams_dir=teams_dir)
        assert result.returncode == 4
        assert "Error: TeamAlreadyExists" in result.stderr

    def test_show_missing_exits_3(self, tmp_path: Path) -> None:
        result = _run_cli("team", "show", "ghost", teams_dir=tmp_path / "teams")
        assert result.returncode == 3
        assert "Error: TeamNotFound" in result.stderr


# ---------------------------------------------------------------------------
# 顶层 help 不被破坏
# ---------------------------------------------------------------------------


class TestTopLevelHelpStillWorks:
    def test_top_level_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "baozicode", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        # 顶层 help 应同时含 TUI 老选项 + team 子命令
        assert "--resume" in result.stdout
        assert "--new" in result.stdout
        assert "team" in result.stdout

    def test_team_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "baozicode", "team", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "create" in result.stdout
        assert "destroy" in result.stdout


# ---------------------------------------------------------------------------
# 完整 lifecycle 端到端
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_create_list_show_destroy_sequence(self, tmp_path: Path) -> None:
        teams_dir = tmp_path / "teams"

        # Create 3 teams
        for n in ["acme", "devops", "qa"]:
            r = _run_cli("team", "create", n, teams_dir=teams_dir)
            assert r.returncode == 0

        # List
        r = _run_cli("team", "list", teams_dir=teams_dir)
        assert r.returncode == 0
        assert r.stdout.strip().splitlines() == ["acme", "devops", "qa"]

        # Show
        r = _run_cli("team", "show", "devops", teams_dir=teams_dir)
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["name"] == "devops"

        # Destroy
        r = _run_cli("team", "destroy", "devops", "--yes", teams_dir=teams_dir)
        assert r.returncode == 0

        # List 减一
        r = _run_cli("team", "list", teams_dir=teams_dir)
        assert r.stdout.strip().splitlines() == ["acme", "qa"]

        # Show 失败
        r = _run_cli("team", "show", "devops", teams_dir=teams_dir)
        assert r.returncode == 3