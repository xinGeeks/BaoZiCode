"""v1.4 Team Foundation + Pane Backend — Team / Member / Mailbox / 后端派生。

公开 API:

- `Team` / `Member` / `Message` —— frozen dataclass,持久化到
  `<teams_dir>/<team>/team.json` 和 `<member>/{inbox,outbox}.jsonl`
- `BackendType` —— Pydantic Literal,5 种后端(pane-tmux / pane-iterm2 /
  pane-windows-terminal / coroutine / worktree-coroutine)
- `MemberState` —— mailbox state.json 运行时状态(status / last_active_ts
  / current_task / backend_pid)
- `TeamNameValidator.validate(name)` —— 纯函数校验
- `TeamsRegistry` —— `<teams_dir>/` 索引,扫所有 team + 唯一性约束
- `TeamStore` —— 单个 team 的目录操作(create / load / add_member /
  destroy)
- `Mailbox` —— `<member>/` 目录的 JSONL 原子写 + state.json 读写 +
  wake.signal touch
- `mailbox_lock(path, *, timeout, stale_seconds)` —— 跨平台 lockfile
  context manager(POSIX fcntl.flock / Windows msvcrt.locking)

模块:

- `schema.py` — `TeamNameValidator` + `Team` / `Member` / `Message` /
  `MemberState` + 错误枚举
- `mailbox.py` — `Mailbox.append_message` / `read_messages` /
  `read_state` / `write_state` / `touch_wake` / `wait_for_wake`
- `lockfile.py` — `mailbox_lock` + 跨平台分发
- `store.py` — `TeamStore` 目录操作
- `registry.py` — `TeamsRegistry` 全局索引
- `cli.py` — `baozicode team` 子命令 argparse

v1.4 explore 锁定的 12 个决策中,foundation 仅覆盖 schema / mailbox /
lockfile / lifecycle 这一层(决策 8 / 9 / 10);其他 9 个由后续 3 个
proposal(team-tools / team-pane-backend / team-coordinator)在此之上
实现。
"""

from __future__ import annotations

from .lockfile import MailboxLockError, MailboxLockTimeout, mailbox_lock
from .backend_manager import BackendManager
from .mailbox import Direction, Mailbox
from .member_agent import MailboxLayer, build_member_agent
from .member_loop import MemberMainLoop
from .pane import (
    DEFAULT_TMUX_SESSION_PREFIX,
    BackendHandle,
    CoroutineBackend,
    PaneITerm2Backend,
    PaneTmuxBackend,
    PaneWindowsTerminalBackend,
    WorktreeCoroutineBackend,
    tmux_session_name,
    tmux_window_target,
)
from .pane_info import PANE_INFO_SCHEMA_VERSION, PaneInfo, PaneMemberInfo
from .registry import TeamsRegistry
from .schema import (
    BackendType,
    Member,
    MemberAlreadyExists,
    MemberNotFound,
    MemberState,
    MemberStatus,
    Message,
    MESSAGE_SCHEMA_VERSION,
    TEAM_SCHEMA_VERSION,
    Team,
    TeamAlreadyExists,
    TeamNameBadChar,
    TeamNameBadEnd,
    TeamNameBadStart,
    TeamNameDoubleHyphen,
    TeamNameInvalid,
    TeamNameTooLong,
    TeamNameTooShort,
    TeamNameValidator,
    TeamNotFound,
    default_member_state,
    fill_message_timestamp,
)
from .approval import ApprovalAction, ApprovalProtocol
from .mailbox_notifier import MailboxNotifier
from .merge import run_team_merge
from .store import TeamStore
from .tasks import Task, TaskCycleError, Tasks, TaskStatus
from .tools import register_team_tools, unregister_team_tools

__all__ = [
    "ApprovalAction",
    "ApprovalProtocol",
    "BackendHandle",
    "BackendManager",
    "BackendType",
    "CoroutineBackend",
    "DEFAULT_TMUX_SESSION_PREFIX",
    "Direction",
    "Mailbox",
    "MailboxLayer",
    "MailboxLockError",
    "MailboxLockTimeout",
    "MailboxNotifier",
    "Member",
    "MemberAlreadyExists",
    "MemberMainLoop",
    "MemberNotFound",
    "MemberState",
    "MemberStatus",
    "Message",
    "MESSAGE_SCHEMA_VERSION",
    "PANE_INFO_SCHEMA_VERSION",
    "PaneInfo",
    "PaneMemberInfo",
    "TEAM_SCHEMA_VERSION",
    "Task",
    "TaskCycleError",
    "TaskStatus",
    "Tasks",
    "Team",
    "TeamAlreadyExists",
    "TeamNameBadChar",
    "TeamNameBadEnd",
    "TeamNameBadStart",
    "TeamNameDoubleHyphen",
    "TeamNameInvalid",
    "TeamNameTooLong",
    "TeamNameTooShort",
    "TeamNameValidator",
    "TeamNotFound",
    "TeamStore",
    "TeamsRegistry",
    "WorktreeCoroutineBackend",
    "build_member_agent",
    "default_member_state",
    "fill_message_timestamp",
    "mailbox_lock",
    "register_team_tools",
    "run_team_merge",
    "tmux_session_name",
    "tmux_window_target",
    "unregister_team_tools",
]