"""v1.3 Worktree Isolation — `WorktreeCleanupDaemon` 三层过滤测试。

覆盖 `openspec/changes/v1-3-worktree-isolation/specs/worktree-isolation/
spec.md:WorktreeCleanupDaemon three-layer filter` 的 acceptance
scenario。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Iterable

import pytest

from baozicode.worktree import (
    CleanupAction,
    CleanupActionType,
    TaskActiveProbe,
    WorktreeCleanupDaemon,
    WorktreeManager,
)


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> None:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败 ({proc.returncode}): "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@x.io")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# test\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


class _FakeTaskProbe:
    """最小 TaskActiveProbe 实现 —— 由测试 set_active 控制。"""

    def __init__(self) -> None:
        self._active: set[str] = set()

    def set_active(self, *names: str) -> None:
        self._active = set(names)

    def is_task_active(self, name: str) -> bool:
        return name in self._active


def _age_file(path: Path, seconds_ago: float) -> None:
    """把文件 mtime 调到 `seconds_ago` 之前。"""
    target = time.time() - seconds_ago
    os.utime(str(path), (target, target))


# ---------------------------------------------------------------------------
# 三层过滤场景
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestThreeLayerFilter:
    """`WorktreeCleanupDaemon.run_once` 三层过滤每个场景。"""

    @pytest.mark.asyncio
    async def test_active_task_blocks_cleanup(
        self, git_repo: Path,
    ) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("api-designer")
        # 让 mtime "很久以前"(默认 60min retention;调到 90min 前)
        _age_file(spec.path, 90 * 60)

        probe = _FakeTaskProbe()
        probe.set_active("api-designer")
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=60, interval_seconds=1.0,
        )
        actions = await daemon.run_once()
        assert len(actions) == 1
        a = actions[0]
        assert a.name == "api-designer"
        assert a.type == CleanupActionType.SKIPPED
        assert a.reason == "task_active"
        # 路径仍在
        assert spec.path.exists()

    @pytest.mark.asyncio
    async def test_fresh_skipped(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("api-designer")
        # mtime 刚建 → "fresh",保留(默认 60 min)

        probe = _FakeTaskProbe()  # 没 active 任务
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=60, interval_seconds=1.0,
        )
        actions = await daemon.run_once()
        assert len(actions) == 1
        a = actions[0]
        assert a.type == CleanupActionType.SKIPPED
        assert a.reason == "fresh"
        assert spec.path.exists()

    @pytest.mark.asyncio
    async def test_stale_cleaned(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("old-agent")
        _age_file(spec.path, 90 * 60)  # 90 min 前

        probe = _FakeTaskProbe()
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=60, interval_seconds=1.0,
        )
        actions = await daemon.run_once()
        assert len(actions) == 1
        a = actions[0]
        assert a.name == "old-agent"
        assert a.type == CleanupActionType.CLEANED
        assert a.reason == "clean"
        # 真删了
        assert not spec.path.exists()

    @pytest.mark.asyncio
    async def test_dirty_blocked(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("dirty-agent")
        # 写脏文件
        (spec.path / "uncommitted.txt").write_text("data")
        _age_file(spec.path, 90 * 60)

        probe = _FakeTaskProbe()
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=60, interval_seconds=1.0,
        )
        actions = await daemon.run_once()
        assert len(actions) == 1
        a = actions[0]
        assert a.type == CleanupActionType.BLOCKED
        assert a.reason == "uncommitted_changes"
        # 路径保留
        assert spec.path.exists()
        # 脏文件还在
        assert (spec.path / "uncommitted.txt").exists()

    @pytest.mark.asyncio
    async def test_unpushed_blocked(self, git_repo: Path) -> None:
        """branch 有 upstream + 未推送 commit → BLOCKED."""
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("ahead")
        # 加 upstream(用 main 分支作为 upstream)
        _git(spec.path, "branch", "--set-upstream-to", "main")
        # 加 1 个 commit 在 wt/ahead 上(ahead of main)
        (spec.path / "new.txt").write_text("new")
        _git(spec.path, "add", ".")
        _git(spec.path, "commit", "-m", "ahead commit")
        _age_file(spec.path, 90 * 60)

        probe = _FakeTaskProbe()
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=60, interval_seconds=1.0,
        )
        actions = await daemon.run_once()
        assert len(actions) == 1
        a = actions[0]
        # local commit 视 main 是 upstream → 有未推送
        assert a.type == CleanupActionType.BLOCKED
        assert a.reason in {"uncommitted_changes", "unpushed_commits"}
        assert spec.path.exists()


# ---------------------------------------------------------------------------
# 多 worktree + daemon 行为
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestDaemonBehavior:
    """多 worktree 混合场景 + 启动 / 停止 lifecycle。"""

    @pytest.mark.asyncio
    async def test_mixed_actions(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)

        # 4 个 worktree:不同状态
        active_fresh = await mgr.create("active-fresh")
        # 写脏 + 旧
        dirty_old = await mgr.create("dirty-old")
        (dirty_old.path / "u.txt").write_text("x")
        _age_file(dirty_old.path, 90 * 60)
        # clean + 旧
        clean_old = await mgr.create("clean-old")
        _age_file(clean_old.path, 90 * 60)
        # clean + 旧 + task active
        clean_old_active = await mgr.create("clean-old-active")
        _age_file(clean_old_active.path, 90 * 60)

        probe = _FakeTaskProbe()
        probe.set_active("clean-old-active")
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=60, interval_seconds=1.0,
        )
        actions = await daemon.run_once()
        by_name = {a.name: a for a in actions}
        assert set(by_name) == {
            "active-fresh",
            "dirty-old",
            "clean-old",
            "clean-old-active",
        }
        # active-fresh → fresh SKIP
        assert by_name["active-fresh"].reason == "fresh"
        # dirty-old → blocked
        assert by_name["dirty-old"].type == CleanupActionType.BLOCKED
        # clean-old → cleaned
        assert by_name["clean-old"].type == CleanupActionType.CLEANED
        # clean-old-active → task_active SKIP
        assert by_name["clean-old-active"].reason == "task_active"

        # 文件系统断言
        assert active_fresh.path.exists()
        assert dirty_old.path.exists()
        assert not clean_old.path.exists()  # 真删
        assert clean_old_active.path.exists()

    @pytest.mark.asyncio
    async def test_no_worktrees_no_actions(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        probe = _FakeTaskProbe()
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=60, interval_seconds=1.0,
        )
        actions = await daemon.run_once()
        assert actions == []

    @pytest.mark.asyncio
    async def test_start_stop_loop(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        await mgr.create("wt1")

        probe = _FakeTaskProbe()
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=60, interval_seconds=0.1,  # 快轮询
        )
        await daemon.start()
        # 等几轮
        await asyncio.sleep(0.35)
        await daemon.stop()
        # 跑过几轮 → 至少一次 run_once 调用;worktree 因 fresh 不动
        spec = await mgr.create("wt2")
        assert spec.path.exists()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        probe = _FakeTaskProbe()
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=60, interval_seconds=0.1,
        )
        await daemon.start()
        await daemon.start()  # 第二次幂等
        await daemon.stop()

    @pytest.mark.asyncio
    async def test_retention_zero_treats_as_stale_immediately(
        self, git_repo: Path,
    ) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("z")

        probe = _FakeTaskProbe()
        daemon = WorktreeCleanupDaemon(
            manager=mgr, task_probe=probe,
            retention_minutes=0, interval_seconds=1.0,
        )
        actions = await daemon.run_once()
        # 0 retention → 任何 mtime 都满足 → 调 exit → clean → CLEANED
        assert len(actions) == 1
        assert actions[0].type == CleanupActionType.CLEANED
        assert not spec.path.exists()


# ---------------------------------------------------------------------------
# Protocol 形状
# ---------------------------------------------------------------------------


class TestTaskActiveProbeProtocol:
    """`TaskActiveProbe` Protocol 形状 + runtime checkable。"""

    def test_fake_probe_isinstance(self) -> None:
        probe = _FakeTaskProbe()
        assert isinstance(probe, TaskActiveProbe)

    def test_default_probe_says_no_active(self) -> None:
        probe = _FakeTaskProbe()
        assert not probe.is_task_active("anything")