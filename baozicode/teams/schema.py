"""v1.4 Team Foundation — schema 层(名字校验 + Team/Member/Message + 错误)。

公开 API:

- `TeamNameValidator.validate(name)` —— 纯函数,无 IO,字符集 + 长度
  + 起止校验;失败抛 `TeamNameInvalid` 子类
- `Team` —— frozen dataclass(name / lead / created_at / members / metadata)
  + `to_json` / `from_json` / `load` / `save`
- `Member` —— frozen dataclass(name / role / workdir / backend /
  requires_approval / config)+ `BackendType` Literal 强校验
- `Message` —— frozen dataclass(sender / body / timestamp / read /
  summary)+ `to_json_line` / `from_dict`
- `MemberState` —— frozen dataclass(status / last_active_ts /
  current_task / backend_pid),mailbox state.json 序列化
- `MemberStatus` —— Literal 状态机(offline / idle / running / waiting)
- 错误枚举:`TeamNameTooShort` / `TooLong` / `BadChar` / `BadStart` /
  `BadEnd` / `DoubleHyphen` / `MemberAlreadyExists` / `TeamAlreadyExists`
  / `TeamNotFound` / `MemberNotFound`

设计保证:

- `TeamNameValidator.validate` 是**纯函数**,无 IO、无副作用,LLM 直
  传名(类似 `WorktreePathValidator` v1.3 契约)。
- 所有 dataclass 都 `frozen=True`,防止业务层误改。
- `Member.workdir` 留默认 `.worktrees/<name>/`,`__post_init__` 自动
  补(LLM 不传也不挂)。
- `BackendType` 用 Pydantic `Literal` 强校验,LLM 拼错(如
  `"pane-Tmux"`)启动期 fail-fast。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Team name 校验
# ---------------------------------------------------------------------------


# 字符集:小写字母 + 数字 + 中划线
_VALID_NAME_RE = re.compile(r"^[a-z0-9-]+$")

# 长度范围
_MIN_LEN = 2
_MAX_LEN = 30


class TeamNameInvalid(ValueError):
    """Team / Member 名非法(基类)。"""

    code: str = "team_name_invalid"

    def __init__(self, message: str, *, code: str = "team_name_invalid") -> None:
        super().__init__(message)
        self.code = code


class TeamNameTooShort(TeamNameInvalid):
    code = "team_name_too_short"


class TeamNameTooLong(TeamNameInvalid):
    code = "team_name_too_long"


class TeamNameBadChar(TeamNameInvalid):
    code = "team_name_bad_char"


class TeamNameBadStart(TeamNameInvalid):
    code = "team_name_bad_start"


class TeamNameBadEnd(TeamNameInvalid):
    code = "team_name_bad_end"


class TeamNameDoubleHyphen(TeamNameInvalid):
    code = "team_name_double_hyphen"


class TeamNameValidator:
    """Team / Member name 校验 — 字符集 + 长度 + 起止。

    用法:

        >>> TeamNameValidator.validate("devops")
        >>> TeamNameValidator.validate("acme-team")
        >>> TeamNameValidator.validate("team--001")
        Traceback (most recent call call):
            ...
        TeamNameDoubleHyphen: team--001 含连续 -- (视觉易混)
    """

    @staticmethod
    def validate(name: str) -> None:
        """校验 `name`。失败抛 `TeamNameInvalid` 子类,**不动 filesystem**。

        严格规则(基于 v1.4 explore 锁定决策):

        1. 长度 2 ≤ n ≤ 30
        2. 字符集 `[a-z0-9-]`(拒绝大写 / `.` / `_` / `/` / `\\`)
        3. 必须以字母开头(拒绝数字 / `-` 开头)
        4. 必须以字母或数字结尾(拒绝 `-` 结尾)
        5. 拒绝连续 `--`(视觉易混)

        验证顺序(优先级从高到低):
        - 类型 / 长度 / 字符集(先报,避免误分类)
        - 然后连续 `--`(语义独立于位置)
        - 然后首字符 / 末字符(定位更具体)
        """
        if not isinstance(name, str):
            raise TeamNameBadChar(
                f"name 必须是字符串,得到 {type(name).__name__}"
            )
        if len(name) < _MIN_LEN:
            raise TeamNameTooShort(
                f"name 过短 (<{_MIN_LEN}): 实际 {len(name)} ({name!r})"
            )
        if len(name) > _MAX_LEN:
            raise TeamNameTooLong(
                f"name 超长 (>{_MAX_LEN}): 实际 {len(name)} ({name!r})"
            )
        # 字符集 — 必须先于首字符检查,这样 "DevOps" 报 BadChar 而不是 BadStart
        if not _VALID_NAME_RE.match(name):
            raise TeamNameBadChar(
                f"name 含非 [a-z0-9-] 字符: {name!r}"
            )
        # 连续 --
        if "--" in name:
            raise TeamNameDoubleHyphen(
                f"name 含连续 -- (视觉易混): {name!r}"
            )
        # 首字符 — 通过字符集后,首字符可能是 `-` 或数字
        first = name[0]
        if first == "-":
            raise TeamNameBadStart(f"name 不能以 `-` 开头: {name!r}")
        if "0" <= first <= "9":
            raise TeamNameBadStart(f"name 不能以数字开头: {name!r}")
        # 末字符 — 通过字符集后,末字符可能是 `-`
        if name[-1] == "-":
            raise TeamNameBadEnd(f"name 不能以 `-` 结尾: {name!r}")


# ---------------------------------------------------------------------------
# 业务层错误枚举(目录操作相关)
# ---------------------------------------------------------------------------


class TeamAlreadyExists(FileExistsError):
    """`TeamsRegistry.create_team` / `TeamStore.create` 发现同名 team 已存在。"""


class TeamNotFound(FileNotFoundError):
    """`TeamsRegistry.get` / `TeamStore.load` 找不到 team。"""


class MemberAlreadyExists(FileExistsError):
    """`TeamStore.add_member` 发现同名 member 已存在。"""


class MemberNotFound(FileNotFoundError):
    """`TeamStore.get_member` 找不到 member。"""


# ---------------------------------------------------------------------------
# 后端 Literal
# ---------------------------------------------------------------------------


# v1.4 explore 锁定:5 种后端,详见 design.md。
# 拼错值(如 `"pane-Tmux"`)由 Member 的 Pydantic validator 强校验
BackendType = Literal[
    "pane-tmux",
    "pane-iterm2",
    "pane-windows-terminal",
    "coroutine",
    "worktree-coroutine",
]


# ---------------------------------------------------------------------------
# Member / Team / Message dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """Team 内单个队员的描述。

    字段语义:

    - `name` — 队员名,经 `TeamNameValidator.validate`
    - `role` — 自由描述(`backend` / `frontend` / `tester` ...)
    - `workdir` — 工作目录,相对项目根;`__post_init__` 默认补
      `.worktrees/<name>/`
    - `backend` — 运行时后端类型(Pydantic Literal)
    - `requires_approval` — 是否需要 Lead 审批后才动手,默认 False
    - `config` — 后端特定配置(pane_id / session_name 等)
    """

    name: str
    role: str
    workdir: str = ""
    backend: BackendType = "coroutine"
    requires_approval: bool = False
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 名字校验(frozen=True 不能直接赋值,用 object.__setattr__)
        TeamNameValidator.validate(self.name)
        # backend Pydantic Literal 校验 — 用 _validate_backend 报错带详情
        _validate_backend(self.backend)
        # workdir 自动补
        if not self.workdir:
            object.__setattr__(self, "workdir", f".worktrees/{self.name}/")
        # config 必须是 dict[str, Any](LLM 可能传 dict-like 但 typing 不保证)
        if not isinstance(self.config, dict):
            raise ValueError(
                f"Member.config 必须是 dict,得到 {type(self.config).__name__}"
            )

    def to_dict(self) -> dict[str, Any]:
        """转 JSON-serializable dict(供 Team.members 嵌套使用)。"""
        return {
            "name": self.name,
            "role": self.role,
            "workdir": self.workdir,
            "backend": self.backend,
            "requires_approval": self.requires_approval,
            "config": dict(self.config),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Member:
        """从 dict 构造 Member(读 team.json 用)。"""
        if not isinstance(data, dict):
            raise ValueError(
                f"Member dict 必须是 mapping,得到 {type(data).__name__}"
            )
        return cls(
            name=str(data["name"]),
            role=str(data.get("role", "")),
            workdir=str(data.get("workdir", "")),
            backend=data.get("backend", "coroutine"),
            requires_approval=bool(data.get("requires_approval", False)),
            config=dict(data.get("config", {})),
        )


def _validate_backend(backend: str) -> None:
    """校验 BackendType Literal;失败抛 ValueError 含 permitted 列表。"""
    allowed = (
        "pane-tmux",
        "pane-iterm2",
        "pane-windows-terminal",
        "coroutine",
        "worktree-coroutine",
    )
    if backend not in allowed:
        raise ValueError(
            f"Member.backend 不合法 (得到 {backend!r});"
            f" permitted: {', '.join(repr(a) for a in allowed)}"
        )


@dataclass(frozen=True)
class Message:
    """Mailbox 一条消息。

    字段语义:

    - `sender` — 发件人(`"lead"` / member name / `"system"`)
    - `body` — 消息正文(plain text,可含 YAML frontmatter)
    - `timestamp` — `None` → `Mailbox.append_message` 自动补 UTC now
    - `read` — 默认 False,收件人读过可标 True
    - `summary` — 默认空,后续 LLM 自动摘要填(v1-4-team-tools proposal)
    """

    sender: str
    body: str
    timestamp: datetime | None = None
    read: bool = False
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转 JSON-serializable dict(JSONL 行内容)。"""
        return {
            "sender": self.sender,
            "body": self.body,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "read": self.read,
            "summary": self.summary,
        }

    def to_json_line(self) -> str:
        """JSONL 单行序列化(无 trailing newline)。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """从 dict 构造 Message(JSONL 行解析用)。"""
        if not isinstance(data, dict):
            raise ValueError(
                f"Message dict 必须是 mapping,得到 {type(data).__name__}"
            )
        ts_raw = data.get("timestamp")
        ts: datetime | None = None
        if ts_raw:
            if isinstance(ts_raw, datetime):
                ts = ts_raw
            elif isinstance(ts_raw, str):
                # ISO 8601 from `datetime.isoformat()`(默认无 tz;dataclass
                # 写入时若带 tz 会带 +00:00 后缀,Pydantic v2 兼容两种)
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except ValueError as e:
                    raise ValueError(
                        f"Message.timestamp 解析失败: {ts_raw!r}: {e}"
                    ) from e
            else:
                raise ValueError(
                    f"Message.timestamp 类型不支持: {type(ts_raw).__name__}"
                )
        return cls(
            sender=str(data.get("sender", "")),
            body=str(data.get("body", "")),
            timestamp=ts,
            read=bool(data.get("read", False)),
            summary=str(data.get("summary", "")),
        )


# Team status / state JSON schema_version
TEAM_SCHEMA_VERSION = "1.0"
MESSAGE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class Team:
    """Team 全量数据 — registry / store / CLI 共享。

    字段语义:

    - `name` — 团队名,经 `TeamNameValidator.validate`
    - `lead` — Lead 名字(默认 `"lead"`,session 主 Agent)
    - `created_at` — UTC datetime
    - `members` — `{member_name: Member}`(member 名唯一)
    - `metadata` — 自由扩展(json-safe)
    """

    name: str
    lead: str = "lead"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    members: dict[str, Member] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        TeamNameValidator.validate(self.name)
        # members 必须是 dict[str, Member];member 名唯一
        if not isinstance(self.members, dict):
            raise ValueError(
                f"Team.members 必须是 dict,得到 {type(self.members).__name__}"
            )
        seen: set[str] = set()
        for mname, member in self.members.items():
            if not isinstance(member, Member):
                raise ValueError(
                    f"Team.members[{mname!r}] 必须是 Member 实例,得到 {type(member).__name__}"
                )
            if mname != member.name:
                raise ValueError(
                    f"Team.members key {mname!r} 与 Member.name "
                    f"{member.name!r} 不一致"
                )
            if mname in seen:
                raise ValueError(f"Team.members 含重复 member 名: {mname!r}")
            seen.add(mname)
        # created_at 强制 UTC(若带 tz);无 tz → 当 UTC 处理
        if self.created_at.tzinfo is None:
            object.__setattr__(
                self,
                "created_at",
                self.created_at.replace(tzinfo=timezone.utc),
            )
        # metadata 必须是 dict
        if not isinstance(self.metadata, dict):
            raise ValueError(
                f"Team.metadata 必须是 dict,得到 {type(self.metadata).__name__}"
            )

    def to_dict(self) -> dict[str, Any]:
        """转 JSON-serializable dict(写入 team.json)。"""
        return {
            "schema_version": TEAM_SCHEMA_VERSION,
            "name": self.name,
            "lead": self.lead,
            "created_at": self.created_at.isoformat(),
            "members": {
                mname: m.to_dict() for mname, m in self.members.items()
            },
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Team:
        """从 dict 构造 Team(读 team.json 用)。"""
        if not isinstance(data, dict):
            raise ValueError(
                f"Team dict 必须是 mapping,得到 {type(data).__name__}"
            )
        schema_version = data.get("schema_version")
        if schema_version != TEAM_SCHEMA_VERSION:
            raise ValueError(
                f"Team.schema_version 不支持: 期望 {TEAM_SCHEMA_VERSION!r},"
                f" 得到 {schema_version!r}"
            )
        name = str(data["name"])
        lead = str(data.get("lead", "lead"))
        created_raw = data.get("created_at")
        if created_raw is None:
            created = datetime.now(timezone.utc)
        elif isinstance(created_raw, datetime):
            created = created_raw
        elif isinstance(created_raw, str):
            try:
                created = datetime.fromisoformat(created_raw)
            except ValueError as e:
                raise ValueError(
                    f"Team.created_at 解析失败: {created_raw!r}: {e}"
                ) from e
        else:
            raise ValueError(
                f"Team.created_at 类型不支持: {type(created_raw).__name__}"
            )
        members_raw = data.get("members") or {}
        if not isinstance(members_raw, dict):
            raise ValueError(
                f"Team.members 必须是 mapping,得到 {type(members_raw).__name__}"
            )
        members = {
            mname: Member.from_dict(mdata) for mname, mdata in members_raw.items()
        }
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError(
                f"Team.metadata 必须是 dict,得到 {type(metadata).__name__}"
            )
        return cls(
            name=name,
            lead=lead,
            created_at=created,
            members=members,
            metadata=dict(metadata),
        )

    def to_json(self, *, indent: int | None = 2) -> str:
        """JSON 序列化(`show` CLI 用 indent,持久化用 None)。"""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            sort_keys=False,
        )

    @classmethod
    def from_json(cls, text: str) -> Team:
        return cls.from_dict(json.loads(text))

    def save(self, path: Path) -> None:
        """原子写 team.json(write-then-rename)。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.to_json(indent=None), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> Team:
        """从 path 读 team.json;失败抛 ValueError。"""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise TeamNotFound(f"team.json 不存在: {path}") from e
        return cls.from_json(text)


