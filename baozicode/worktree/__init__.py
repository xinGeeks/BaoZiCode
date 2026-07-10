"""v1.3 Worktree Isolation — git worktree-backed sub-Agent 文件隔离。

公开 API:

- `WorktreePathValidator.validate(name)` — 严格路径名校验(字符集 +
  嵌套 + `.`/`..` 拒绝)
- `WorktreeSpec` — frozen dataclass 描述单个 worktree(name / path /
  branch / state)
- `WorktreeState` — Literal 状态机 `creating` / `active` / `detached` /
  `stale` / `removed`
- `WorktreeManager` — 生命周期编排(create / fast-path / enter / exit
  / remove)
- `WorktreeExitResult` — `exit` 决策树的返回(detached / removed +
  reason)
- `PathValidationError` / `WorktreeNotInRepoError` /
  `WorktreeNotFoundError` / `WorktreeCreationFailed` /
  `WorktreeExistsDirtyError` — 错误枚举

模块:

- `schema.py` — 路径校验 + Spec + State + 错误定义
- `manager.py` — `WorktreeManager` (create / fast-path / enter / exit
  / remove)
- `initializer.py` — `WorktreeInitializer` (4 步 link/copy/hooks /
  gitignore)
- `cleanup.py` — `WorktreeCleanupDaemon` (asyncio,三层过滤)

每个 sub-Agent 声明 `isolation: worktree` 时,本包为它在主 repo 内创
建一个独立 git worktree,做到文件系统物理隔离。退出时按未提交/未推
送决策保留或清理,后台 daemon 按三层过滤自动清理过期目录。
"""

from __future__ import annotations

from .cleanup import (
    CleanupAction,
    CleanupActionType,
    TaskActiveProbe,
    WorktreeCleanupDaemon,
)
from .initializer import WorktreeInitConfig, WorktreeInitializer
from .manager import WorktreeExitResult, WorktreeManager
from .schema import (
    PathValidationError,
    WorktreeCreationFailed,
    WorktreeExistsDirtyError,
    WorktreeNotInRepoError,
    WorktreeNotFoundError,
    WorktreePathValidator,
    WorktreeSpec,
    WorktreeState,
)

__all__ = [
    "CleanupAction",
    "CleanupActionType",
    "PathValidationError",
    "TaskActiveProbe",
    "WorktreeCleanupDaemon",
    "WorktreeCreationFailed",
    "WorktreeExistsDirtyError",
    "WorktreeInitConfig",
    "WorktreeInitializer",
    "WorktreeNotInRepoError",
    "WorktreeNotFoundError",
    "WorktreePathValidator",
    "WorktreeSpec",
    "WorktreeState",
    "WorktreeExitResult",
    "WorktreeManager",
]
