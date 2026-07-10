"""v1.3 Worktree Isolation — WorktreeManager(生命周期编排)。

公开 API:

- `WorktreeManager(setup_dir, *, max_concurrent=5)` —— 构造时校验
  `setup_dir` 是 git repo,否则抛 `WorktreeNotInRepoError`
- `async WorktreeManager.create(name) -> WorktreeSpec` —— 主入口;
  自动走 fast-path(目录已存在健康时)或 normal-path(`git worktree
  add -b wt/<name> ...`)
- `async WorktreeManager.enter(name) -> Path` —— 已存在 worktree 的
  路径(fast-path)
- `async WorktreeManager.exit(name, *, force=False) -> ExitResult`
  —— 决策树:未提交/未推送 → 保留 detached;都干净 → 删
- `async WorktreeManager.remove(name, *, force=False) -> None` ——
  强制物理删除(已经是 exit + force 的等价别名,但 remove 不维护状态
  机 —— 直接走 git 操作)
- `async WorktreeManager.list_active() -> list[WorktreeSpec]` ——
  当前 active 集合(TUI status 用)
- `async WorktreeManager.exists(name) -> bool` —— active + 物理
  存在

私有辅助:

- `_git_subprocess(*args, cwd=None)` —— 调 `git` 命令,统一超时(15s
  默认)
- `_try_fast_path(name, worktree_dir)` —— 3 步健康判定(D7)
- `_spawn_subprocess(...)` —— 同 `_git_subprocess` 别名(后续
  cleanup 也用)

设计依赖:

- `WorktreePathValidator.validate(name)` —— 在 `create` 第一行就调,
  失败快速失败(LLM 输入直传)
- `WorktreeInitializer.run(worktree_spec.path, setup_dir, config)` ——
  由调用方在 `create` 成功后调;`manager.create` **不**自动调(隔离关
  注点,让 spec 决策 in caller path)
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .schema import (
    PathValidationError,
    WorktreeCreationFailed,
    WorktreeExistsDirtyError,
    WorktreeNotInRepoError,
    WorktreeNotFoundError,
    WorktreePathValidator,
    WorktreeSpec,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 退出决策结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeExitResult:
    """`WorktreeManager.exit` 的返回。

    字段:
    - `state`:退出后 worktree 的新状态(`detached` 或 `removed`)
    - `reason`:为什么是这个决策(供上层 / TUI 提示用户)
    - `path`:退出后路径的**当前**状态。`removed` 时路径已不存在但
      我们仍记原 path 方便 caller log + UI 提示
    """

    state: Literal["detached", "removed"]
    reason: Literal[
        "clean",                # 都干净 → removed
        "uncommitted_changes",  # dirty 工作区 → detached
        "unpushed_commits",     # 有未推送 commit → detached
        "force",                # force=True → removed
        "force_detached",       # force=True 但 worktree 不存在 → 占位,不会发生
    ]
    path: Path

    @property
    def removed(self) -> bool:
        return self.state == "removed"

    @property
    def preserved(self) -> bool:
        """`True` iff worktree 保留(detached)。"""
        return self.state == "detached"


# ---------------------------------------------------------------------------
# WorktreeManager
# ---------------------------------------------------------------------------


_DEFAULT_GIT_TIMEOUT = 15.0  # 秒,git 命令超时不阻塞事件循环
_MAX_CONCURRENT_DEFAULT = 5


class WorktreeManager:
    """git worktree 生命周期编排器。

    用法:
        mgr = WorktreeManager(setup_dir=Path("/repo"), max_concurrent=5)
        spec = await mgr.create("api-designer")  # fast-path or normal-path
        # 主 Agent / sub-Agent 在 spec.path 干活
        result = await mgr.exit("api-designer")  # 决策树
    """

    def __init__(
        self,
        *,
        setup_dir: Path,
        max_concurrent: int = _MAX_CONCURRENT_DEFAULT,
    ) -> None:
        self._setup_dir = setup_dir.resolve()
        self._max_concurrent = max_concurrent
        # 构造期 sync 校验(避免延迟失败)
        if not self._is_git_repo(self._setup_dir):
            raise WorktreeNotInRepoError(
                f"setup_dir 不是 git repo: {self._setup_dir}"
            )

    # ---- 公开 API ----

    @property
    def setup_dir(self) -> Path:
        return self._setup_dir

    @property
    def worktrees_root(self) -> Path:
        """`<setup_dir>/.worktrees/` 主目录。"""
        return self._setup_dir / ".worktrees"

    def spec_for(self, name: str) -> WorktreeSpec:
        """派生 `WorktreeSpec` —— 仅由 `create` / `enter` / manager 内
        部使用。供 TUI `list_active` 后构造 spec 给上层。
        """
        WorktreePathValidator.validate(name)
        return WorktreeSpec(
            name=name,
            path=self.worktrees_root / name.replace("/", self._sep_for_branch()),
            branch=self._branch_name_for(name),
            state="active",
        )

    async def create(self, name: str) -> WorktreeSpec:
        """创建 worktree。

        Args:
            name: 已通过 validator 的合法名(本函数也会调一次作防御)

        Returns:
            `WorktreeSpec(state="active")`

        Raises:
            PathValidationError: name 非法
            WorktreeExistsDirtyError: fast-path 探测到 dirty / 目录非空
            WorktreeCreationFailed: `git worktree add` 失败
        """
        WorktreePathValidator.validate(name)
        spec = self.spec_for(name)
        worktree_dir = spec.path
        self._ensure_worktrees_root()

        # ---- fast-path ----
        fast = await self._try_fast_path(name, worktree_dir)
        if fast is not None:
            log.debug("worktree fast-path 接管: %s", name)
            return fast

        # ---- normal-path:git worktree add ----
        # 先清理可能残留的 wt/<name> 分支(上一轮不干净失败遗留)
        branch = spec.branch
        await self._git_subprocess(
            "branch", "-D", branch, cwd=self._setup_dir, check=False
        )

        try:
            await self._git_subprocess(
                "worktree", "add", "-b", branch,
                str(worktree_dir), "HEAD",
                cwd=self._setup_dir,
            )
        except _GitCommandError as exc:
            stderr = exc.stderr or ""
            # 目录非空 / 已有 .git file → 报 dirty(用户没清干净)
            if "already exists" in stderr or "is not empty" in stderr:
                raise WorktreeExistsDirtyError(
                    f"fast-path 失败后 normal-path 也失败: worktree 目录已存在"
                    f"且非空({worktree_dir});无法接管"
                ) from exc
            raise WorktreeCreationFailed(
                f"git worktree add 失败: {stderr.strip() or exc}"
            ) from exc

        log.debug("worktree 创建: %s at %s", name, worktree_dir)
        return spec

    async def enter(self, name: str) -> Path:
        """进入已存在的 worktree。仅 fast-path,不走 normal-path。

        Raises:
            PathValidationError: name 非法
            WorktreeNotFoundError: fast-path 失败(目录不健康)
        """
        WorktreePathValidator.validate(name)
        spec = self.spec_for(name)
        fast = await self._try_fast_path(name, spec.path)
        if fast is None:
            raise WorktreeNotFoundError(
                f"worktree 不存在或不健康: {name} ({spec.path})"
            )
        return fast.path

    async def exit(self, name: str, *, force: bool = False) -> WorktreeExitResult:
        """退出 worktree,按决策树决定删 / 保留。

        Raises:
            PathValidationError: name 非法
            WorktreeNotFoundError: worktree 完全不存在
        """
        WorktreePathValidator.validate(name)
        spec = self.spec_for(name)
        if not spec.path.exists():
            raise WorktreeNotFoundError(f"worktree 不存在: {name}")

        if force:
            await self._do_remove(spec, force=True)
            return WorktreeExitResult(
                state="removed", reason="force", path=spec.path,
            )

        # ---- 检测 1:未提交修改 ----
        dirty = await self._is_dirty(spec.path)
        if dirty:
            log.info("worktree dirty,保留 detached: %s", name)
            return WorktreeExitResult(
                state="detached",
                reason="uncommitted_changes",
                path=spec.path,
            )

        # ---- 检测 2:未推送 commit ----
        if await self._has_upstream(spec.path):
            unpushed = await self._has_unpushed_commits(spec.path)
            if unpushed:
                log.info("worktree 有未推送 commit,保留 detached: %s", name)
                return WorktreeExitResult(
                    state="detached",
                    reason="unpushed_commits",
                    path=spec.path,
                )

        # ---- 都干净,真删 ----
        await self._do_remove(spec)
        return WorktreeExitResult(
            state="removed", reason="clean", path=spec.path,
        )

    async def remove(self, name: str, *, force: bool = False) -> None:
        """强制物理删除 —— 不管状态机。

        `force=False` 调 `git worktree remove`(可能拒 dirty);
        `force=True` 调 `git worktree remove --force`。
        """
        WorktreePathValidator.validate(name)
        spec = self.spec_for(name)
        if not spec.path.exists():
            return
        await self._do_remove(spec, force=force)

    async def exists(self, name: str) -> bool:
        """检查 name 对应 worktree 物理存在(不校验健康)。"""
        WorktreePathValidator.validate(name)
        spec = self.spec_for(name)
        return spec.path.exists() and (spec.path / ".git").exists()

    async def list_active(self) -> list[Path]:
        """列出所有 active worktree 路径(供 TUI status bar)。

        递归遍历 `<root>/**/*`,筛 `.git` file 在的目录 —— 这样
        嵌套名(`phase1/api-designer`)的 leaf 也被找到。
        """
        root = self.worktrees_root
        if not root.exists():
            return []
        seen: set[Path] = set()
        for p in root.rglob("*"):
            if not p.is_dir():
                continue
            if not (p / ".git").is_file():
                continue
            resolved = p.resolve()
            if resolved not in seen:
                seen.add(resolved)
        return sorted(seen)

    def iter_worktrees(self) -> list[tuple[str, Path]]:
        """列出所有现存 worktree 的 `(name, path)` 对。

        `name` 是相对 `<root>` 的路径(`/a/b/c` 风格);daemon 用它
        跟 task 名 / spec 对齐。同步 helper(只 fs stat,不调 git)。
        """
        root = self.worktrees_root
        if not root.exists():
            return []
        out: list[tuple[str, Path]] = []
        for p in root.rglob("*"):
            if not p.is_dir():
                continue
            if not (p / ".git").is_file():
                continue
            try:
                rel = p.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            # name 统一用 `/`(validator 用 `/` 分隔)
            name = rel.as_posix()
            out.append((name, p.resolve()))
        return sorted(out, key=lambda x: x[0])

    # ---- 私有:git 调用 ----

    async def _git_subprocess(
        self,
        *args: str,
        cwd: Path,
        timeout: float = _DEFAULT_GIT_TIMEOUT,
        check: bool = True,
    ) -> str:
        """统一的 git 子进程调用。

        Returns:
            stdout 解码(str);失败抛 `_GitCommandError`
        """
        git_path = shutil.which("git")
        if not git_path:
            raise RuntimeError("git 不在 PATH;WorktreeManager 需要 git 可执行")

        proc = await asyncio.create_subprocess_exec(
            git_path, *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except OSError:
                pass
            raise RuntimeError(f"git {' '.join(args)} 超时 {timeout}s")

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if proc.returncode != 0 and check:
            raise _GitCommandError(
                args=args,
                returncode=proc.returncode or -1,
                stdout=stdout,
                stderr=stderr,
            )
        return stdout

    def _spawn_subprocess(self, *args: str, cwd: Path) -> "asyncio.Future[str]":
        """同步入口 helper —— 跟 `_git_subprocess` 同功能但不在 await
        上下文使用(留给 cleanup 用)。"""
        import asyncio as _aio
        return _aio.ensure_future(self._git_subprocess(*args, cwd=cwd))

    # ---- 私有:路径 / 分支名 ----

    @staticmethod
    def _sep_for_branch() -> str:
        # 嵌套 named `phase1/api-designer` 时,文件路径用 OS sep('/' on POSIX,
        # '\\' on Windows),分支名我们用 'wt/phase1/api-designer'。`spec_for`
        # 里 path 用 filesystem sep。
        import os
        return os.sep

    @staticmethod
    def _branch_name_for(name: str) -> str:
        """分支名 —— 嵌套 name 时用 `/` 分隔(git 允许分支名有 `/`,
        显示为 `wt/phase1/api-designer`)。"""
        # 注意:git branch 不允许 `.lock` 或 `..` 等特殊结尾;但我们的
        # validator 已经拒了 `..` 段,且字符集合法。分支最末段需避
        # 免 `.lock` 等,这里宽松处理(全名都安全)。
        return f"wt/{name}"

    def _ensure_worktrees_root(self) -> None:
        """确保 `.worktrees/` 主目录存在。"""
        root = self.worktrees_root
        root.mkdir(parents=True, exist_ok=True)

    # ---- 私有:fast-path 健康判定 ----

    async def _try_fast_path(
        self, name: str, worktree_dir: Path,
    ) -> WorktreeSpec | None:
        """3 步健康判定(D7):
        1. 目录存在
        2. 含 `.git` file
        3. `git worktree list --porcelain` 能看到
        4. `git status --porcelain` 为空

        全部满足 → 返回 spec(state="active");任何一步不过 → None
        """
        if not worktree_dir.is_dir():
            return None
        git_file = worktree_dir / ".git"
        if not git_file.is_file():
            return None

        # git worktree list --porcelain
        try:
            out = await self._git_subprocess(
                "worktree", "list", "--porcelain",
                cwd=self._setup_dir, check=True,
            )
        except _GitCommandError:
            return None

        # worktree 行格式:`worktree /abs/path`,路径匹配用 Path.normalize
        # 处理 Windows 上 git 输出 forward-slash vs Path 后端 backslash
        # 的差异。
        listed = False
        for line in out.splitlines():
            if line.startswith("worktree "):
                git_path = Path(line[len("worktree "):])
                if git_path.resolve() == worktree_dir.resolve():
                    listed = True
                    break
        if not listed:
            return None

        # 不脏就 fast-path
        try:
            status_out = await self._git_subprocess(
                "status", "--porcelain",
                cwd=worktree_dir, check=True,
            )
        except _GitCommandError:
            return None
        if status_out.strip():
            return None  # dirty → fallback normal-path

        return self.spec_for(name).with_state("active")

    # ---- 私有:检测函数 ----

    async def _is_dirty(self, worktree_dir: Path) -> bool:
        """`git status --porcelain --untracked-files=all` 非空 → True。"""
        try:
            out = await self._git_subprocess(
                "status", "--porcelain", "--untracked-files=all",
                cwd=worktree_dir, check=True,
            )
        except _GitCommandError:
            return True  # git 异常时保守视为 dirty
        return bool(out.strip())

    async def _has_upstream(self, worktree_dir: Path) -> bool:
        """`git rev-parse --verify --quiet @{u}` 返回 0 → True。"""
        try:
            await self._git_subprocess(
                "rev-parse", "--verify", "--quiet", "@{u}",
                cwd=worktree_dir, check=True,
            )
            return True
        except _GitCommandError:
            return False

    async def _has_unpushed_commits(self, worktree_dir: Path) -> bool:
        """`git log @{u}..HEAD` 非空 → True。"""
        try:
            out = await self._git_subprocess(
                "log", "@{u}..HEAD", "--oneline",
                cwd=worktree_dir, check=True,
            )
        except _GitCommandError:
            return False
        return bool(out.strip())

    # ---- 私有:物理删除 ----

    async def _do_remove(
        self, spec: WorktreeSpec, *, force: bool = False,
    ) -> None:
        """实际跑 `git worktree remove` + `git branch -d|D`。"""
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(spec.path))
        await self._git_subprocess(*args, cwd=self._setup_dir)

        # 然后删分支(默认 -d,只在 merged 时成功;显式 force 用 -D)
        # 注意:_do_remove(force=True) 时分支可能没 merged → 用 -D
        branch_args = ["branch", "-D" if force else "-d", spec.branch]
        await self._git_subprocess(
            *branch_args, cwd=self._setup_dir, check=False
        )

    # ---- 私有:git repo 检查 ----

    @staticmethod
    def _is_git_repo(setup_dir: Path) -> bool:
        """同步检查 `setup_dir` 是否是 git repo(top-level)。

        Windows 注意:`git rev-parse --show-toplevel` 在 Windows 上返
        回 forward-slash 风格(`C:/...`),而 `Path.resolve()` 返回
        backslash 风格(`C:\\...`)。normalize 后比较。
        """
        try:
            import subprocess

            proc = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(setup_dir),
                capture_output=True,
                timeout=5,
            )
            if proc.returncode != 0:
                return False
            git_says = Path(proc.stdout.decode().strip())
            return git_says.resolve() == setup_dir.resolve()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False


# ---------------------------------------------------------------------------
# 内部异常(跨方法传递 git 失败)
# ---------------------------------------------------------------------------


class _GitCommandError(Exception):
    """git 子进程失败 —— 跨方法传递 stdout / stderr。

    不用 `@dataclass` 是因为 `Exception` 已有 `args` 属性,会跟
    dataclass field 名字冲突。
    """

    def __init__(
        self,
        args: tuple,
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.args_git = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"git {' '.join(args)} 失败 (exit {returncode}): "
            f"{(stderr or stdout).strip()}"
        )


__all__ = [
    "WorktreeExitResult",
    "WorktreeManager",
]
