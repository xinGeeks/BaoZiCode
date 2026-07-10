"""v1.3 Worktree Isolation — schema + WorktreeManager 测试。

覆盖 `WorktreePathValidator` + `WorktreeSpec` + `WorktreeManager`
的所有 acceptance scenario 在 `openspec/changes/v1-3-
worktree-isolation/specs/worktree-isolation/spec.md`。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from baozicode.worktree import (
    PathValidationError,
    WorktreeCreationFailed,
    WorktreeExistsDirtyError,
    WorktreeExitResult,
    WorktreeManager,
    WorktreeNotInRepoError,
    WorktreeNotFoundError,
    WorktreePathValidator,
    WorktreeSpec,
)
from baozicode.worktree.schema import WorktreeSpec as SpecDirect


# ---------------------------------------------------------------------------
# WorktreePathValidator
# ---------------------------------------------------------------------------


class TestAcceptedNames:
    """Requirement: Accepted names —— 单段、纯字母数字、嵌套。"""

    @pytest.mark.parametrize(
        "name",
        [
            "api-designer",
            "wt_001",
            "phase1/api-designer",
            "a/b/c",
            "phase2/research-agent",
            ".foo",          # 单段字符合法(含 .)
            "foo.bar",       # 中段含 .(单段)
            "x",             # 最短合法
        ],
    )
    def test_accepted(self, name: str) -> None:
        # 不抛
        WorktreePathValidator.validate(name)


class TestRejection:
    """Requirement: rejected —— 路径遍历 / 长度 / 边界 / 字符集 / 类型。"""

    @pytest.mark.parametrize(
        "name,expected_substr",
        [
            ("../../../etc/passwd", ". 或 .."),
            ("foo/../bar", ". 或 .."),
            ("a" * 65, "长度越界"),
            ("", "长度越界"),
            ("/foo", "开头或结尾"),
            ("foo/", "开头或结尾"),
            ("foo//bar", "连续 //"),
            ("./name", ". 或 .."),
            ("foo bar", "字符集超出"),
            ("foo;bar", "字符集超出"),
            ("foo*", "字符集超出"),
            ("foo$bar", "字符集超出"),
            ("foo\\bar", "字符集超出"),
            ("foo|bar", "字符集超出"),
            ("foo`bar", "字符集超出"),
        ],
    )
    def test_reject(self, name: str, expected_substr: str) -> None:
        with pytest.raises(PathValidationError) as exc_info:
            WorktreePathValidator.validate(name)
        assert expected_substr in str(exc_info.value), (
            f"expected {expected_substr!r} in error, got: {exc_info.value}"
        )

    def test_reject_non_string(self) -> None:
        with pytest.raises(PathValidationError) as exc_info:
            WorktreePathValidator.validate(123)
        assert "字符串" in str(exc_info.value)

    def test_reject_none(self) -> None:
        with pytest.raises(PathValidationError):
            WorktreePathValidator.validate(None)


class TestMaxLengthEdge:
    """Requirement: 长度为 64 时合法,65 时拒。"""

    def test_64_accepted(self) -> None:
        WorktreePathValidator.validate("a" * 64)

    def test_65_rejected(self) -> None:
        with pytest.raises(PathValidationError):
            WorktreePathValidator.validate("a" * 65)

    def test_1_accepted(self) -> None:
        WorktreePathValidator.validate("a")


# ---------------------------------------------------------------------------
# WorktreeSpec
# ---------------------------------------------------------------------------


class TestWorktreeSpec:
    """Requirement: WorktreeSpec 字段 + 派生方法。"""

    def test_create_with_absolute_path(self) -> None:
        spec = WorktreeSpec(
            name="api-designer",
            path=Path("/tmp/repo/.worktrees/api-designer"),
            branch="wt/api-designer",
            state="active",
        )
        assert spec.name == "api-designer"
        assert spec.path.is_absolute()
        assert spec.branch == "wt/api-designer"
        assert spec.state == "active"

    def test_relative_path_resolved(self) -> None:
        spec = WorktreeSpec(
            name="x",
            path=Path("relative/.worktrees/x"),
            branch="wt/x",
            state="active",
        )
        assert spec.path.is_absolute()

    def test_with_state_creates_new(self) -> None:
        spec = WorktreeSpec(
            name="a",
            path=Path("/r/.worktrees/a"),
            branch="wt/a",
            state="active",
        )
        detached = spec.with_state("detached")
        assert spec.state == "active"
        assert detached.state == "detached"
        assert detached is not spec
        assert detached.name == spec.name
        assert detached.path == spec.path

    def test_is_active_only_for_active(self) -> None:
        spec = WorktreeSpec(
            name="a",
            path=Path("/r/.worktrees/a"),
            branch="wt/a",
            state="active",
        )
        assert spec.is_active
        for s in ("creating", "detached", "stale", "removed"):
            other = spec.with_state(s)  # type: ignore[arg-type]
            assert not other.is_active

    def test_is_deletable_for_active_detached_stale(self) -> None:
        spec = WorktreeSpec(
            name="a",
            path=Path("/r/.worktrees/a"),
            branch="wt/a",
            state="active",
        )
        for s in ("active", "detached", "stale"):
            assert spec.with_state(s).is_deletable  # type: ignore[arg-type]
        for s in ("creating", "removed"):
            assert not spec.with_state(s).is_deletable  # type: ignore[arg-type]

    def test_frozen_dataclass(self) -> None:
        spec = WorktreeSpec(
            name="a",
            path=Path("/r/.worktrees/a"),
            branch="wt/a",
            state="active",
        )
        with pytest.raises(Exception):
            spec.state = "removed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# WorktreeManager — fixtures + 行为
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> None:
    """同步 git 调用 helper(fixture 用)。"""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败 ({proc.returncode}): "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Init 一个 git repo + 1 commit + user config 完毕。"""
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
def not_repo(tmp_path: Path) -> Path:
    """非 git 目录。"""
    p = tmp_path / "no_repo"
    p.mkdir()
    return p


