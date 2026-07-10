"""v1.4 Team Foundation — CLI 子命令测试。

覆盖 `openspec/changes/v1-4-team-foundation/specs/team-management/spec.md`
中 CLI acceptance scenario:

- 5 子命令 happy:create / list / show / use / destroy
- 参数错误:name 不合法 / team 不存在 / 重名
- destroy 确认交互:`--yes` 跳过 / 无 --yes 时 stdin 输 y 才走
- 退出码:0 / 1 / 2 / 3 / 4 / 5
- 错误格式:`Error: <EnumClass>: <detail>` 到 stderr
- `--teams-dir` 覆盖默认路径
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from baozicode.teams.cli import (
    EXIT_CONFIG,
    EXIT_GENERIC,
    EXIT_IO,
    EXIT_NAME_INVALID,
    EXIT_NOT_FOUND,
    EXIT_OK,
    main,
)


def _run_cli(
    argv: list[str], *, stdin: str | None = None, teams_dir: Path | None = None
) -> tuple[int, str, str]:
    """跑 main(argv) 并捕获 stdout / stderr + 退出码。

    Args:
        argv: 命令行参数(`team` 子命令起)
        stdin: 喂给 stdin 的字符串(`destroy` 确认用)
        teams_dir: 临时 teams dir(注入 --teams-dir 到 argv)

    Returns:
        (exit_code, stdout_text, stderr_text)
    """
    full_argv: list[str] = list(argv)
    if teams_dir is not None:
        full_argv = [full_argv[0], "--teams-dir", str(teams_dir), *full_argv[1:]]

    real_stdin = sys.stdin
    real_stdout = sys.stdout
    real_stderr = sys.stderr
    try:
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        code = main(full_argv)
        out = sys.stdout.getvalue()
        err = sys.stderr.getvalue()
    finally:
        sys.stdin = real_stdin
        sys.stdout = real_stdout
        sys.stderr = real_stderr
    return code, out, err


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


class TestCreateHappy:
    def test_create_returns_zero(self, tmp_path: Path) -> None:
        code, out, err = _run_cli(
            ["team", "create", "devops"], teams_dir=tmp_path
        )
        assert code == EXIT_OK
        assert "Created team 'devops'" in out
        assert (tmp_path / "devops" / "team.json").exists()
        assert err == ""

    def test_create_with_lead(self, tmp_path: Path) -> None:
        code, out, _ = _run_cli(
            ["team", "create", "devops", "--lead", "alice"], teams_dir=tmp_path
        )
        assert code == EXIT_OK
        team_json = json.loads(
            (tmp_path / "devops" / "team.json").read_text(encoding="utf-8")
        )
        assert team_json["lead"] == "alice"

    def test_create_emits_team_json(self, tmp_path: Path) -> None:
        _run_cli(["team", "create", "devops"], teams_dir=tmp_path)
        data = json.loads(
            (tmp_path / "devops" / "team.json").read_text(encoding="utf-8")
        )
        assert data["name"] == "devops"
        assert data["lead"] == "lead"
        assert data["members"] == {}
        assert data["schema_version"] == "1.0"


class TestCreateErrors:
    def test_invalid_name_bad_char(self, tmp_path: Path) -> None:
        code, out, err = _run_cli(
            ["team", "create", "DevOps"], teams_dir=tmp_path
        )
        assert code == EXIT_NAME_INVALID
        assert "Error: TeamNameBadChar" in err
        assert out == ""
        assert not (tmp_path / "DevOps").exists()

    def test_invalid_name_too_short(self, tmp_path: Path) -> None:
        code, _, err = _run_cli(["team", "create", "a"], teams_dir=tmp_path)
        assert code == EXIT_NAME_INVALID
        assert "Error: TeamNameTooShort" in err

    def test_invalid_name_double_hyphen(self, tmp_path: Path) -> None:
        code, _, err = _run_cli(
            ["team", "create", "dev--ops"], teams_dir=tmp_path
        )
        assert code == EXIT_NAME_INVALID
        assert "Error: TeamNameDoubleHyphen" in err

    def test_duplicate_returns_io_exit(self, tmp_path: Path) -> None:
        _run_cli(["team", "create", "devops"], teams_dir=tmp_path)
        code, _, err = _run_cli(
            ["team", "create", "devops"], teams_dir=tmp_path
        )
        assert code == EXIT_IO
        assert "Error: TeamAlreadyExists" in err


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_empty_returns_marker(self, tmp_path: Path) -> None:
        code, out, _ = _run_cli(["team", "list"], teams_dir=tmp_path)
        assert code == EXIT_OK
        assert out.strip() == "(no teams)"

    def test_multiple_alphabetical(self, tmp_path: Path) -> None:
        _run_cli(["team", "create", "zulu"], teams_dir=tmp_path)
        _run_cli(["team", "create", "alpha"], teams_dir=tmp_path)
        _run_cli(["team", "create", "mike"], teams_dir=tmp_path)
        code, out, _ = _run_cli(["team", "list"], teams_dir=tmp_path)
        assert code == EXIT_OK
        assert out.strip().splitlines() == ["alpha", "mike", "zulu"]


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShow:
    def test_show_pretty_json(self, tmp_path: Path) -> None:
        _run_cli(["team", "create", "devops"], teams_dir=tmp_path)
        code, out, _ = _run_cli(["team", "show", "devops"], teams_dir=tmp_path)
        assert code == EXIT_OK
        # JSON 解析通过 + 缩进
        parsed = json.loads(out)
        assert parsed["name"] == "devops"
        assert "\n" in out  # pretty-printed

    def test_show_missing(self, tmp_path: Path) -> None:
        code, _, err = _run_cli(["team", "show", "ghost"], teams_dir=tmp_path)
        assert code == EXIT_NOT_FOUND
        assert "Error: TeamNotFound" in err


# ---------------------------------------------------------------------------
# use
# ---------------------------------------------------------------------------


class TestUse:
    def test_use_existing(self, tmp_path: Path) -> None:
        _run_cli(["team", "create", "devops"], teams_dir=tmp_path)
        code, out, err = _run_cli(["team", "use", "devops"], teams_dir=tmp_path)
        assert code == EXIT_OK
        assert "Activated team 'devops'" in out
        assert "foundation" in err  # 占位说明走 stderr

    def test_use_missing(self, tmp_path: Path) -> None:
        code, _, err = _run_cli(["team", "use", "ghost"], teams_dir=tmp_path)
        assert code == EXIT_NOT_FOUND
        assert "Error: TeamNotFound" in err


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------


class TestDestroy:
    def test_destroy_with_yes_skips_prompt(self, tmp_path: Path) -> None:
        _run_cli(["team", "create", "devops"], teams_dir=tmp_path)
        # 不喂 stdin,只有 --yes 时不读 stdin
        code, out, _ = _run_cli(
            ["team", "destroy", "devops", "--yes"], teams_dir=tmp_path
        )
        assert code == EXIT_OK
        assert "Destroyed team 'devops'" in out
        assert not (tmp_path / "devops").exists()

    def test_destroy_without_yes_prompts(self, tmp_path: Path) -> None:
        _run_cli(["team", "create", "devops"], teams_dir=tmp_path)
        # 喂 y → 走删
        code, out, _ = _run_cli(
            ["team", "destroy", "devops"], stdin="y\n", teams_dir=tmp_path
        )
        assert code == EXIT_OK
        assert not (tmp_path / "devops").exists()

    def test_destroy_without_yes_aborts(self, tmp_path: Path) -> None:
        _run_cli(["team", "create", "devops"], teams_dir=tmp_path)
        code, out, err = _run_cli(
            ["team", "destroy", "devops"], stdin="n\n", teams_dir=tmp_path
        )
        assert code == EXIT_GENERIC
        assert "Aborted" in err
        assert (tmp_path / "devops").exists()  # 没删

    def test_destroy_missing_returns_not_found(self, tmp_path: Path) -> None:
        code, _, err = _run_cli(
            ["team", "destroy", "ghost", "--yes"], teams_dir=tmp_path
        )
        assert code == EXIT_NOT_FOUND
        assert "Error: TeamNotFound" in err

    def test_destroy_missing_with_force_returns_ok(self, tmp_path: Path) -> None:
        code, _, err = _run_cli(
            ["team", "destroy", "ghost", "--yes", "--force"], teams_dir=tmp_path
        )
        assert code == EXIT_OK
        assert "Warn: TeamNotFound" in err


# ---------------------------------------------------------------------------
# help / dispatch
# ---------------------------------------------------------------------------


class TestHelp:
    def test_help_top_level(self, tmp_path: Path, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "team" in out

    def test_help_team(self, tmp_path: Path, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["team", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "create" in out
        assert "list" in out
        assert "show" in out
        assert "use" in out
        assert "destroy" in out

    def test_no_action_shows_help(self, tmp_path: Path, capsys) -> None:
        # argparse 在 required subparsers 缺失时直接抛 SystemExit(2) ——
        # 这是 argparse 标准行为,不是 CLI 自己的 EXIT_GENERIC。
        # 测试验证 exit code 是 argparse 标准的 2(stderr 应含 usage)。
        with pytest.raises(SystemExit) as exc:
            main(["team"])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "usage:" in err
        assert "required" in err


# ---------------------------------------------------------------------------
# --teams-dir 覆盖默认
# ---------------------------------------------------------------------------


class TestTeamsDirOverride:
    def test_teams_dir_creates_separate_root(self, tmp_path: Path) -> None:
        custom = tmp_path / "myteams"
        code, _, _ = _run_cli(
            ["team", "create", "alpha"], teams_dir=custom
        )
        assert code == EXIT_OK
        assert (custom / "alpha" / "team.json").exists()
        # 默认目录不应被影响
        default_root = tmp_path  # 我们没在 tmp_path 直接建 team
        assert not (default_root / "alpha").exists()


# ---------------------------------------------------------------------------
# 退出码常量 sanity
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_exit_codes_distinct(self) -> None:
        codes = {
            EXIT_OK,
            EXIT_GENERIC,
            EXIT_NAME_INVALID,
            EXIT_NOT_FOUND,
            EXIT_IO,
            EXIT_CONFIG,
        }
        assert len(codes) == 6