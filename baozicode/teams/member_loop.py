"""v1.4 Pane Backend — `MemberMainLoop` 长生命周期 polling。

公开 API:

- `MemberMainLoop(teams_registry, team_name, member_name, *,
  llm_client, config, permissions, project_root=None,
  skill_*)` —— 长生命周期 polling 循环:`wait_for_wake →
  read_messages → build_member_agent → run_turn → write_outbox →
  write_state(idle) → loop`
- `request_terminate()` —— set terminate flag + cancel active turn
  task;供 CLI signal handler / `team_cancel(terminate=True)` 调

设计要点:

- Member Agent 是 fresh-per-turn(`build_member_agent` 每轮 wake
  调一次)— 避免 Agent state 跨 turn 串台,符合 v0.3 Agent Loop 抽
  出语义
- 循环节奏:`wait_for_wake(timeout=...)` → 阻塞等 wake.signal mtime
  更新;超时返 False → 回到 while 顶(允许 terminate 检查)
- 异常隔离:每 turn try/except 包,不让 turn 失败挂整个 loop
- `request_terminate` cancel active turn task(LLM stream 取消)— 不
  强杀进程,graceful
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from baozicode.config.schema import AppConfig
from baozicode.llm.base import LLMClient
from baozicode.permissions.types import MergedPermissions
from baozicode.skills.activation import SkillActivation
from baozicode.skills.registry import SkillRegistry
from baozicode.skills.whitelist import SkillWhitelistFilter

from .mailbox import Mailbox
from .member_agent import MailboxLayer, build_member_agent
from .registry import TeamsRegistry
from .schema import Member, MemberNotFound, MemberState, Message, TeamNotFound

log = logging.getLogger(__name__)

# 默认 polling 参数
_DEFAULT_WAKE_TIMEOUT = 60.0  # 每次 wait_for_wake 的最长阻塞


class MemberMainLoop:
    """Member 进程长生命周期 polling loop。"""

    def __init__(
        self,
        teams_registry: TeamsRegistry,
        team_name: str,
        member_name: str,
        *,
        llm_client: LLMClient,
        config: AppConfig,
        permissions: MergedPermissions,
        project_root: Path | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_activation: SkillActivation | None = None,
        skill_filter: SkillWhitelistFilter | None = None,
        wake_timeout: float = _DEFAULT_WAKE_TIMEOUT,
    ) -> None:
        self._registry = teams_registry
        self._team_name = team_name
        self._member_name = member_name
        self._llm = llm_client
        self._config = config
        self._permissions = permissions
        self._project_root = project_root
        self._skill_registry = skill_registry
        self._skill_activation = skill_activation
        self._skill_filter = skill_filter
        self._wake_timeout = wake_timeout
        # 派生状态
        self._team: Any = None
        self._member: Member | None = None
        self._member_dir: Path | None = None
        # 控制
        self._terminate_flag = asyncio.Event()
        self._active_turn_task: asyncio.Task | None = None
        self._mailbox_layer: MailboxLayer | None = None
        # turn 计数
        self._turn_count = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_terminate(self) -> None:
        """set terminate flag + cancel active turn task(graceful)。"""
        self._terminate_flag.set()
        if self._active_turn_task is not None and not self._active_turn_task.done():
            self._active_turn_task.cancel()

    @property
    def is_terminated(self) -> bool:
        return self._terminate_flag.is_set()

    @property
    def turn_count(self) -> int:
        return self._turn_count

    # ------------------------------------------------------------------
    # run — 主循环
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """长生命周期 polling loop。终止条件:

        - `request_terminate()` set flag
        - 进程被 SIGINT / SIGTERM(CLI signal handler 调
          `request_terminate`)
        - 异常致循环退出(应被外层 supervisor 重启)
        """
        # 1) 解析 team / member(失败抛 TeamNotFound / MemberNotFound)
        self._team = self._registry.get(self._team_name)
        if self._team is None:
            raise TeamNotFound(f"team {self._team_name!r} 不存在")
        team_obj = self._team.show()
        if self._member_name not in team_obj.members:
            raise MemberNotFound(
                f"member {self._member_name!r} 在 team {self._team_name!r} 中不存在"
            )
        self._member = team_obj.members[self._member_name]
        self._member_dir = (
            self._registry.teams_dir / self._team_name / self._member_name
        )
        self._mailbox_layer = MailboxLayer(
            self._registry, self._team_name, self._member_name
        )
        # 2) chdir 到 member workdir(让 LLM 跑 git 等命令 cwd 对)
        workdir_path = self._resolve_workdir()
        try:
            workdir_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("MemberMainLoop: 创建 workdir %s 失败:%s", workdir_path, e)
        try:
            os.chdir(workdir_path)
        except OSError as e:
            log.warning("MemberMainLoop: chdir %s 失败:%s", workdir_path, e)
        # 3) wake_initialized:记录当前 mtime 作 wait_for_wake 起点
        Mailbox.wake_initialized(self._member_dir)
        log.info(
            "MemberMainLoop 启动: %s/%s workdir=%s",
            self._team_name, self._member_name, workdir_path,
        )
        # 4) 主循环
        while not self._terminate_flag.is_set():
            try:
                await self._run_once()
            except asyncio.CancelledError:
                log.info("MemberMainLoop: turn cancelled,exit loop")
                break
            except Exception as e:  # noqa: BLE001
                log.error(
                    "MemberMainLoop: turn %d 异常: %s",
                    self._turn_count, e,
                )
                # 不挂 loop,等下一轮 wake

        # 退出:写 state=offline
        if self._member_dir is not None:
            try:
                Mailbox.write_state(
                    self._member_dir,
                    MemberState(status="offline"),
                )
            except OSError as e:
                log.warning("MemberMainLoop: 退出写 state 失败:%s", e)
        log.info(
            "MemberMainLoop 退出: %s/%s turns=%d",
            self._team_name, self._member_name, self._turn_count,
        )

    async def _run_once(self) -> None:
        """一轮 wake → read → build_agent → run → write_outbox → idle。"""
        assert self._member_dir is not None
        assert self._member is not None
        assert self._mailbox_layer is not None
        # 1) 等 wake.signal
        woke = await Mailbox.wait_for_wake(
            self._member_dir, timeout=self._wake_timeout
        )
        if not woke:
            return  # 超时,回到 while 顶(让 terminate 检查)
        if self._terminate_flag.is_set():
            return
        # 2) 读未读 inbox
        unread = self._mailbox_layer.read_inbox_unread()
        if not unread:
            return
        log.info(
            "MemberMainLoop: turn %d 收到 %d 条消息",
            self._turn_count + 1, len(unread),
        )
        # 3) 写 state=running
        Mailbox.write_state(
            self._member_dir,
            MemberState(status="running"),
        )
        # 4) fresh agent(每轮 new — 避免 state 串台)
        assert self._member is not None
        agent = build_member_agent(
            self._registry,
            self._team_name,
            self._member,
            llm_client=self._llm,
            config=self._config,
            permissions=self._permissions,
            project_root=self._project_root,
            skill_registry=self._skill_registry,
            skill_activation=self._skill_activation,
            skill_filter=self._skill_filter,
        )
        # 5) run turn(把 inbox 拼成 user message)
        user_msg = self._format_unread_as_user_msg(unread)
        self._active_turn_task = asyncio.current_task()
        try:
            response_text = await self._run_turn(agent, user_msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("MemberMainLoop: agent.run 异常:%s", e)
            response_text = f"[error] turn failed: {e}"
        finally:
            self._active_turn_task = None
        # 6) 写 outbox
        if response_text:
            self._mailbox_layer.write_outbox(response_text)
        # 7) 标 inbox 已读
        self._mailbox_layer.mark_inbox_read(unread)
        # 8) 写 state=idle
        Mailbox.write_state(
            self._member_dir,
            MemberState(status="idle"),
        )
        self._turn_count += 1

    async def _run_turn(
        self, agent: Any, user_msg: str
    ) -> str:
        """跑一次 Agent turn,收集 text 流,返完整 text。"""
        result = ""
        async for event in agent.run(user_msg):
            if event.type == "text":
                result += event.payload
            elif event.type == "done":
                break
            elif event.type == "error":
                log.warning("MemberMainLoop: agent error event: %s", event)
                break
        return result

    @staticmethod
    def _format_unread_as_user_msg(messages: list[Message]) -> str:
        """`---MSG-<i>---\nsender=<sender>\n\n<body>\n---END---` 拼接。"""
        chunks = []
        for i, m in enumerate(messages, start=1):
            chunks.append(
                f"---MSG-{i}---\n"
                f"sender={m.sender}\n"
                f"timestamp={m.timestamp.isoformat() if m.timestamp else 'N/A'}\n"
                f"\n{m.body}\n"
                f"---END---"
            )
        return "\n\n".join(chunks)

    def _resolve_workdir(self) -> Path:
        """解析 member.workdir(可能相对 / 绝对)→ 绝对路径。"""
        assert self._member is not None
        wd = self._member.workdir or f".worktrees/{self._member_name}/"
        p = Path(wd)
        if p.is_absolute():
            return p
        base = self._project_root or Path(os.getcwd())
        return base / p


__all__ = ["MemberMainLoop"]
