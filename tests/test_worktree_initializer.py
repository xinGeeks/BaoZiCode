"""v1.3 Worktree Isolation — `WorktreeInitializer` 4 步环境初始化测试。

覆盖 `openspec/changes/v1-3-worktree-isolation/specs/worktree-isolation/
spec.md:WorktreeInitializer 4-step setup` 的 acceptance scenario。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from baozicode.worktree import WorktreeInitConfig, WorktreeInitializer


# ---------------------------------------------------------------------------
# Fixtures
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
    """Init git repo + 1 commit + user config 完毕。"""
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


@pytest.fixture()
def worktree_path(git_repo: Path) -> Path:
    """模拟一个 worktree 目录(纯文件系统,不动 git worktree)。

    Initializer 不要求 worktree 是 git worktree 注册过的 —— 它只看
    路径是否存在。`_step_hooks` 调 `git config` 需要 worktree 内有
    `.git` 文件 / 目录。我们用 `git worktree add` 构造真实 worktree,
    其他步骤测纯文件系统行为。
    """
    # 真造一个 worktree(`git worktree add` 比 init 独立目录更接近
    # 生产语义)
    wt = git_repo / ".worktrees" / "test"
    _git(git_repo, "worktree", "add", "-b", "wt/test", str(wt), "HEAD")
    return wt


# ---------------------------------------------------------------------------
# 默认配置 + API 形状
# ---------------------------------------------------------------------------


class TestWorktreeInitConfig:
    """`WorktreeInitConfig` 字段 + 默认值。"""

    def test_defaults(self) -> None:
        cfg = WorktreeInitConfig()
        assert ".venv" in cfg.link_paths
        assert ".baozicode/BaoZiCode.md" in cfg.copy_paths
        assert cfg.hooks_relpath == "../_hooks/"
        assert cfg.gitignore_pattern == r"^\.worktrees/?$"

    def test_frozen(self) -> None:
        cfg = WorktreeInitConfig()
        with pytest.raises(Exception):
            cfg.link_paths = []  # type: ignore[misc]

    def test_custom(self) -> None:
        cfg = WorktreeInitConfig(
            link_paths=[".venv"],
            copy_paths=[".env"],
        )
        assert cfg.link_paths == [".venv"]
        assert cfg.copy_paths == [".env"]


# ---------------------------------------------------------------------------
# Step 1: link
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo", "worktree_path")
class TestLinkStep:
    """symlink 创建 + 跨盘符优雅跳过 + 幂等。"""

    @pytest.mark.asyncio
    async def test_link_happy(self, git_repo: Path, worktree_path: Path) -> None:
        # 源存在
        venv = git_repo / ".venv"
        venv.mkdir()
        (venv / "bin").mkdir()
        (venv / "bin" / "python").write_text("fake")

        cfg = WorktreeInitConfig(link_paths=[".venv"])
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        link = worktree_path / ".venv"
        assert link.exists() or link.is_symlink()
        # POSIX symlink 或 Windows junction 至少 link-like
        if os.name != "nt":
            assert link.is_symlink()
        # 软链读到原文件
        assert (link / "bin" / "python").read_text() == "fake"

    @pytest.mark.asyncio
    async def test_link_skips_missing_source(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        # 源不存在 → 不抛,不建
        cfg = WorktreeInitConfig(link_paths=[".venv", "node_modules"])
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)
        assert not (worktree_path / ".venv").exists()
        assert not (worktree_path / "node_modules").exists()

    @pytest.mark.asyncio
    async def test_link_idempotent(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        venv = git_repo / ".venv"
        venv.mkdir()
        cfg = WorktreeInitConfig(link_paths=[".venv"])

        await WorktreeInitializer.run(worktree_path, git_repo, cfg)
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        # 第二次跑仍存在(没被覆盖)
        assert (worktree_path / ".venv").exists()


# ---------------------------------------------------------------------------
# Step 2: copy
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo", "worktree_path")
class TestCopyStep:
    """`copy_paths` 复制 + 失败跳过 + 幂等。"""

    @pytest.mark.asyncio
    async def test_copy_file(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        env = git_repo / ".env"
        env.write_text("API_KEY=secret\n")

        cfg = WorktreeInitConfig(copy_paths=[".env"])
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        assert (worktree_path / ".env").read_text() == "API_KEY=secret\n"

    @pytest.mark.asyncio
    async def test_copy_directory(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        # 源是目录
        claude = git_repo / ".claude"
        claude.mkdir()
        (claude / "settings.json").write_text("{}")

        cfg = WorktreeInitConfig(copy_paths=[".claude/"])
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        dst = worktree_path / ".claude/"
        assert dst.is_dir()
        assert (dst / "settings.json").read_text() == "{}"

    @pytest.mark.asyncio
    async def test_copy_overwrites_idempotent(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        env = git_repo / ".env"
        env.write_text("v1")

        cfg = WorktreeInitConfig(copy_paths=[".env"])
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        # 改源,再跑 → worktree 内文件也更新(覆盖)
        env.write_text("v2")
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)
        assert (worktree_path / ".env").read_text() == "v2"

    @pytest.mark.asyncio
    async def test_copy_skips_missing_source(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        cfg = WorktreeInitConfig(copy_paths=[".env", "config.yaml"])
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)
        # 不存在 → 不建,也不抛
        assert not (worktree_path / ".env").exists()


# ---------------------------------------------------------------------------
# Step 3: hooks
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo", "worktree_path")
class TestHooksStep:
    """`git config core.hooksPath` + 共享 `_hooks/` 目录创建。"""

    @pytest.mark.asyncio
    async def test_hooks_path_set_relative(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        cfg = WorktreeInitConfig()  # 默认 ../_hooks/
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        # 读 worktree 内的 core.hooksPath
        rc = subprocess.run(
            ["git", "config", "core.hooksPath"],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
        )
        assert rc.returncode == 0, rc.stderr
        configured = rc.stdout.strip()
        # 相对路径(从 worktree 出发)
        assert not Path(configured).is_absolute(), (
            f"hooksPath 必须是相对路径,得到 {configured!r}"
        )
        # 应该解析到 .worktrees/_hooks/
        resolved = (worktree_path / configured).resolve()
        assert resolved == (git_repo / ".worktrees" / "_hooks").resolve()
        # 目录存在
        assert resolved.is_dir()


# ---------------------------------------------------------------------------
# Step 4: gitignore
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo", "worktree_path")
class TestGitignoreStep:
    """`.gitignore` 自动加 `.worktrees/` 行(幂等)。"""

    @pytest.mark.asyncio
    async def test_gitignore_appends_when_missing(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        # 初始 .gitignore 没有 .worktrees/
        gi = git_repo / ".gitignore"
        gi.write_text("__pycache__/\n*.pyc\n")

        cfg = WorktreeInitConfig()
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        text = gi.read_text()
        # 原行保留
        assert "__pycache__/" in text
        assert "*.pyc" in text
        # 新行加上
        assert re.search(r"^\.worktrees/?$", text, re.MULTILINE)

    @pytest.mark.asyncio
    async def test_gitignore_idempotent(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        gi = git_repo / ".gitignore"
        gi.write_text(".worktrees/\n__pycache__/\n")

        cfg = WorktreeInitConfig()
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        text = gi.read_text()
        # 只有 1 行 .worktrees/
        matches = re.findall(r"^\.worktrees/?$", text, re.MULTILINE)
        assert len(matches) == 1

    @pytest.mark.asyncio
    async def test_gitignore_creates_when_missing(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        gi = git_repo / ".gitignore"
        assert not gi.exists()

        cfg = WorktreeInitConfig()
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        assert gi.exists()
        text = gi.read_text()
        assert re.search(r"^\.worktrees/?$", text, re.MULTILINE)

    @pytest.mark.asyncio
    async def test_gitignore_matches_variants(
        self, git_repo: Path, worktree_path: Path,
    ) -> None:
        # `.worktrees` 不带斜杠也算匹配(不重复 append)
        gi = git_repo / ".gitignore"
        gi.write_text(".worktrees\n")

        cfg = WorktreeInitConfig()
        await WorktreeInitializer.run(worktree_path, git_repo, cfg)

        text = gi.read_text()
        matches = re.findall(r"^\.worktrees/?$", text, re.MULTILINE)
        assert len(matches) == 1