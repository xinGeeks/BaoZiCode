"""v1.3 Worktree Isolation — `WorktreeCleanupDaemon`(后台三层过滤清理)。

公开 API:

- `CleanupActionType` — `SKIPPED` / `BLOCKED` / `CLEANED` 枚举
- `CleanupAction` — frozen dataclass(`name` / `type` / `reason`)
- `TaskActiveProbe` — Protocol(`is_task_active(name) -> bool`)
- `WorktreeCleanupDaemon` — `start()` / `stop()` / `run_once()`
  三层过滤 + 决策出口

设计取舍:

- **无持久化状态**:daemon 跑时直接调 `manager.exit(name,
  force=False)`,由 `WorktreeExitResult` 决定 CLEANED / BLOCKED;
  `active` 状态的判定通过 `task_probe.is_task_active(name)`(spec
  第 3 层)。
- **三层顺序**:task → time → exit。task 优先(无 IO),time(stat mtime),
  exit(git 调用,最重)。
- **跨 daemon 重启**:daemon 不维护任何内存状态,只读 manager 的
  `iter_worktrees()` —— 重启无副作用。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from .manager import WorktreeManager
from .schema import WorktreeNotFoundError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CleanupAction — 决策结果
# ---------------------------------------------------------------------------


class CleanupActionType(str, Enum):
    """daemon 单次决策的类型。"""

    SKIPPED = "skipped"   # 任一过滤拒绝 → 不动
    BLOCKED = "blocked"   # exit 判定保留(dirty / unpushed)→ 不删
    CLEANED = "cleaned"   # exit 判定删(干净)→ 真删


@dataclass(frozen=True)
class CleanupAction:
    """`run_once` 对单个 worktree 的决策结果。"""

    name: str
    type: CleanupActionType
    reason: str
    """人类可读原因 —— SKIPPED: `task_active` / `fresh` / `active`;
    BLOCKED: `uncommitted_changes` / `unpushed_commits`;CLEANED:
    `clean`。"""


# ---------------------------------------------------------------------------
# TaskActiveProbe — 任务活跃判定(由 App 层注入)
# ---------------------------------------------------------------------------


@runtime_checkable
class TaskActiveProbe(Protocol):
    """判断 `name` 对应的 sub-Agent task 是否仍活跃(running / pending)。

    由 `SubAgentManager` 在 App 装配时注入。Daemon 调
    `is_task_active(name)` 做第 3 层过滤。
    """

    def is_task_active(self, name: str) -> bool: ...


# ---------------------------------------------------------------------------
# WorktreeCleanupDaemon
# ---------------------------------------------------------------------------


class WorktreeCleanupDaemon:
    """后台三层过滤清理 daemon。

    用法:
        daemon = WorktreeCleanupDaemon(
            manager=mgr,
            task_probe=subagent_manager,  # 实现 TaskActiveProbe
            retention_minutes=60,
            interval_seconds=60,
        )
        await daemon.start()  # 启动 background loop
        ...
        await daemon.stop()   # 优雅退出
    """

    def __init__(
        self,
        *,
        manager: WorktreeManager,
        task_probe: TaskActiveProbe,
        retention_minutes: int = 60,
        interval_seconds: float = 60.0,
    ) -> None:
        self._manager = manager
        self._task_probe = task_probe
        self._retention_seconds = float(retention_minutes) * 60.0
        self._interval_seconds = float(interval_seconds)
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动后台 loop。幂等:第二次调 no-op。"""
        if self._task is not None and not self._task.done():
            log.debug("WorktreeCleanupDaemon 已经启动")
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(), name="worktree-cleanup-daemon",
        )
        log.info(
            "WorktreeCleanupDaemon 启动: retention=%ds interval=%ds",
            int(self._retention_seconds),
            int(self._interval_seconds),
        )

    async def stop(self) -> None:
        """停止 daemon,await 当前 iteration 完成。"""
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except asyncio.TimeoutError:
            log.warning("WorktreeCleanupDaemon stop 超时,cancel")
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            self._task = None
            self._stop_event = None

    async def _loop(self) -> None:
        """后台 loop —— 每 `interval_seconds` 跑一次 `run_once`。"""
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                # daemon 不死 —— 异常打 log 继续下一轮
                log.exception("WorktreeCleanupDaemon.run_once 异常")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_seconds,
                )
            except asyncio.TimeoutError:
                # interval 到,继续下一轮
                continue
        log.debug("WorktreeCleanupDaemon loop 自然退出")

    # ---- 单次清理 ----

    async def run_once(self) -> list[CleanupAction]:
        """跑一次三层过滤 —— 返回每 worktree 的决策。

        三层顺序(spec 语义 → 实现顺序):
        - **Layer 1 — state**:由 task_probe + exit 决策覆盖;
          daemon 实际上把"active"判定合并到 layer 3(task_probe),
          "detached" 判定由 exit() 给出
        - **Layer 2 — time**:`mtime > now - retention_minutes` → SKIP
          `fresh`
        - **Layer 3 — task**:`task_probe.is_task_active(name)` → SKIP
          `task_active`

        全部通过 → 调 `manager.exit(name, force=False)` →
        `removed` 报 `CLEANED`,`detached` 报 `BLOCKED(reason=...)`。
        """
        actions: list[CleanupAction] = []
        now = time.time()
        for name, path in self._manager.iter_worktrees():
            action = await self._decide_one(name, path, now)
            actions.append(action)
        return actions

    async def _decide_one(
        self, name: str, path: Path, now: float,
    ) -> CleanupAction:
        # Layer 3: active task → SKIP
        if self._task_probe.is_task_active(name):
            return CleanupAction(
                name=name, type=CleanupActionType.SKIPPED,
                reason="task_active",
            )
        # Layer 2: fresh (too new) → SKIP
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            log.warning("WorktreeCleanupDaemon: stat %s 失败: %s", path, exc)
            return CleanupAction(
                name=name, type=CleanupActionType.SKIPPED,
                reason="stat_failed",
            )
        age = now - mtime
        if age < self._retention_seconds:
            return CleanupAction(
                name=name, type=CleanupActionType.SKIPPED,
                reason="fresh",
            )
        # All filters pass → call exit
        try:
            result = await self._manager.exit(name, force=False)
        except WorktreeNotFoundError:
            return CleanupAction(
                name=name, type=CleanupActionType.SKIPPED,
                reason="missing",
            )
        if result.removed:
            return CleanupAction(
                name=name, type=CleanupActionType.CLEANED,
                reason=result.reason,
            )
        return CleanupAction(
            name=name, type=CleanupActionType.BLOCKED,
            reason=result.reason,
        )


__all__ = [
    "CleanupAction",
    "CleanupActionType",
    "TaskActiveProbe",
    "WorktreeCleanupDaemon",
]