"""v1.4 Team Tools — 共享任务清单(Tasks / Task)。

公开 API:

- `Task` —— frozen dataclass,持久化到 `<teams_dir>/<team>/tasks.jsonl`
- `TaskStatus` —— 6 个状态字面量(pending / ready / in_progress / done
  / failed / canceled)
- `Tasks` —— 静态方法集合:append / read_all / update_status / find_ready
  / detect_cycles(全部走 `<team_dir>/.tasks.lock` 锁)
- `TaskCycleError` —— 任务依赖成环异常

设计要点:

- `tasks.jsonl` 用 `mailbox_lock(.tasks.lock)` 串行化,与
  `<member>/.lock` 分开(Lead LLM 单写但不与成员并发 inbox 写互锁)
- `Tasks.update_status` 走 read-modify-replace(整文件
  write-then-rename),不允许 append-only 模式 —— tasks.jsonl 是
  durable state,既增也改,append-only 会产生多份同 id 条目
- `depends_on: tuple[str, ...]` 是 frozen hashable,frozen dataclass
  天然要求
- `find_ready` 拓扑门控简单实现:`task.status == "pending"` AND
  所有 deps 的 status ∈ {done, canceled}(canceled = 跳过)
- `detect_cycles` DFS 检测,环路径返 `list[list[str]]`(自环 / 二元
  环 / 三元环)
"""

from __future__ import annotations

import json
import os
import random
import shutil
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .lockfile import mailbox_lock
from .schema import fill_message_timestamp  # 复用 timestamp 自动补

TaskStatus = Literal[
    "pending",      # 已创建,deps 未满足
    "ready",        # deps 全部 satisfied,准备好被派
    "in_progress",  # 已被某 member 接管
    "done",         # 成功完成
    "failed",       # 失败
    "canceled",     # 显式取消
]


# 锁与外层 mailbox 锁区别(避免 Lead LLM 决策被并发 inbox 写阻塞)
_DEFAULT_LOCK_TIMEOUT = 5.0
_DEFAULT_LOCK_STALE = 30.0


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class TaskCycleError(ValueError):
    """任务依赖关系成环(自环 / 二元环 / 三元环)。"""


