"""v1.3 Worktree Isolation — schema 层(路径校验 + Spec + State + 错误)。

公开 API:

- `WorktreePathValidator.validate(name: str) -> None` —— 纯函数,无 IO,
  只做字符集 + 嵌套结构校验;失败抛 `PathValidationError`。
- `WorktreeSpec` —— frozen dataclass(name / path / branch / state)
  描述单个 worktree 的**配置**而非运行时状态。
- `WorktreeState` —— Literal 状态机:`creating` → `active` → `detached`
  → `removed`(或 `active` → `detached` → `stale` → `removed`)。
- `PathValidationError` —— 字符集/嵌套越界。
- `WorktreeNotInRepoError` —— `<setup_dir>` 不是 git repo。
- `WorktreeCreationFailed` —— `git worktree add` 失败(非空目录、分
  支冲突、磁盘满)。
- `WorktreeExistsDirtyError` —— fast-path 探测到目录存在但脏改动。

设计保证:

- 路径校验仅做字符串过滤,**绝不**做 IO(确定性 + 零副作用)。
- `WorktreeSpec.path` 总是 `<setup_dir>/.worktrees/<name>/` 的
  `Path.resolve()`(不含 trailing slash,绝对路径)。
- 状态机线性:`creating` 是创建中间态(未提交 git),`active` 是可用
  状态;`detached` 是有 dirty/未推送 commit 须手工处理;`stale` 是
  daemon 标定时长过期的 detached;`removed` 是终态(物理删除完成)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


# ---------------------------------------------------------------------------
# 路径名校验
# ---------------------------------------------------------------------------


# 字符集:小写/大写字母 + 数字 + `_` + `.` + `/` + `-`
# `/` 是嵌套语义("phase1/api-designer");不参与文件系统跳转
_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9_./-]+$")

# 长度上限(实测 git 分支名建议 ≤ 63,但留 64 方便加 prefix)
_MIN_LEN = 1
_MAX_LEN = 64


class PathValidationError(ValueError):
    """worktree 名非法(字符集 / 嵌套 / 长度越界)。"""


class WorktreeNotInRepoError(Exception):
    """`setup_dir` 不是 git repo —— `WorktreeManager.__init__` 检测时即抛。"""


class WorktreeNotFoundError(Exception):
    """`enter(name)` fast-path 失败 —— worktree 物理不存在或健康检
    查不通过。`exit(name)` 在 `spec.path.exists()` False 时也抛。"""


class WorktreeCreationFailed(Exception):
    """`git worktree add` 失败 —— 通常因目标目录非空或分支冲突。"""


class WorktreeExistsDirtyError(WorktreeCreationFailed):
    """fast-path 检查发现目录已存在 + `.git` file 在,但工作区脏。

    比 `WorktreeCreationFailed` 更具体;`WorktreeManager.create` 进
    normal-path 试图 `git worktree add` 又失败时也走这个(目录不
    空导致 add 报 "already exists")。
    """


class WorktreePathValidator:
    """worktree 名校验 —— 字符集 + 长度 + 嵌套结构。

    用法:

        >>> WorktreePathValidator.validate("api-designer")
        >>> WorktreePathValidator.validate("phase1/api-designer")
        >>> WorktreePathValidator.validate("../etc/passwd")
        Traceback (most recent call last):
            ...
        PathValidationError: 拒绝 . 或 .. 段: '../etc/passwd'
    """

    @staticmethod
    def validate(name: str) -> None:
        """校验 `name`。失败抛 `PathValidationError`,**不动 filesystem**。

        严格规则(D1):

        1. 长度 1 ≤ n ≤ 64
        2. 字符集 `[a-zA-Z0-9_./-]`
        3. 不以 `/` 起始、不以 `/` 结尾
        4. 任何段不空、不等于 `.`、不等于 `..`
        """
        if not isinstance(name, str):
            raise PathValidationError(f"name 必须是字符串,得到 {type(name).__name__}")
        if len(name) < _MIN_LEN or len(name) > _MAX_LEN:
            raise PathValidationError(
                f"name 长度越界 (1 ≤ n ≤ {_MAX_LEN}): 实际 {len(name)}"
            )
        if name.startswith("/") or name.endswith("/"):
            raise PathValidationError(
                f"不允许开头或结尾的 /: {name!r}"
            )
        if "//" in name:
            # 双斜杠是空段的另一种形式(如 `foo//bar` 隐含空段)
            raise PathValidationError(
                f"拒绝连续 // (含空段): {name!r}"
            )
        parts = name.split("/")
        for p in parts:
            if p in {"", ".", ".."}:
                raise PathValidationError(
                    f"拒绝 . 或 .. 段: {name!r}"
                )
        if not _VALID_NAME_RE.match(name):
            raise PathValidationError(
                f"字符集超出 [a-zA-Z0-9_./-]: {name!r}"
            )


# ---------------------------------------------------------------------------
# WorktreeSpec + WorktreeState
# ---------------------------------------------------------------------------


# 状态机字面量
WorktreeState = Literal[
    "creating",  # git worktree add 还没回执
    "active",    # 可用,可被 task 关联
    "detached",  # 有未提交/未推送 commit,须手工处理
    "stale",     # detached 超过 retention 阈值,daemon 可清理
    "removed",   # 物理目录已删,终态
]


@dataclass(frozen=True)
class WorktreeSpec:
    """单个 worktree 的**配置快照** —— frozen dataclass。

    字段含义:

    - `name`:用户给的合法名(经 `WorktreePathValidator` 校验)
    - `path`:`<setup_dir>/.worktrees/<name>/` 的绝对路径(`resolve()`
      后,无 trailing slash)
    - `branch`:git 分支名,格式 `wt/<name>`(可能因嵌套含 `/`,git
      允许但分支 ref 存为 `wt/phase1/api-designer`)
    - `state`:当前状态机值
    """

    name: str
    path: Path
    branch: str
    state: WorktreeState

    def __post_init__(self) -> None:
        # frozen 但可以 __post_init__ 规范化 Path
        if not isinstance(self.path, Path):
            object.__setattr__(self, "path", Path(self.path))
        if not self.path.is_absolute():
            # 强制绝对,frozen dataclass 的 __post_init__ 可改字段
            object.__setattr__(self, "path", self.path.resolve())

    @property
    def is_active(self) -> bool:
        """`True` iff state == "active",供 daemon / manager 简写判断。"""
        return self.state == "active"

    @property
    def is_deletable(self) -> bool:
        """`True` iff 物理存在 + state ∈ {active, detached, stale}。

        `creating` / `removed` 都不算(creating 等添加;removed 已不
        存在)。
        """
        return self.state in {"active", "detached", "stale"}

    def with_state(self, new_state: WorktreeState) -> "WorktreeSpec":
        """派生新 spec(`frozen` 不允许 `self.state = ...`,只能新建)。"""
        return WorktreeSpec(
            name=self.name,
            path=self.path,
            branch=self.branch,
            state=new_state,
        )


__all__ = [
    "PathValidationError",
    "WorktreeCreationFailed",
    "WorktreeExistsDirtyError",
    "WorktreeNotInRepoError",
    "WorktreeNotFoundError",
    "WorktreePathValidator",
    "WorktreeSpec",
    "WorktreeState",
]
