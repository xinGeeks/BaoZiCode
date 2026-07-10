"""v1.3 Worktree Isolation — `WorktreeInitializer` (4 步环境初始化)。

公开 API:

- `WorktreeInitConfig` —— frozen dataclass,描述 init 需要的白名单 +
  hooks 相对路径
- `WorktreeInitializer.run(worktree_path, setup_dir, config)` —— 主
  入口,4 步串联:link → copy → hooks → gitignore

设计目标:

- **不阻断** — 任一步失败只 warn,LLM 工作流不中断(D6)
- **幂等** — 重复跑效果一致(覆盖 / 不 append 第二遍)
- **跨平台** — POSIX `os.symlink`;Windows 上 `os.symlink` 失败时
  fallback `cmd /c mklink /J`;跨盘符 junction 失败时 warn + skip

步骤细节见 `worktree-isolation/spec.md:WorktreeInitializer 4-step
setup`。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Init 配置(frozen dataclass;AppConfig.worktree 由 Pydantic 提供同样字段)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeInitConfig:
    """Initializer 需要的配置 —— 与 `WorktreeConfig`(config/schema)字段
    对齐,但**不**依赖 Pydantic,便于单元测试独立 mock。"""

    link_paths: list[str] = field(
        default_factory=lambda: [".venv", "node_modules", ".cargo"],
    )
    copy_paths: list[str] = field(
        default_factory=lambda: [
            ".baozicode/BaoZiCode.md",
            ".env",
            "config.yaml",
            ".claude/",
        ],
    )
    hooks_relpath: str = "../_hooks/"  # 从 worktree 内的相对路径
    gitignore_pattern: str = r"^\.worktrees/?$"


# ---------------------------------------------------------------------------
# Initializer
# ---------------------------------------------------------------------------


class WorktreeInitializer:
    """4 步初始化 worktree 的环境(链接 / 复制 / hooks / gitignore)。

    用法:

        cfg = WorktreeInitConfig()
        await WorktreeInitializer.run(worktree_path, setup_dir, cfg)

    注:本类无状态,`run` 是静态入口;写成 class 是为后续扩展(如
    `dry_run=True`)留接口位置。
    """

    @staticmethod
    async def run(
        worktree_path: Path,
        setup_dir: Path,
        config: WorktreeInitConfig,
    ) -> None:
        """主入口 — 4 步串联;任一步失败只 warn。"""
        worktree_path = worktree_path.resolve()
        setup_dir = setup_dir.resolve()
        if not worktree_path.is_dir():
            log.warning(
                "WorktreeInitializer: worktree 路径不存在,跳过全部 4 步: %s",
                worktree_path,
            )
            return

        WorktreeInitializer._step_link(worktree_path, setup_dir, config)
        WorktreeInitializer._step_copy(worktree_path, setup_dir, config)
        WorktreeInitializer._step_hooks(worktree_path, setup_dir, config)
        WorktreeInitializer._step_gitignore(setup_dir, config)

    # ---- step 1: link ----

    @staticmethod
    def _step_link(
        worktree_path: Path, setup_dir: Path, config: WorktreeInitConfig,
    ) -> None:
        """对 `link_paths` 每项在 worktree 内建 symlink(指向 setup_dir 同
        名路径)。失败 warn + skip,不阻断。"""
        for rel in config.link_paths:
            src = setup_dir / rel
            dst = worktree_path / rel
            if not src.exists():
                log.info(
                    "WorktreeInitializer.link: 源不存在,跳过 %s",
                    rel,
                )
                continue
            if dst.exists() or dst.is_symlink():
                # 幂等:已链过(可能上次残留)
                continue
            try:
                WorktreeInitializer._create_symlink(src, dst)
                log.debug("WorktreeInitializer.link: %s -> %s", dst, src)
            except _CrossDriveError:
                log.warning(
                    "WorktreeInitializer.link: 跨盘符,无法 junction: %s -> %s",
                    dst, src,
                )
            except OSError as exc:
                log.warning(
                    "WorktreeInitializer.link: 失败(%s): %s -> %s",
                    exc, dst, src,
                )

    @staticmethod
    def _create_symlink(src: Path, dst: Path) -> None:
        """POSIX:`os.symlink`。Windows:先 `os.symlink` 失败 fallback
        `cmd /c mklink /J`(junction,不需要 admin / Developer Mode);跨
        盘符时 junction 也失败 → 抛 `_CrossDriveError`。"""
        # POSIX 一把过
        try:
            os.symlink(src, dst, target_is_directory=True)
            return
        except OSError as exc:
            if os.name != "nt":
                raise

        # Windows:`os.symlink` 失败常见原因(权限、Developer Mode 未开)
        # fallback junction。junction **不能**跨盘符。
        src_drive, _ = os.path.splitdrive(str(src.resolve()))
        dst_drive, _ = os.path.splitdrive(str(dst.resolve()))
        if src_drive and dst_drive and src_drive != dst_drive:
            raise _CrossDriveError(
                f"junction 跨盘符失败: src={src} dst={dst}"
            )

        # junction:`mklink /J <link> <target>`
        rc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dst), str(src)],
            capture_output=True,
            text=True,
        )
        if rc.returncode != 0:
            raise OSError(
                f"mklink /J 失败 (rc={rc.returncode}): {rc.stderr.strip()}"
            )

    # ---- step 2: copy ----

    @staticmethod
    def _step_copy(
        worktree_path: Path, setup_dir: Path, config: WorktreeInitConfig,
    ) -> None:
        """对 `copy_paths` 每项用 `shutil.copytree(dirs_exist_ok=True)` 复制
        到 worktree 内。失败 warn + skip。"""
        for rel in config.copy_paths:
            src = setup_dir / rel
            dst = worktree_path / rel
            if not src.exists():
                log.info(
                    "WorktreeInitializer.copy: 源不存在,跳过 %s", rel,
                )
                continue
            try:
                if src.is_dir():
                    shutil.copytree(
                        src, dst, dirs_exist_ok=True,
                    )
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                log.debug("WorktreeInitializer.copy: %s <- %s", dst, src)
            except OSError as exc:
                log.warning(
                    "WorktreeInitializer.copy: 失败(%s): %s <- %s",
                    exc, dst, src,
                )

    # ---- step 3: hooks ----

    @staticmethod
    def _step_hooks(
        worktree_path: Path, setup_dir: Path, config: WorktreeInitConfig,
    ) -> None:
        """在 worktree 内 `git config core.hooksPath <relative hooks>` + 自动
        建共享 `_hooks/` 空目录。"""
        # hooks 路径从 worktree 内出发;默认是 `../_hooks/` 即
        # `<setup_dir>/.worktrees/_hooks/`(所有 worktree 共享)
        hooks_rel = config.hooks_relpath
        # worktree_path = setup_dir/.worktrees/<name>;父目录是
        # .worktrees/。如果 hooks_relpath 以 ../ 开头,解析到 setup_dir
        # 这一级。
        if hooks_rel.startswith("../"):
            # ../_hooks/ → setup_dir/.worktrees/_hooks/
            hooks_dir = (worktree_path / hooks_rel).resolve()
        else:
            hooks_dir = (worktree_path / hooks_rel).resolve()

        try:
            hooks_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning(
                "WorktreeInitializer.hooks: 创建 %s 失败(%s)",
                hooks_dir, exc,
            )
            return

        # 设 git config。注意:必须传**相对路径**(跟 worktree 相关)
        # 让 hooks 在 worktree 移动时仍有效。
        # 如果 hooks_dir 在 worktree 外(我们的 default 是),用
        # worktree_path 到 hooks_dir 的相对路径。
        try:
            rel_from_wt = os.path.relpath(hooks_dir, worktree_path)
        except ValueError:
            # 不同盘符 on Windows → relpath 失败;fallback 用绝对
            rel_from_wt = str(hooks_dir)

        rc = subprocess.run(
            ["git", "config", "core.hooksPath", rel_from_wt],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
        )
        if rc.returncode != 0:
            log.warning(
                "WorktreeInitializer.hooks: git config core.hooksPath "
                "失败(%s): %s",
                rc.returncode, rc.stderr.strip(),
            )
        else:
            log.debug(
                "WorktreeInitializer.hooks: set core.hooksPath=%s in %s",
                rel_from_wt, worktree_path,
            )

    # ---- step 4: gitignore ----

    @staticmethod
    def _step_gitignore(
        setup_dir: Path, config: WorktreeInitConfig,
    ) -> None:
        """读 `<setup_dir>/.gitignore`,缺 `.worktrees/` 行则 append。"""
        gi = setup_dir / ".gitignore"
        pattern = re.compile(config.gitignore_pattern)

        if gi.exists():
            try:
                text = gi.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning(
                    "WorktreeInitializer.gitignore: 读 %s 失败(%s)",
                    gi, exc,
                )
                return
            if any(pattern.search(line) for line in text.splitlines()):
                # 已有匹配行,幂等跳过
                return
            new_text = text.rstrip("\n") + "\n.worktrees/\n"
        else:
            new_text = ".worktrees/\n"

        try:
            gi.write_text(new_text, encoding="utf-8")
            log.debug(
                "WorktreeInitializer.gitignore: append .worktrees/ -> %s",
                gi,
            )
        except OSError as exc:
            log.warning(
                "WorktreeInitializer.gitignore: 写 %s 失败(%s)",
                gi, exc,
            )


# ---------------------------------------------------------------------------
# 内部异常
# ---------------------------------------------------------------------------


class _CrossDriveError(Exception):
    """Windows junction 跨盘符(junction 必须同盘符)。"""


__all__ = [
    "WorktreeInitConfig",
    "WorktreeInitializer",
]