# ---------------------------------------------------------------------------
# Task dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """共享任务清单的一个条目。

    字段:

    - `id` —— 8 字符 hex token(由 `Tasks.append` 自动生成 `secrets.
      token_hex(4)`),LLM 不传
    - `body` —— 任务描述,plain text,可分多行
    - `status` —— 6 状态字面量之一
    - `depends_on` —— tuple of task id;frozen hashable;tuple 选择保证
      `Task` 可作 dict key
    - `assignee` —— member 名;None 表示尚未分配
    - `created_at / started_at / completed_at` —— UTC datetime
    - `error` —— 失败原因,仅 `status == "failed"` 时填
    """

    id: str
    body: str
    status: TaskStatus = "pending"
    depends_on: tuple[str, ...] = ()
    assignee: str | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        # id 校验
        if not isinstance(self.id, str):
            raise ValueError(f"Task.id 必须是 str,得到 {type(self.id).__name__}")
        if not self.id:
            raise ValueError("Task.id 不能为空")
        # status 校验
        allowed_status = (
            "pending", "ready", "in_progress", "done", "failed", "canceled"
        )
        if self.status not in allowed_status:
            raise ValueError(
                f"Task.status 非法 (得到 {self.status!r});"
                f" permitted: {', '.join(repr(s) for s in allowed_status)}"
            )
        # depends_on 必须是 tuple[str, ...]
        if not isinstance(self.depends_on, tuple):
            raise ValueError(
                f"Task.depends_on 必须是 tuple,得到 {type(self.depends_on).__name__}"
            )
        for dep in self.depends_on:
            if not isinstance(dep, str):
                raise ValueError(
                    f"Task.depends_on 含非 str: {dep!r}"
                )
        # body 必须是 str
        if not isinstance(self.body, str):
            raise ValueError(
                f"Task.body 必须是 str,得到 {type(self.body).__name__}"
            )
        # created_at tz
        if self.created_at.tzinfo is None:
            object.__setattr__(
                self,
                "created_at",
                self.created_at.replace(tzinfo=timezone.utc),
            )
        # started_at / completed_at 如果有,强制 UTC
        if self.started_at is not None and self.started_at.tzinfo is None:
            object.__setattr__(
                self,
                "started_at",
                self.started_at.replace(tzinfo=timezone.utc),
            )
        if self.completed_at is not None and self.completed_at.tzinfo is None:
            object.__setattr__(
                self,
                "completed_at",
                self.completed_at.replace(tzinfo=timezone.utc),
            )
        # error 仅 failed 时有
        if self.error is not None and self.status != "failed":
            # 工程警告但允许(future 用例)
            pass

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """转 JSON-serializable dict(JSONL 行内容)。"""
        return {
            "id": self.id,
            "body": self.body,
            "status": self.status,
            "depends_on": list(self.depends_on),
            "assignee": self.assignee,
            "created_at": self.created_at.isoformat(),
            "started_at": (
                self.started_at.isoformat() if self.started_at else None
            ),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "error": self.error,
        }

    def to_json_line(self) -> str:
        """JSONL 单行序列化(无 trailing newline)。"""
        return json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":")
        )

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """从 dict 构造 Task(JSONL 行解析用)。"""
        if not isinstance(data, dict):
            raise ValueError(
                f"Task dict 必须是 mapping,得到 {type(data).__name__}"
            )
        # id 必填
        if "id" not in data:
            raise ValueError("Task dict 缺 id 字段")
        # depends_on 默认 []
        deps_raw = data.get("depends_on") or []
        if not isinstance(deps_raw, list):
            raise ValueError(
                f"Task.depends_on 必须是 list,得到 {type(deps_raw).__name__}"
            )
        deps = tuple(str(d) for d in deps_raw)
        # 时间字段
        def _parse_dt(raw):
            if raw is None:
                return None
            if isinstance(raw, datetime):
                return raw
            if isinstance(raw, str):
                try:
                    return datetime.fromisoformat(raw)
                except ValueError as e:
                    raise ValueError(
                        f"Task 时间字段解析失败: {raw!r}: {e}"
                    ) from e
            raise ValueError(
                f"Task 时间字段类型不支持: {type(raw).__name__}"
            )
        return cls(
            id=str(data["id"]),
            body=str(data.get("body", "")),
            status=data.get("status", "pending"),
            depends_on=deps,
            assignee=(
                str(data["assignee"]) if data.get("assignee") else None
            ),
            created_at=_parse_dt(data.get("created_at")) or datetime.now(
                timezone.utc
            ),
            started_at=_parse_dt(data.get("started_at")),
            completed_at=_parse_dt(data.get("completed_at")),
            error=(
                str(data["error"]) if data.get("error") is not None else None
            ),
        )


# ---------------------------------------------------------------------------
# Tasks 文件层
# ---------------------------------------------------------------------------


class Tasks:
    """共享任务清单 — 静态方法集合,与 Mailbox 风格一致。

    所有方法都是无状态函数,接收 `<team_dir>/` 路径。
    """

    @staticmethod
    def _lock_path(team_dir: Path) -> Path:
        return team_dir / ".tasks.lock"

    @staticmethod
    def _target_path(team_dir: Path) -> Path:
        return team_dir / "tasks.jsonl"

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    @staticmethod
    def append(
        team_dir: Path,
        task: Task,
        *,
        lock_timeout: float = _DEFAULT_LOCK_TIMEOUT,
        lock_stale_seconds: float = _DEFAULT_LOCK_STALE,
    ) -> None:
        """原子追加一条 task 到 `<team_dir>/tasks.jsonl`。

        Args:
            team_dir: team 目录(已存在的 Team 目录)
            task: Task 实例;`created_at` 为 None 时自动补 UTC now
            lock_timeout: mailbox_lock 超时(秒)
            lock_stale_seconds: stale 锁判定(秒)

        Steps:
            1. 拿 `<team_dir>/.tasks.lock`
            2. 写临时 `.tasks.jsonl.<pid>.<rand>` + flush + fsync
            3. `shutil.copyfileobj` 追加到 `tasks.jsonl` + flush + fsync
            4. 删临时
            5. release 锁

        任何步骤崩溃后,`tasks.jsonl` MUST 仍是合法 JSONL。
        """
        # created_at 自动补(若 tzinfo 是 None 已经被 Task.__post_init__ 处理)
        if task.created_at is None:
            task = replace(task, created_at=datetime.now(timezone.utc))

        target = Tasks._target_path(team_dir)
        lock_path = Tasks._lock_path(team_dir)
        line = task.to_json_line() + "\n"

        with mailbox_lock(
            lock_path, timeout=lock_timeout, stale_seconds=lock_stale_seconds
        ):
            tmp = team_dir / f".tasks.jsonl.{os.getpid()}.{random.randint(0, 9999)}"
            try:
                team_dir.mkdir(parents=True, exist_ok=True)
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                with open(target, "a", encoding="utf-8", newline="\n") as dst:
                    with open(tmp, "r", encoding="utf-8", newline="\n") as src:
                        shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def read_all(
        team_dir: Path,
        *,
        skip_bad_lines: bool = True,
    ) -> list[Task]:
        """读整个 `tasks.jsonl`,坏行跳过,返回 `list[Task]`。

        Args:
            team_dir: team 目录
            skip_bad_lines: True(默认)— 坏行静默跳过;False — 抛
                ValueError

        Returns:
            `list[Task]`,append 时序返回;文件不存在 / 0 字节 → `[]`
        """
        target = Tasks._target_path(team_dir)
        if not target.exists() or target.stat().st_size == 0:
            return []
        result: list[Task] = []
        with open(target, "r", encoding="utf-8", newline="\n") as f:
            for line_no, raw in enumerate(f, start=1):
                raw = raw.rstrip("\r\n")
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as e:
                    if skip_bad_lines:
                        continue
                    raise ValueError(
                        f"{target}:{line_no} JSON 解析失败: {e}"
                    ) from e
                if not isinstance(data, dict):
                    if skip_bad_lines:
                        continue
                    raise ValueError(
                        f"{target}:{line_no} 不是 JSON object:"
                        f" {type(data).__name__}"
                    )
                try:
                    result.append(Task.from_dict(data))
                except (ValueError, KeyError):
                    if skip_bad_lines:
                        continue
                    raise
        return result

    @staticmethod
    def update_status(
        team_dir: Path,
        task_id: str,
        new_status: TaskStatus,
        *,
        assignee: str | None = None,
        error: str | None = None,
        lock_timeout: float = _DEFAULT_LOCK_TIMEOUT,
        lock_stale_seconds: float = _DEFAULT_LOCK_STALE,
    ) -> bool:
        """更新任务的 status / assignee / error。

        整个调用 MUST 在 `.tasks.lock` 内 read-modify-replace 完成:
        读全部 → 找 task → 改字段 → 整文件 write-then-rename。

        Args:
            team_dir: team 目录
            task_id: 要更新的 task id
            new_status: 新 status
            assignee: 若非 None,改 assignee 字段
            error: 若非 None,改 error 字段(通常仅 failed 时)

        Returns:
            True — 找到并更新;False — 没找到(同 id 多版本时取第一个,
            log warning 不抛)

        Side effects:
            - 首次 `pending|ready → in_progress` 自动填 `started_at=now`
            - 任意 `→ done|failed|canceled` 自动填 `completed_at=now`
            - 二次 `in_progress → in_progress` 不覆盖 `started_at`
        """
        target = Tasks._target_path(team_dir)
        lock_path = Tasks._lock_path(team_dir)
        now = datetime.now(timezone.utc)

        with mailbox_lock(
            lock_path, timeout=lock_timeout, stale_seconds=lock_stale_seconds
        ):
            tasks = Tasks.read_all(team_dir)
            found_idx = next(
                (i for i, t in enumerate(tasks) if t.id == task_id),
                None,
            )
            if found_idx is None:
                return False
            old = tasks[found_idx]

            # 确定 started_at:首次进 in_progress 时填
            new_started_at = old.started_at
            if new_status == "in_progress" and old.started_at is None:
                new_started_at = now
            # 确定 completed_at:进 terminal 时填
            new_completed_at = old.completed_at
            if new_status in ("done", "failed", "canceled"):
                new_completed_at = now

            new_task = replace(
                old,
                status=new_status,
                assignee=(
                    assignee if assignee is not None else old.assignee
                ),
                error=(error if error is not None else old.error),
                started_at=new_started_at,
                completed_at=new_completed_at,
            )
            tasks[found_idx] = new_task

            # 整文件 write-then-rename(在 lock 内)
            tmp = team_dir / f".tasks.jsonl.{os.getpid()}.{random.randint(0, 9999)}"
            try:
                content = "\n".join(t.to_json_line() for t in tasks) + "\n"
                tmp.write_text(content, encoding="utf-8")
                # os.replace 原子(同卷)
                os.replace(tmp, target)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
            return True

    @staticmethod
    def find_ready(team_dir: Path) -> list[Task]:
        """返回所有 deps 已 satisfied 的 `pending` 任务(`Task` 列表,
        按 append 时序)。

        拓扑门控规则:
        - `task.status == "pending"`
        - AND `task.depends_on` 中每个 dep 的 status ∈ {done, canceled}
          (canceled = 跳过)

        Returns:
            `list[Task]`,未满足依赖的任务 MUST NOT 出现。

        Side effect:无副作用(read-only)。调用方可配合
        `update_status` 把 ready task 标为 `in_progress` 或保留
        `pending`(手动派活时)。
        """
        all_tasks = Tasks.read_all(team_dir)
        # 索引 by id
        by_id: dict[str, Task] = {t.id: t for t in all_tasks}
        terminal_success = {"done", "canceled"}
        result: list[Task] = []
        for t in all_tasks:
            if t.status != "pending":
                continue
            # 每个 dep 都需在 terminal_success
            all_done = True
            for dep in t.depends_on:
                dep_task = by_id.get(dep)
                if dep_task is None:
                    # 引用了不存在的 task id → 视为未满足(不做 cycle
                    # 判断,detect_cycles 单独负责)
                    all_done = False
                    break
                if dep_task.status not in terminal_success:
                    all_done = False
                    break
            if all_done:
                result.append(t)
        return result

    @staticmethod
    def detect_cycles(team_dir: Path) -> list[list[str]]:
        """DFS 检测 depends_on 成环,返所有环的路径列表。

        Args:
            team_dir: team 目录

        Returns:
            `list[list[str]]` —— 每个内 list 是一个环的 task id 序列
            (e.g. `["t-001", "t-002"]` 表示 t-001 依赖 t-002 但
            t-002 也依赖 t-001)。无环返 `[]`。

        算法:
        对每个 task 做白灰黑 DFS 着色;白色 = 未访问,灰色 = 栈上,
        黑色 = 完成;命中灰色节点 → 找到一个环。
        """
        all_tasks = Tasks.read_all(team_dir)
        # 邻接表:id → set of ids 它依赖的
        adj: dict[str, set[str]] = {t.id: set(t.depends_on) for t in all_tasks}
        # 包含孤立 nodes(空依赖)在 adj 里也确保有 key
        all_ids = {t.id for t in all_tasks}

        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {tid: WHITE for tid in all_ids}
        parent: dict[str, str | None] = {tid: None for tid in all_ids}
        cycles: list[list[str]] = []

        def dfs(start: str, u: str, path: list[str]) -> None:
            color[u] = GRAY
            for v in adj.get(u, ()):  # type: ignore[arg-type]
                if v not in color:
                    # 引用了不存在 task id — 不当成环
                    continue
                if color[v] == GRAY:
                    # 找到环 — 从 v 开始逆推到 start
                    cycle_start = v
                    cycle = [v]
                    cur = u
                    while cur != cycle_start and cur != start:
                        cycle.append(cur)
                        cur = parent.get(cur)  # type: ignore[arg-type]
                        if cur is None:
                            break
                    cycle.reverse()
                    # 避免重复(同环被多次命中)
                    if cycle not in cycles:
                        cycles.append(cycle)
                elif color[v] == WHITE:
                    parent[v] = u
                    dfs(start, v, path + [v])
            color[u] = BLACK

        for tid in sorted(all_ids):
            if color[tid] == WHITE:
                parent[tid] = None
                dfs(tid, tid, [tid])
        return cycles


__all__ = [
    "Task",
    "Tasks",
    "TaskStatus",
    "TaskCycleError",
]
