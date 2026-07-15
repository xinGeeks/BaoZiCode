"""v1.4 Pane Backend — `baozicode member run` CLI 测试。

覆盖 `openspec/changes/v1-4-team-pane-backend/specs/team-management/spec.md`
中 CLI Requirement:

- `add_member_subcommand` 注册 `member` + `run` 二级子命令
- `--team` `--name` 必填校验(argparse UsageError,exit 2)
- `--cwd` 可选
- team 不存在 → exit 3 (`EXIT_NOT_FOUND`)
- member 不存在 → exit 6 (`EXIT_MEMBER_NOT_FOUND`)
- `os.chdir` 到 `--cwd` 或 `member.workdir`
- graceful 退出 → exit 0
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_team(teams_dir: Path, team_name: str, member_name: str) -> Path:
    """在 teams_dir/<team>/ 建一份真实 team.json + member dir。"""
    team_dir = teams_dir / team_name
    member_dir = team_dir / member_name
    member_dir.mkdir(parents=True, exist_ok=True)
    from baozicode.teams.schema import Member, Team

    team = Team(
        name=team_name,
        members={
            member_name: Member(
                name=member_name, role="dev",
                workdir=str(member_dir),
                backend="coroutine",
            ),
        },
    )
    team.save(team_dir / "team.json")
    return member_dir


class _FakeLoop:
    """替代 MemberMainLoop.run() 的桩 — 立刻返回。"""

    def __init__(self, *args, **kwargs) -> None:
        self._terminated = False

    async def run(self) -> None:
        return None

    def request_terminate(self) -> None:
        self._terminated = True

    @property
    def is_terminated(self) -> bool:
        return self._terminated


@pytest.fixture
def teams_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".baozicode" / "teams"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def devops_team(teams_dir: Path) -> Path:
    """<teams_dir>/devops/alice/ 已建好,workdir = <teams_dir>/devops/alice。"""
    return _build_team(teams_dir, "devops", "alice")


# ---------------------------------------------------------------------------
# `add_member_subcommand` 注册
# ---------------------------------------------------------------------------


class TestAddMemberSubcommand:
    """`member` 二级子命令挂到顶层 argparse。"""

    def test_member_registered_at_top_level(self) -> None:
        import argparse
        from baozicode.teams.cli import add_member_subcommand

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="top_command")
        add_member_subcommand(sub)
        args = parser.parse_args(
            ["member", "run", "--team", "foo", "--name", "bar"]
        )
        assert args.top_command == "member"
        assert args.member_action == "run"
        assert args.team == "foo"
        assert args.name == "bar"

    def test_team_required(self) -> None:
        import argparse
        from baozicode.teams.cli import add_member_subcommand

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="top_command")
        add_member_subcommand(sub)
        with pytest.raises(SystemExit):
            parser.parse_args(["member", "run", "--name", "bar"])

    def test_name_required(self) -> None:
        import argparse
        from baozicode.teams.cli import add_member_subcommand

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="top_command")
        add_member_subcommand(sub)
        with pytest.raises(SystemExit):
            parser.parse_args(["member", "run", "--team", "foo"])

    def test_cwd_optional(self) -> None:
        import argparse
        from baozicode.teams.cli import add_member_subcommand

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="top_command")
        add_member_subcommand(sub)
        args = parser.parse_args(
            ["member", "run", "--team", "foo", "--name", "bar",
             "--cwd", "/tmp/x"]
        )
        assert args.cwd == "/tmp/x"

    def test_member_action_required(self) -> None:
        """`member` 不带 run/show 应 SystemExit。"""
        import argparse
        from baozicode.teams.cli import add_member_subcommand

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="top_command")
        add_member_subcommand(sub)
        with pytest.raises(SystemExit):
            parser.parse_args(["member"])


# ---------------------------------------------------------------------------
# `main_member_run` 行为
# ---------------------------------------------------------------------------


class TestMainMemberRun:
    """async 入口:registry 解析 + chdir + MemberMainLoop.run()。"""

    def test_team_not_found_exits_3(
        self, teams_dir: Path, monkeypatch
    ) -> None:
        from baozicode.teams import cli as teams_cli

        async def run() -> None:
            args = _make_args(teams_dir, "ghost", "alice")
            rc = await teams_cli.main_member_run(args)
            assert rc == teams_cli.EXIT_NOT_FOUND
        asyncio.run(run())

    def test_member_not_found_exits_6(
        self, teams_dir: Path, devops_team: Path, monkeypatch
    ) -> None:
        from baozicode.teams import cli as teams_cli

        async def run() -> None:
            args = _make_args(teams_dir, "devops", "ghost")
            rc = await teams_cli.main_member_run(args)
            assert rc == teams_cli.EXIT_MEMBER_NOT_FOUND
        asyncio.run(run())

    def test_chdir_to_cwd_override(
        self, teams_dir: Path, devops_team: Path, monkeypatch, tmp_path
    ) -> None:
        """`--cwd` 覆盖 member.workdir。"""
        custom_cwd = tmp_path / "custom_member_cwd"
        custom_cwd.mkdir()

        from baozicode.teams import cli as teams_cli
        from tests._agent_helpers import make_minimal_config

        async def run() -> None:
            args = _make_args(teams_dir, "devops", "alice", cwd=str(custom_cwd))
            monkeypatch.setattr(
                "baozicode.teams.cli.MemberMainLoop", _FakeLoop,
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.load_config",
                lambda _path=None: make_minimal_config(),
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.create_client",
                lambda cfg: _FakeLLM(),
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.perm_bootstrap",
                lambda *_a, **_kw: _FakePerms(),
            )
            rc = await teams_cli.main_member_run(args)
            assert rc == teams_cli.EXIT_OK
            assert Path.cwd().resolve() == custom_cwd.resolve()
        asyncio.run(run())

    def test_chdir_to_member_workdir_fallback(
        self, teams_dir: Path, devops_team: Path, monkeypatch
    ) -> None:
        """无 `--cwd` → 走 member.workdir(已存在)。"""
        from baozicode.teams import cli as teams_cli
        from tests._agent_helpers import make_minimal_config

        async def run() -> None:
            args = _make_args(teams_dir, "devops", "alice")
            monkeypatch.setattr(
                "baozicode.teams.cli.MemberMainLoop", _FakeLoop,
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.load_config",
                lambda _path=None: make_minimal_config(),
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.create_client",
                lambda cfg: _FakeLLM(),
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.perm_bootstrap",
                lambda *_a, **_kw: _FakePerms(),
            )
            rc = await teams_cli.main_member_run(args)
            assert rc == teams_cli.EXIT_OK
            assert Path.cwd().resolve() == devops_team.resolve()
        asyncio.run(run())

    def test_graceful_exit_returns_0(
        self, teams_dir: Path, devops_team: Path, monkeypatch
    ) -> None:
        from baozicode.teams import cli as teams_cli
        from tests._agent_helpers import make_minimal_config

        async def run() -> None:
            args = _make_args(teams_dir, "devops", "alice")
            monkeypatch.setattr(
                "baozicode.teams.cli.MemberMainLoop", _FakeLoop,
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.load_config",
                lambda _path=None: make_minimal_config(),
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.create_client",
                lambda cfg: _FakeLLM(),
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.perm_bootstrap",
                lambda *_a, **_kw: _FakePerms(),
            )
            rc = await teams_cli.main_member_run(args)
            assert rc == teams_cli.EXIT_OK
        asyncio.run(run())

    def test_keyboard_interrupt_returns_0(
        self, teams_dir: Path, devops_team: Path, monkeypatch
    ) -> None:
        """loop.run 抛 KeyboardInterrupt → graceful return 0。"""

        class _KI:
            def __init__(self, *a, **kw) -> None:
                pass

            async def run(self) -> None:
                raise KeyboardInterrupt

            def request_terminate(self) -> None:
                pass

        from baozicode.teams import cli as teams_cli
        from tests._agent_helpers import make_minimal_config

        async def run() -> None:
            args = _make_args(teams_dir, "devops", "alice")
            monkeypatch.setattr(
                "baozicode.teams.cli.MemberMainLoop", _KI,
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.load_config",
                lambda _path=None: make_minimal_config(),
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.create_client",
                lambda cfg: _FakeLLM(),
            )
            monkeypatch.setattr(
                "baozicode.teams.cli.perm_bootstrap",
                lambda *_a, **_kw: _FakePerms(),
            )
            rc = await teams_cli.main_member_run(args)
            assert rc == teams_cli.EXIT_OK
        asyncio.run(run())


class _FakeLLM:
    """LLMClient stub — 不调真 API。"""
    async def stream(self, messages, system, tools, **kwargs):
        from baozicode.agent.events import AgentEvent
        yield AgentEvent(type="done", stop_reason="COMPLETED", payload={})


class _FakePerms:
    """MergedPermissions stub。"""
    def __init__(self) -> None:
        self.mode = "default"
        self.rules = []
        from pathlib import Path
        self.real_root = Path("/")
        self.path_sandbox_enabled = False
        self.session_rules = []


def _make_args(
    teams_dir: Path,
    team_name: str,
    member_name: str,
    cwd: str | None = None,
) -> "object":
    """构造 `main_member_run(args)` 期望的 Namespace。"""
    import argparse
    args = argparse.Namespace(
        config=None,
        teams_dir=str(teams_dir),
        member_action="run",
        team=team_name,
        name=member_name,
        cwd=cwd,
    )
    return args