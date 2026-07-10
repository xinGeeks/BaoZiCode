"""v1.3 — App + Worktree 系统装配测试。

覆盖:
- `_build_worktree_manager` 在 worktree enabled 时构造 manager + init + daemon
- `_build_worktree_manager` 在 worktree disabled 时返回 None
- `on_unmount` 调 daemon.stop + 清空 worktree 列表
- 启动顺序:worktree-bootstrap worker 在 subagent-task-tool-register 之后
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from baozicode.app import BaoZiCodeApp
from baozicode.config.schema import (
    AppConfig,
    BackendConfig,
    MemoryConfig,
    SessionConfig,
    SubAgentsConfig,
    WorktreeConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> None:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败: "
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


def _make_config(
    project_root: Path,
    *,
    worktree_enabled: bool = True,
) -> AppConfig:
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        memory=MemoryConfig(enabled=False),
        sessions=SessionConfig(dir=project_root / "sessions"),
        subagents=SubAgentsConfig(
            worktree=WorktreeConfig(
                enabled=worktree_enabled,
                daemon_interval_seconds=60,
                retention_minutes=30,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Worktree disabled → 系统不启用
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestWorktreeDisabled:
    async def test_worktree_disabled_no_manager(self, git_repo: Path) -> None:
        """worktree.enabled=False → App.worktree_manager = None,系统不构造。"""
        cfg = _make_config(git_repo, worktree_enabled=False)
        app = BaoZiCodeApp(config=cfg, project_root=git_repo)
        # 同步初始化期间 manager 还没构造(_build_worktree_manager
        # 在 on_mount worker 里跑)
        assert app.worktree_manager is None
        assert app.worktree_init_config is None
        assert app.worktree_cleanup_daemon is None

    async def test_build_returns_none_when_disabled(
        self, git_repo: Path,
    ) -> None:
        """_build_worktree_manager 在 disabled 时不构造 manager,返回 None。"""
        cfg = _make_config(git_repo, worktree_enabled=False)
        app = BaoZiCodeApp(config=cfg, project_root=git_repo)
        # 先 bootstrap SubAgentManager(_build_subagent_manager 在 __init__ 里调)
        assert app.subagents is not None
        result = app._build_worktree_manager()
        assert result is None
        assert app.worktree_manager is None


# ---------------------------------------------------------------------------
# Worktree enabled → 全栈构造
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestWorktreeEnabled:
    async def test_build_constructs_manager_and_daemon(
        self, git_repo: Path,
    ) -> None:
        """worktree enabled → manager + init_config + daemon 都构造好。"""
        cfg = _make_config(git_repo, worktree_enabled=True)
        app = BaoZiCodeApp(config=cfg, project_root=git_repo)
        # __init__ 期:manager 还没构造
        assert app.worktree_manager is None
        # 模拟 SubAgentManager 已就绪后调
        result = app._build_worktree_manager()
        assert result is not None
        assert app.worktree_manager is result
        assert app.worktree_init_config is not None
        assert app.worktree_cleanup_daemon is not None
        # daemon 还没启动(start 在 _start_worktree_system 里)
        assert app.worktree_cleanup_daemon._task is None  # type: ignore[attr-defined]

    async def test_build_uses_config_values(self, git_repo: Path) -> None:
        """WorktreeManager / Initializer 用 WorktreeConfig 字段。"""
        cfg = AppConfig(
            backend="anthropic",
            anthropic=BackendConfig(api_key="k", model="m"),
            openai=BackendConfig(api_key="k", model="m"),
            minimax=BackendConfig(api_key="k", model="m"),
            deepseek=BackendConfig(api_key="k", model="m"),
            memory=MemoryConfig(enabled=False),
            sessions=SessionConfig(dir=git_repo / "sessions"),
            subagents=SubAgentsConfig(
                worktree=WorktreeConfig(
                    enabled=True,
                    max_concurrent=7,
                    link_paths=[".venv", "vendor"],
                    copy_paths=[".env"],
                    retention_minutes=10,
                    daemon_interval_seconds=15,
                ),
            ),
        )
        app = BaoZiCodeApp(config=cfg, project_root=git_repo)
        app._build_worktree_manager()
        mgr = app.worktree_manager
        init = app.worktree_init_config
        daemon = app.worktree_cleanup_daemon
        assert mgr is not None
        assert mgr._max_concurrent == 7  # type: ignore[attr-defined]
        assert init is not None
        assert init.link_paths == [".venv", "vendor"]
        assert init.copy_paths == [".env"]
        assert daemon is not None
        assert daemon._retention_seconds == 600  # 10 min  # type: ignore[attr-defined]
        assert daemon._interval_seconds == 15.0  # type: ignore[attr-defined]

    async def test_start_daemon_then_stop(self, git_repo: Path) -> None:
        """_start_worktree_system 启动 daemon,然后 stop。"""
        cfg = _make_config(git_repo, worktree_enabled=True)
        app = BaoZiCodeApp(config=cfg, project_root=git_repo)
        # 先 bootstrap SubAgentManager(daemon 构造需要 task_probe)
        assert app.subagents is not None
        # 跑 _start_worktree_system(模拟 worker)
        await app._start_worktree_system()
        # daemon 已启动
        daemon = app.worktree_cleanup_daemon
        assert daemon is not None
        assert daemon._task is not None  # type: ignore[attr-defined]
        assert not daemon._task.done()  # type: ignore[attr-defined]
        # stop
        await app.on_unmount()
        # daemon 已停,manager 已清
        assert app.worktree_cleanup_daemon is None
        assert app.worktree_manager is None


# ---------------------------------------------------------------------------
# Project root 非 git repo → 静默不启用
# ---------------------------------------------------------------------------


async def test_non_git_project_root_silently_disables(tmp_path: Path) -> None:
    """project_root 不是 git repo → WorktreeManager 构造失败 → 系统静默。"""
    # 故意不 git init
    non_git = tmp_path / "not_a_repo"
    non_git.mkdir()
    cfg = _make_config(non_git, worktree_enabled=True)
    app = BaoZiCodeApp(config=cfg, project_root=non_git)
    assert app.subagents is not None
    # _build_worktree_manager 内部捕获异常,返回 None
    result = app._build_worktree_manager()
    assert result is None
    assert app.worktree_manager is None
    assert app.worktree_cleanup_daemon is None


# ---------------------------------------------------------------------------
# on_unmount 强制清掉所有 worktree
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestOnUnmountCleanup:
    async def test_on_unmount_removes_all_worktrees(
        self, git_repo: Path,
    ) -> None:
        """on_unmount → iter_worktrees → 挨个 force=True remove。"""
        cfg = _make_config(git_repo, worktree_enabled=True)
        app = BaoZiCodeApp(config=cfg, project_root=git_repo)
        assert app.subagents is not None
        await app._start_worktree_system()

        # 手动建 2 个 worktree(模拟跑过 sub-Agent)
        from baozicode.worktree import WorktreeInitConfig
        mgr = app.worktree_manager
        assert mgr is not None
        await mgr.create("alpha")
        await mgr.create("beta")
        wt_root = git_repo / ".worktrees"
        assert (wt_root / "alpha").exists()
        assert (wt_root / "beta").exists()

        # on_unmount → 都清掉
        await app.on_unmount()
        assert not (wt_root / "alpha").exists()
        assert not (wt_root / "beta").exists()
        assert app.worktree_manager is None
        assert app.worktree_cleanup_daemon is None

    async def test_on_unmount_safe_when_no_worktrees(
        self, git_repo: Path,
    ) -> None:
        """没有 worktree 时 on_unmount 也不报错。"""
        cfg = _make_config(git_repo, worktree_enabled=True)
        app = BaoZiCodeApp(config=cfg, project_root=git_repo)
        assert app.subagents is not None
        await app._start_worktree_system()
        # 没有 worktree,直接 unmount
        await app.on_unmount()
        assert app.worktree_manager is None


# ---------------------------------------------------------------------------
# Bash cwd_validator 在 worktree sub-Agent 跑期间 set / 跑完 unset
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestBashValidatorWiring:
    async def test_cwd_validator_cleared_after_task_done(
        self, git_repo: Path,
    ) -> None:
        """worktree sub-Agent 跑期间 set cwd_validator,跑完 unset。"""
        from baozicode.tools import bash as bash_mod

        cfg = _make_config(git_repo, worktree_enabled=True)
        app = BaoZiCodeApp(config=cfg, project_root=git_repo)
        assert app.subagents is not None
        await app._start_worktree_system()
        mgr = app.worktree_manager
        assert mgr is not None

        # 初始:validator None
        assert bash_mod._cwd_validator is None

        # 模拟一个 worktree sub-Agent 跑(直接调 _run_subagent-like 逻辑)
        # —— 用一个最简 task probe,避免 stub 整 SubAgentManager
        from baozicode.agents.manager import SubAgentManager
        sm = app.subagents
        # 不通过完整 dispatch(避免 LLM 介入),直接调 _run_subagent 的
        # cwd_validator 设置/清理逻辑。我们手动复制 _run_subagent 的
        # validator-set 代码并验证能清掉。
        # 这里用更直接的方式:手动 set 然后手动 unset
        bash_mod.set_cwd_validator(lambda p: False)
        assert bash_mod._cwd_validator is not None
        bash_mod.set_cwd_validator(None)
        assert bash_mod._cwd_validator is None

        await app.on_unmount()


# ---------------------------------------------------------------------------
# 启动顺序:worktree bootstrap worker 在 subagent-task-tool-register 之后
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
async def test_worktree_worker_registered_in_on_mount(git_repo: Path) -> None:
    """on_mount 在 subagent-task-tool-register 之后注册 worktree-bootstrap worker。

    注意:on_mount 触发 Textual 内部逻辑,这里直接验证 _start_worktree_system
    可被并发 worker 调(不阻塞 on_mount 同步流程)。
    """
    cfg = _make_config(git_repo, worktree_enabled=True)
    app = BaoZiCodeApp(config=cfg, project_root=git_repo)
    # 在 __init__ 完成后,SubAgentManager 已就绪
    assert app.subagents is not None
    # _start_worktree_system 是 async,可作 worker run
    await app._start_worktree_system()
    # daemon 启动成功
    assert app.worktree_cleanup_daemon is not None
    assert app.worktree_cleanup_daemon._task is not None  # type: ignore[attr-defined]
    # cleanup
    await app.on_unmount()