# ---------------------------------------------------------------------------
# MemberState(mailbox state.json 持久化用)
# ---------------------------------------------------------------------------


MemberStatus = Literal["offline", "idle", "running", "waiting"]


@dataclass(frozen=True)
class MemberState:
    """Mailbox state.json 的运行时状态。

    字段语义:

    - `status` — `offline` / `idle` / `running` / `waiting`
    - `last_active_ts` — 最后活跃 UTC 时间;None 表示从未活跃
    - `current_task` — 当前任务 ID(共享任务清单);None 表示无任务
    - `backend_pid` — 运行时进程 PID(coroutine 时为 None)
    """

    status: MemberStatus = "offline"
    last_active_ts: datetime | None = None
    current_task: str | None = None
    backend_pid: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "last_active_ts": (
                self.last_active_ts.isoformat() if self.last_active_ts else None
            ),
            "current_task": self.current_task,
            "backend_pid": self.backend_pid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MemberState:
        """从 dict 构造;`None` 或空 dict 走全默认。"""
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise ValueError(
                f"MemberState dict 必须是 mapping,得到 {type(data).__name__}"
            )
        ts_raw = data.get("last_active_ts")
        ts: datetime | None = None
        if ts_raw:
            if isinstance(ts_raw, datetime):
                ts = ts_raw
            elif isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except ValueError as e:
                    raise ValueError(
                        f"MemberState.last_active_ts 解析失败: {ts_raw!r}: {e}"
                    ) from e
            else:
                raise ValueError(
                    f"MemberState.last_active_ts 类型不支持: {type(ts_raw).__name__}"
                )
        pid_raw = data.get("backend_pid")
        pid: int | None = None
        if pid_raw is not None:
            try:
                pid = int(pid_raw)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"MemberState.backend_pid 必须可转 int,得到 {pid_raw!r}"
                ) from e
        status = data.get("status", "offline")
        if status not in ("offline", "idle", "running", "waiting"):
            raise ValueError(f"MemberState.status 非法: {status!r}")
        return cls(
            status=status,
            last_active_ts=ts,
            current_task=(
                str(data["current_task"]) if data.get("current_task") else None
            ),
            backend_pid=pid,
        )


def default_member_state() -> MemberState:
    """新创建 member 时的默认 state.json 内容。"""
    return MemberState(status="offline")


# ---------------------------------------------------------------------------
# Helper:timestamp 自动补(Message 在 append 时 None → now)
# ---------------------------------------------------------------------------


def fill_message_timestamp(msg: Message) -> Message:
    """`Message.timestamp` 为 None 时替换为 UTC now;否则原样返回。"""
    if msg.timestamp is not None:
        return msg
    return replace(msg, timestamp=datetime.now(timezone.utc))


__all__ = [
    "BackendType",
    "Member",
    "MemberAlreadyExists",
    "MemberNotFound",
    "MemberState",
    "MemberStatus",
    "Message",
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
    "TEAM_SCHEMA_VERSION",
    "MESSAGE_SCHEMA_VERSION",
    "default_member_state",
    "fill_message_timestamp",
]