class TestWorktreeManagerConstruction:
    """Constructor + 拒绝非 git repo."""

    def test_construct_in_git_repo(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        assert mgr.setup_dir == git_repo.resolve()

    def test_reject_non_repo(self, not_repo: Path) -> None:
        with pytest.raises(WorktreeNotInRepoError):
            WorktreeManager(setup_dir=not_repo)

    def test_worktrees_root_path(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        assert mgr.worktrees_root == git_repo.resolve() / ".worktrees"

    def test_spec_for_resolves_separator(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = mgr.spec_for("phase1/api-designer")
        assert spec.name == "phase1/api-designer"
        assert spec.branch == "wt/phase1/api-designer"
        # path 必须包含嵌套
        assert spec.path == (mgr.worktrees_root / "phase1" / "api-designer")

    def test_spec_for_rejects_invalid_name(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        with pytest.raises(PathValidationError):
            mgr.spec_for("../escape")


@pytest.mark.usefixtures("git_repo")
class TestWorktreeManagerCreate:
    """create() 主路径 + fast-path + 拒绝。"""

    @pytest.mark.asyncio
    async def test_happy_create(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("api-designer")
        assert spec.path.exists()
        assert (spec.path / ".git").exists()
        assert (spec.path / "README.md").read_text() == "# test\n"
        assert spec.state == "active"
        assert spec.is_active

    @pytest.mark.asyncio
    async def test_fast_path_takeover(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec1 = await mgr.create("api-designer")
        # 第二次 create 走 fast-path,不动 git worktree add
        spec2 = await mgr.create("api-designer")
        assert spec2.path == spec1.path
        assert spec2.state == "active"

    @pytest.mark.asyncio
    async def test_create_nested_name(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("phase1/api-designer")
        assert spec.path.exists()
        assert spec.branch == "wt/phase1/api-designer"

    @pytest.mark.asyncio
    async def test_create_rejects_invalid_name(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        with pytest.raises(PathValidationError):
            await mgr.create("../escape")

    @pytest.mark.asyncio
    async def test_create_rejects_occupied_dir(
        self, git_repo: Path,
    ) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        target = git_repo / ".worktrees" / "taken"
        target.mkdir(parents=True)
        (target / "junk.txt").write_text("blocked")
        with pytest.raises(WorktreeCreationFailed):
            await mgr.create("taken")


@pytest.mark.usefixtures("git_repo")
class TestWorktreeManagerEnter:
    """enter() fast-path + missing reject。"""

    @pytest.mark.asyncio
    async def test_enter_existing(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        await mgr.create("alpha")
        p = await mgr.enter("alpha")
        assert p.exists()
        assert (p / "README.md").exists()

    @pytest.mark.asyncio
    async def test_enter_missing_rejected(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        with pytest.raises(WorktreeNotFoundError):
            await mgr.enter("nonexistent")


@pytest.mark.usefixtures("git_repo")
class TestWorktreeManagerExit:
    """exit() 决策树。"""

    @pytest.mark.asyncio
    async def test_clean_remove(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("clean")
        result = await mgr.exit("clean")
        assert result.state == "removed"
        assert result.reason == "clean"
        assert result.removed
        assert not result.preserved
        assert not spec.path.exists()

    @pytest.mark.asyncio
    async def test_dirty_preserves_detached(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("dirty")
        (spec.path / "uncommitted.txt").write_text("pending")
        result = await mgr.exit("dirty")
        assert result.state == "detached"
        assert result.reason == "uncommitted_changes"
        assert result.preserved
        # 保留路径
        assert spec.path.exists()
        # 清理 force
        await mgr.remove("dirty", force=True)
        assert not spec.path.exists()

    @pytest.mark.asyncio
    async def test_local_commits_no_upstream_removed(
        self, git_repo: Path,
    ) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("local")
        (spec.path / "a.txt").write_text("hi")
        _git(spec.path, "add", ".")
        _git(spec.path, "commit", "-m", "local commit")
        # branch 没 upstream → git 视为不需要推送
        result = await mgr.exit("local")
        assert result.state == "removed"
        assert result.reason == "clean"

    @pytest.mark.asyncio
    async def test_force_removes_dirty(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("forced")
        (spec.path / "x.txt").write_text("data")
        result = await mgr.exit("forced", force=True)
        assert result.state == "removed"
        assert result.reason == "force"
        assert not spec.path.exists()

    @pytest.mark.asyncio
    async def test_exit_on_missing_raises(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        with pytest.raises(WorktreeNotFoundError):
            await mgr.exit("never-existed", force=True)


@pytest.mark.usefixtures("git_repo")
class TestWorktreeManagerQueries:
    """exists / list_active / remove。"""

    @pytest.mark.asyncio
    async def test_exists_true_for_active(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        await mgr.create("one")
        assert await mgr.exists("one")
        assert not await mgr.exists("two")

    @pytest.mark.asyncio
    async def test_list_active(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        await mgr.create("a")
        await mgr.create("nested/b")
        active = await mgr.list_active()
        names = sorted(p.name for p in active)
        assert "a" in names
        assert "b" in names  # nested 的 leaf 目录名

    @pytest.mark.asyncio
    async def test_remove_idempotent_on_missing(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        # 不存在的 remove 不抛
        await mgr.remove("nonexistent")

    @pytest.mark.asyncio
    async def test_remove_force_clean(self, git_repo: Path) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("rm")
        await mgr.remove("rm", force=True)
        assert not spec.path.exists()


# ---------------------------------------------------------------------------
# 公开 API 表面
# ---------------------------------------------------------------------------


class TestPublicAPI:
    """公开 API re-export + 类型关系。"""

    def test_top_level_imports(self) -> None:
        from baozicode.worktree import (
            PathValidationError,
            WorktreeCreationFailed,
            WorktreeExitResult,
            WorktreeExistsDirtyError,
            WorktreeManager,
            WorktreeNotInRepoError,
            WorktreeNotFoundError,
            WorktreePathValidator,
            WorktreeSpec,
            WorktreeState,
        )
        assert PathValidationError is not None
        assert WorktreeCreationFailed is not None
        assert WorktreeExistsDirtyError is not None
        assert WorktreeNotInRepoError is not None
        assert WorktreeNotFoundError is not None
        assert WorktreePathValidator is not None
        assert WorktreeSpec is not None
        assert WorktreeManager is not None
        assert WorktreeExitResult is not None
        assert WorktreeState is not None

    def test_worktree_exists_dirty_is_subclass(self) -> None:
        assert issubclass(WorktreeExistsDirtyError, WorktreeCreationFailed)

    def test_path_validation_is_value_error(self) -> None:
        assert issubclass(PathValidationError, ValueError)

    def test_spec_reexport_matches_direct(self) -> None:
        assert WorktreeSpec is SpecDirect


# ---------------------------------------------------------------------------
# integration sanity — asyncio.run 走通最小 e2e
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestIntegrationMinimal:
    """end-to-end smoke —— create + dirty + exit detached + force clean。"""

    @pytest.mark.asyncio
    async def test_e2e_dirty_preserve_then_force(
        self, git_repo: Path,
    ) -> None:
        mgr = WorktreeManager(setup_dir=git_repo)
        spec = await mgr.create("e2e")

        # 在 worktree 内写文件,验证物理隔离(主 repo 不受影响)
        (spec.path / "scratch.txt").write_text("from sub-agent")
        assert not (git_repo / "scratch.txt").exists()

        # dirty → detached
        result = await mgr.exit("e2e")
        assert result.state == "detached"

        # 路径仍在,后续可 force 清
        await mgr.remove("e2e", force=True)
        assert not spec.path.exists()
