"""v1.4 Pane Backend — `BackendManager` 居中调度。

公开 API:

- `BackendManager(teams_registry, *, backend_detection_timeout,
  member_run_command)` —— 居中调度 5 种 BackendType
- `detect_available_backends() -> dict[BackendType, bool]` ——
  缓存 + 4 并行 `available()` probe(coroutine 永远 True)
- `effective_backend(member) -> BackendType` —— 决定 member 实际
  派生后端:显式 pane-* 优先(不健康降级)/ 默认 `coroutine` 在 pane
  健康时 upgrade / `worktree-coroutine` 留用户显式
- `spawn_if_offline(team, member) -> BackendHandle` —— 首 dispatch
  触发;同 (team, member) dedup;写 state.json + pane_info.json
- `is_alive(team, member) -> bool` —— 从 `_handles` 查
- `kill(team, member, *, reason="", grace_seconds=5.0)` ——
  写 state=offline + handle.kill(grace chain)
- `restore_panes(team) -> int` —— on_mount 触发:扫 pane_info.json
  对每成员 `os.kill(pid, 0)` 验证 pane 还活;活 → hydrate wrapper
  handle;死 → 移除 entry + log info

设计要点:

- 同 (team, member) spawn dedup 走 `asyncio.Lock`,首条 dispatch 拿
  锁 + spawn,后续 await 同 in-flight task 完成
- BackendManager 是 singleton(BaoZiCodeApp.on_mount 构造一次),
  panes 跨 Lead restart 持久(`on_unmount` 不清理)
- `member_run_command` 默认 `[sys.argv[0], "member", "run", ...]`,
  由 `Member` 的 workdir 决定 cwd
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mailbox import Mailbox
from .pane import (
    BackendHandle,
    CoroutineBackend,
    PaneITerm2Backend,
    PaneTmuxBackend,
    PaneWindowsTerminalBackend,
    WorktreeCoroutineBackend,
    _pid_alive,
    _safe_kill,
)
from .pane_info import PaneInfo, PaneMemberInfo
from .registry import TeamsRegistry
from .schema import BackendType, Member, MemberState, Message

log = logging.getLogger(__name__)

# BackendType → backend class 映射
_BACKEND_CLASS: dict[BackendType, type] = {
    "pane-tmux": PaneTmuxBackend,
    "pane-iterm2": PaneITerm2Backend,
    "pane-windows-terminal": PaneWindowsTerminalBackend,
    "coroutine": CoroutineBackend,
    "worktree-coroutine": WorktreeCoroutineBackend,
}


@dataclass
class _RestoredHandle:
    """从 pane_info.json 还原的最小 handle —— 仅 is_alive / kill。

    没 pane_id / window_id / tab_uuid 等 backend-specific 字段,
    不调用 spawn / title(后端 pane 已存在,无需重建)。
    """

    member_name: str
    team_name: str
    backend_type: BackendType
    pid: int | None

    def is_alive(self) -> bool:
        return _pid_alive(self.pid) if self.pid else False

    def kill(self, *, grace_seconds: float = 5.0) -> None:
        if self.pid is None:
            return
        _safe_kill(self.pid, 15)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if not _pid_alive(self.pid):
                return
            time.sleep(0.1)
        _safe_kill(self.pid, 9)

    def title(self, new_title: str) -> None:
        # restored handle 无 pane 句柄 — no-op
        log.debug("_RestoredHandle.title(%r) no-op", new_title)


class BackendManager:
    """Member 后端派生 + 调度 + 持久化居中。"""

    def __init__(
        self,
        teams_registry: TeamsRegistry,
        *,
        project_root: Path | None = None,
        backend_detection_timeout: float = 2.0,
        member_run_command: list[str] | None = None,
    ) -> None:
        self._registry = teams_registry
        self._project_root = project_root or Path(os.getcwd())
        self._backend_detection_timeout = backend_detection_timeout
        self._member_run_command = member_run_command or self._default_member_command()
        # 活跃 handles — {(team, member): BackendHandle}
        self._handles: dict[tuple[str, str], BackendHandle] = {}
        # spawn dedup locks — {(team, member): asyncio.Lock}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # backend availability 缓存
        self._detected_available: dict[BackendType, bool] | None = None
        # pane_info 路径缓存
        self._pane_info_path_cache: dict[str, Path] = {}

    @staticmethod
    def _default_member_command() -> list[str]:
        """默认 `baozicode member run` 命令 — 含 `--team {team} --name
        {member}` 占位符。BackendManager.spawn 替换占位。"""
        return [
            sys.argv[0] if sys.argv else "python",
            "member", "run",
            "--team", "{team}",
            "--name", "{name}",
        ]

    def _format_command(self, team: str, member: str) -> list[str]:
        """`member_run_command` 替换 `{team}` / `{name}` 占位符。"""
        return [
            p.format(team=team, name=member) for p in self._member_run_command
        ]

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    async def detect_available_backends(self) -> dict[BackendType, bool]:
        """探测环境可用的 backend —— 4 个 probe 并行,coroutine 永远 True。

        结果缓存,二次调用直接返缓存。
        """
        if self._detected_available is not None:
            return self._detected_available
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    asyncio.to_thread(PaneTmuxBackend.available),
                    asyncio.to_thread(PaneITerm2Backend.available),
                    asyncio.to_thread(PaneWindowsTerminalBackend.available),
                    return_exceptions=True,
                ),
                timeout=self._backend_detection_timeout,
            )
            tmux_ok = results[0] is True
            iterm_ok = results[1] is True
            wt_ok = results[2] is True
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            log.warning("detect_available_backends 探测超时/失败:%s", e)
            tmux_ok = iterm_ok = wt_ok = False

        self._detected_available = {
            "pane-tmux": tmux_ok,
            "pane-iterm2": iterm_ok,
            "pane-windows-terminal": wt_ok,
            "coroutine": True,
            "worktree-coroutine": True,  # 在 coroutine 之上,coroutine 活就活
        }
        log.info("detect_available_backends: %s", self._detected_available)
        return self._detected_available

    def _reset_detection_cache(self) -> None:
        """测试 hook — 重置 availability 缓存。"""
        self._detected_available = None

    # ------------------------------------------------------------------
    # Effective backend 选择
    # ------------------------------------------------------------------

    async def effective_backend(self, member: Member) -> BackendType:
        """决定 member 实际派生用的 backend。

        决策树(按 v1.4 explore 锁定决策 1):

        1. 显式 `member.backend in {pane-tmux, pane-iterm2,
           pane-windows-terminal}` → 强用(healthy 才 spawn,否则降级
           coroutine + log warn)
        2. 显式 `member.backend == "worktree-coroutine"` → 保留
        3. `member.backend == "coroutine"` 且 pane 健康 → 按 sys.platform
           优先级升级到 pane:
              - Windows → pane-windows-terminal
              - macOS → pane-iterm2
              - Linux/其他 → pane-tmux
        4. pane 全部不可用 → 仍用 `coroutine`
        """
        avail = await self.detect_available_backends()

        # case 1: 显式 pane-* 字符串
        if member.backend in ("pane-tmux", "pane-iterm2", "pane-windows-terminal"):
            if avail.get(member.backend, False):
                return member.backend
            log.warning(
                "effective_backend: 显式 %s 但不可用,降级到 coroutine",
                member.backend,
            )
            return "coroutine"

        # case 2: 显式 worktree-coroutine → 保留
        if member.backend == "worktree-coroutine":
            return "worktree-coroutine"

        # case 3: coroutine 默认 + pane 健康 → upgrade
        import platform as _platform
        sys_platform = _platform.system()
        candidate: BackendType
        if sys_platform == "Windows" and avail.get("pane-windows-terminal", False):
            candidate = "pane-windows-terminal"
        elif sys_platform == "Darwin" and avail.get("pane-iterm2", False):
            candidate = "pane-iterm2"
        elif sys_platform in ("Linux", "") and avail.get("pane-tmux", False):
            candidate = "pane-tmux"
        else:
            return "coroutine"

        # case 4: 候选 pane 健康 → upgrade
        if avail.get(candidate, False):
            return candidate
        return "coroutine"

    # ------------------------------------------------------------------
    # spawn / kill / is_alive
    # ------------------------------------------------------------------

    def _member_dir(self, team: str, member: str) -> Path:
        """`<teams_dir>/<team>/<member>/` —— member mailbox 根目录。"""
        return self._registry.teams_dir / team / member

    def _pane_info_path(self, team: str) -> Path:
        if team not in self._pane_info_path_cache:
            self._pane_info_path_cache[team] = (
                self._registry.teams_dir / team / "pane_info.json"
            )
        return self._pane_info_path_cache[team]

    def _build_handle(
        self, backend_type: BackendType, team: str, member: Member
    ) -> BackendHandle:
        """构造 backend 实例(command 用 member_run_command 模板替换)。"""
        command = self._format_command(team, member.name)
        cls = _BACKEND_CLASS[backend_type]
        if backend_type == "worktree-coroutine":
            return cls(
                member_name=member.name,
                team_name=team,
                command=command,
                workdir=member.workdir,
                setup_dir=str(self._project_root),
            )
        return cls(
            member_name=member.name,
            team_name=team,
            command=command,
        )

    async def spawn_if_offline(
        self, team: str, member: Member
    ) -> BackendHandle:
        """首 dispatch 触发派生;同 (team, member) dedup。

        流程:
          1. 快路径 — handle 在 `_handles` 且 alive → 返现有
          2. 拿 `(team, member)` 锁,锁内二次检查
          3. `effective_backend` 选 backend
          4. `_build_handle` 构造实例
          5. handle.spawn()(`asyncio.to_thread` 包装同步 backend)
          6. Mailbox.write_state(idle/running + backend_pid)
          7. 持久化 pane_info.json
          8. 缓存到 `_handles`
        """
        key = (team, member.name)
        # 1) 快路径
        existing = self._handles.get(key)
        if existing is not None:
            try:
                if existing.is_alive():
                    return existing
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "spawn_if_offline: is_alive 检查失败 %s: %s",
                    key, e,
                )

        # 2) per-(team, member) lock
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # 锁内二次检查
            existing = self._handles.get(key)
            if existing is not None and existing.is_alive():
                return existing
            # 3) effective_backend
            backend_type = await self.effective_backend(member)
            # 4) build handle
            handle = self._build_handle(backend_type, team, member)
            # 5) spawn
            try:
                if isinstance(handle, CoroutineBackend):
                    await handle.spawn()
                else:
                    await asyncio.to_thread(handle.spawn)
            except Exception as e:  # noqa: BLE001
                log.error(
                    "spawn_if_offline: %s/%s spawn 失败 (%s),降级 coroutine",
                    team, member.name, e,
                )
                # 失败 fallback 到 coroutine(覆盖原 handle)
                backend_type = "coroutine"
                handle = self._build_handle(backend_type, team, member)
                await handle.spawn()
            # 6) write state
            member_dir = self._member_dir(team, member.name)
            member_dir.mkdir(parents=True, exist_ok=True)
            new_state = MemberState(
                status="idle",
                backend_pid=handle.pid,
            )
            Mailbox.write_state(member_dir, new_state)
            # 7) pane_info 持久化
            self._persist_pane_info(team, member, handle, backend_type)
            # 8) 缓存
            self._handles[key] = handle
            log.info(
                "spawn_if_offline: %s/%s → %s pid=%s",
                team, member.name, backend_type, handle.pid,
            )
            return handle

    def _persist_pane_info(
        self,
        team: str,
        member: Member,
        handle: BackendHandle,
        backend_type: BackendType,
    ) -> None:
        """写 pane_info.json:backend-specific 句柄 + pid + last_active_ts。"""
        pane_path = self._pane_info_path(team)
        info = PaneInfo.load(pane_path) or PaneInfo.empty(team=team)
        # 用 dataclasses.replace 更新 members dict
        pane_id = ""
        if isinstance(handle, PaneTmuxBackend):
            pane_id = handle.pane_id
        elif isinstance(handle, PaneITerm2Backend):
            pane_id = handle.window_id
        elif isinstance(handle, PaneWindowsTerminalBackend):
            pane_id = handle.tab_uuid
        # frozen=True → replace
        from dataclasses import replace
        new_members = dict(info.members)
        new_members[member.name] = PaneMemberInfo(
            backend_type=backend_type,
            pane_identifier=pane_id,
            pid=handle.pid,
            last_active_ts=datetime.now(timezone.utc),
        )
        new_info = replace(info, members=new_members)
        new_info.save(pane_path)

    def is_alive(self, team: str, member_name: str) -> bool:
        """`_handles[(team, member)].is_alive()`;无 entry → False。"""
        handle = self._handles.get((team, member_name))
        if handle is None:
            return False
        try:
            return handle.is_alive()
        except Exception as e:  # noqa: BLE001
            log.warning(
                "is_alive: %s/%s 检查失败:%s",
                team, member_name, e,
            )
            return False

    async def kill(
        self,
        team: str,
        member_name: str,
        *,
        reason: str = "",
        grace_seconds: float = 5.0,
    ) -> bool:
        """kill 流程:写 state=offline → handle.kill(grace 链)。

        Returns True — kill 触发成功;False — handle 不存在(无可杀)。
        """
        # 1) write state=offline
        member_dir = self._member_dir(team, member_name)
        if member_dir.exists():
            try:
                old = Mailbox.read_state(member_dir)
                new_state = MemberState(
                    status="offline",
                    last_active_ts=old.last_active_ts,
                    current_task=None,
                    backend_pid=None,
                )
                Mailbox.write_state(member_dir, new_state)
            except Exception as e:  # noqa: BLE001
                log.warning("kill: 写 state=offline 失败:%s", e)
        # 2) handle.kill
        handle = self._handles.pop((team, member_name), None)
        if handle is None:
            log.info("kill: %s/%s 无活跃 handle", team, member_name)
            return False
        try:
            if isinstance(handle, CoroutineBackend):
                handle.kill(grace_seconds=grace_seconds)
            else:
                await asyncio.to_thread(handle.kill, grace_seconds=grace_seconds)
        except Exception as e:  # noqa: BLE001
            log.error(
                "kill: %s/%s handle.kill 失败:%s",
                team, member_name, e,
            )
        # 3) pane_info entry 标记 backend_type=None(标识已 kill)
        pane_path = self._pane_info_path(team)
        info = PaneInfo.load(pane_path)
        if info is not None and member_name in info.members:
            from dataclasses import replace
            new_members = dict(info.members)
            existing = new_members[member_name]
            new_members[member_name] = PaneMemberInfo(
                backend_type=existing.backend_type,
                pane_identifier=existing.pane_identifier,
                pid=None,  # 清 pid(标识已 kill)
                last_active_ts=existing.last_active_ts,
            )
            new_info = replace(info, members=new_members)
            new_info.save(pane_path)
        log.info(
            "kill: %s/%s reason=%s grace=%ss",
            team, member_name, reason, grace_seconds,
        )
        return True

    # ------------------------------------------------------------------
    # restore_panes — Lead 启动时恢复 pane 状态
    # ------------------------------------------------------------------

    def restore_panes(self, team: str) -> int:
        """读 pane_info.json,对每成员验证 pid 还活。

        - 活 → hydrate `_RestoredHandle`,缓存到 `_handles`
        - 死 → 移除 entry + log info
        - 文件不存在 → 0(无需恢复)

        Returns: 成功 hydrate 的 member 数。
        """
        pane_path = self._pane_info_path(team)
        info = PaneInfo.load(pane_path)
        if info is None:
            log.debug("restore_panes: %s 无 pane_info.json", team)
            return 0
        restored = 0
        from dataclasses import replace
        new_members: dict[str, PaneMemberInfo] = {}
        for mname, minfo in info.members.items():
            if minfo.pid is not None and _pid_alive(minfo.pid):
                # 活 → hydrate
                self._handles[(team, mname)] = _RestoredHandle(
                    member_name=mname,
                    team_name=team,
                    backend_type=minfo.backend_type,
                    pid=minfo.pid,
                )
                new_members[mname] = minfo
                restored += 1
                log.info(
                    "restore_panes: %s/%s pid=%s 还活",
                    team, mname, minfo.pid,
                )
            else:
                # 死 → 移除
                log.info(
                    "restore_panes: %s/%s pid=%s 已死,移除 entry",
                    team, mname, minfo.pid,
                )
        # 写回清理后的 pane_info(死的已移除)
        new_info = replace(info, members=new_members)
        new_info.save(pane_path)
        return restored

    # ------------------------------------------------------------------
    # cleanup_team — team destroy 时调,清所有 handles + pane_info
    # ------------------------------------------------------------------

    async def cleanup_team(self, team: str) -> int:
        """team destroy 触发:kill 所有 member + 删 pane_info。

        Returns: 杀掉的 member 数。
        """
        killed = 0
        keys = [k for k in self._handles if k[0] == team]
        for key in keys:
            _, mname = key
            ok = await self.kill(team, mname, reason="team-destroy")
            if ok:
                killed += 1
        # 删 pane_info
        pane_path = self._pane_info_path(team)
        if pane_path.exists():
            try:
                pane_path.unlink()
            except OSError as e:
                log.warning(
                    "cleanup_team: 删 %s 失败:%s", pane_path, e,
                )
        return killed


__all__ = ["BackendManager"]
