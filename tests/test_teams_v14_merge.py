"""v1.4 Team Tools — `run_team_merge` git 顺序合并 helper 测试。

覆盖 `openspec/changes/v1-4-team-tools/specs/team-management/spec.md` 中
team_merge per-member branch sequential 部分:

- happy path(空 team / 单 member / 多 member dry_run)
- dry_run 不打 git
- 非 git repo → error
- 冲突 → merge --abort + aborted 列表收集(用 monkeypatch 模拟 git)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from baozicode.teams import TeamStore
from baozicode.teams.merge import run_team_merge
from baozicode.teams.schema import Member, Team


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def team_with_members(tmp_path: Path) -> Team:
    """构造一个含 alice + bob 的 Team 实例(team 目录不真建,只用于 merge 跑)。"""
    return Team(
        name="devops",
        members={
            "alice": Member(name="alice", role="dev",
                            workdir=".worktrees/alice",
                            backend="coroutine"),
            "bob": Member(name="bob", role="dev",
                          workdir=".worktrees/bob",
                          backend="coroutine"),
        },
    )


@pytest.fixture
def empty_team() -> Team:
    return Team(name="empty", members={})


@pytest.fixture
def single_member_team() -> Team:
    return Team(name="solo", members={
        "alice": Member(name="alice", role="dev",
                        workdir=".worktrees/alice",
                        backend="coroutine"),
    })


# ---------------------------------------------------------------------------
# dry_run
# ---------------------------------------------------------------------------


class TestDryRun:
    """Requirement: dry_run 不打 git,只返 plan。"""

    def test_dry_run_with_members(
        self, tmp_path: Path, team_with_members: Team
    ) -> None:
        result = run_team_merge(
            tmp_path, team_with_members, target="main", dry_run=True
        )
        assert result["status"] == "would-merge"
        assert result["target"] == "main"
        assert "wt/alice" in result["members"]
        assert "wt/bob" in result["members"]

    def test_dry_run_empty_team(
        self, tmp_path: Path, empty_team: Team
    ) -> None:
        result = run_team_merge(
            tmp_path, empty_team, target="main", dry_run=True
        )
        assert result["status"] == "would-merge"
        assert result["members"] == []

    def test_dry_run_no_git_invocation(
        self, tmp_path: Path, team_with_members: Team, monkeypatch
    ) -> None:
        """dry_run 必须完全不调 git。"""
        calls: list[Any] = []

        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        run_team_merge(
            tmp_path, team_with_members, target="main", dry_run=True
        )
        assert calls == []


# ---------------------------------------------------------------------------
# Non-git repo
# ---------------------------------------------------------------------------


class TestNonGitRepo:
    """Requirement: 非 git repo → error。"""

    def test_non_git_repo_returns_error(
        self, tmp_path: Path, team_with_members: Team
    ) -> None:
        # tmp_path 不是 git repo
        result = run_team_merge(tmp_path, team_with_members, target="main")
        assert result["status"] == "error"
        assert "not a git repository" in result["error"]


# ---------------------------------------------------------------------------
# Mock git subprocess to test happy / conflict paths
# ---------------------------------------------------------------------------


def _make_git_runner(
    monkeypatch,
    *,
    checkout_rc: int = 0,
    merge_results: dict[str, int] | None = None,
    abort_rc: int = 0,
):
    """构造一个 fake subprocess.run,模拟 git rev-parse + checkout + merge。

    Args:
        checkout_rc: checkout target 的 returncode
        merge_results: `branch → returncode` map;未列出的分支默认 0
        abort_rc: merge --abort 的 returncode
    """
    merge_results = merge_results or {}
    call_log: list[tuple[str, ...]] = []

    def fake_run(cmd, **kwargs):
        # 提取 git args(`git -C root <args...>`)→ args[0] 是 git sub-cmd
        args = tuple(cmd[3:])  # skip 'git', '-C', root
        call_log.append(args)
        # rev-parse --show-toplevel → 成功
        if args and args[0] == "rev-parse":
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        # checkout <target>
        if args and args[0] == "checkout":
            return subprocess.CompletedProcess(
                args=cmd, returncode=checkout_rc, stdout="", stderr=""
                if checkout_rc == 0
                else "fatal: pathspec 'X' did not match any file(s) known to git"
            )
        # merge --abort
        if args and args[0] == "merge" and "--abort" in args:
            return subprocess.CompletedProcess(
                args=cmd, returncode=abort_rc, stdout="", stderr=""
            )
        # merge --no-ff <branch> ...
        if args and args[0] == "merge":
            # git args: ['merge', '--no-ff', <branch>, '-m', <msg>]
            branch = args[2] if len(args) > 2 else ""
            rc = merge_results.get(branch, 0)
            stderr = "" if rc == 0 else f"CONFLICT in {branch}: simulated"
            return subprocess.CompletedProcess(
                args=cmd, returncode=rc, stdout="", stderr=stderr
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    return call_log


def _ensure_git_dir(project_root: Path) -> None:
    """Make the project root look like a git repo enough that
    `git rev-parse --show-toplevel` returns 0; we mock the run anyway."""
    (project_root / ".git").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestMergeHappy:
    """Requirement: 全 member merge clean → status='complete'。"""

    def test_single_member_clean(
        self, tmp_path: Path, single_member_team: Team, monkeypatch
    ) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        _ensure_git_dir(project_root)
        log = _make_git_runner(monkeypatch)

        result = run_team_merge(project_root, single_member_team)
        assert result["status"] == "complete"
        assert result["merged"] == ["alice"]
        assert result["aborted"] == []
        # log entries: ('rev-parse'|'checkout'|'merge', ...)
        assert any(len(c) > 0 and c[0] == "checkout" for c in log)
        assert any(len(c) > 0 and c[0] == "merge" for c in log)

    def test_multi_member_clean(
        self, tmp_path: Path, team_with_members: Team, monkeypatch
    ) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        _ensure_git_dir(project_root)
        _make_git_runner(monkeypatch)

        result = run_team_merge(project_root, team_with_members)
        assert result["status"] == "complete"
        assert result["merged"] == ["alice", "bob"]
        assert result["aborted"] == []


# ---------------------------------------------------------------------------
# Conflict path
# ---------------------------------------------------------------------------


class TestMergeConflict:
    """Requirement: 冲突 → merge --abort + aborted 列表 + status='partial'。"""

    def test_bob_conflicts(
        self, tmp_path: Path, team_with_members: Team, monkeypatch
    ) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        _ensure_git_dir(project_root)
        log = _make_git_runner(
            monkeypatch,
            merge_results={"wt/bob": 1},  # bob 冲突
        )

        result = run_team_merge(project_root, team_with_members)
        assert result["status"] == "partial"
        assert result["merged"] == ["alice"]
        assert len(result["aborted"]) == 1
        assert result["aborted"][0]["member"] == "bob"
        assert "CONFLICT" in result["aborted"][0]["reason"]
        # 应该跑了 merge --abort (log entry 含 --abort)
        assert any("--abort" in c for c in log)

    def test_target_checkout_fails(
        self, tmp_path: Path, team_with_members: Team, monkeypatch
    ) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        _ensure_git_dir(project_root)
        _make_git_runner(monkeypatch, checkout_rc=128)

        result = run_team_merge(project_root, team_with_members)
        assert result["status"] == "error"
        assert "checkout" in result["error"]


# ---------------------------------------------------------------------------
# Single / empty team
# ---------------------------------------------------------------------------


class TestEmptyAndSingle:
    def test_empty_team(
        self, tmp_path: Path, empty_team: Team, monkeypatch
    ) -> None:
        project_root = tmp_path / "proj"
        project_root.mkdir()
        _ensure_git_dir(project_root)
        _make_git_runner(monkeypatch)

        result = run_team_merge(project_root, empty_team)
        assert result["status"] == "complete"
        assert result["merged"] == []
        assert result["aborted"] == []