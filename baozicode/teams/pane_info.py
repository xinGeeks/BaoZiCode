"""v1.4 Pane Backend — `<teams_dir>/<team>/pane_info.json` 持久化层。

公开 API:

- `PaneInfo` —— frozen dataclass,schema_version + team + 每 member
  的 backend 信息;`save(path)` / `load(path)` 原子读写
- `PaneMemberInfo` —— frozen dataclass,描述单个 member 的派生状
  态(backend_type / pane_identifier / pid / last_active_ts)
- `PANE_INFO_SCHEMA_VERSION` —— `"1.0"`

设计要点:

- 写盘走 write-then-rename,临时文件 `pane_info.json.tmp.<pid>.<rand>`
  + `os.replace` 原子替换
- `load(path)` 找不到 → 返 `PaneInfo.empty(team=...)` 而不是 None,
  调用方少一步 None 检查
- `PaneMemberInfo` 是 BackendManager.spawn_if_offline 写入的最终
  持久化对象;restore_panes() 读回后做 is_alive 校验
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import BackendType

PANE_INFO_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class PaneMemberInfo:
    """单个 member 派生后端的持久化元数据。

    字段语义:

    - `backend_type` — 实际派生用的 backend 字面量
    - `pane_identifier` — backend-specific 句柄(tmux pane_id `%0` /
      iTerm2 window_id / WT tab_uuid);coroutine 时为空
    - `pid` — 派生进程 PID;coroutine 时为 None
    - `last_active_ts` — 上次活跃 UTC 时间
    """

    backend_type: BackendType
    pane_identifier: str = ""
    pid: int | None = None
    last_active_ts: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_type": self.backend_type,
            "pane_identifier": self.pane_identifier,
            "pid": self.pid,
            "last_active_ts": (
                self.last_active_ts.isoformat() if self.last_active_ts else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaneMemberInfo:
        if not isinstance(data, dict):
            raise ValueError(
                f"PaneMemberInfo dict 必须是 mapping,得到 "
                f"{type(data).__name__}"
            )
        backend_type = data.get("backend_type", "coroutine")
        allowed = (
            "pane-tmux", "pane-iterm2", "pane-windows-terminal",
            "coroutine", "worktree-coroutine",
        )
        if backend_type not in allowed:
            raise ValueError(
                f"PaneMemberInfo.backend_type 不合法: {backend_type!r}"
            )
        pid_raw = data.get("pid")
        pid: int | None = None
        if pid_raw is not None:
            try:
                pid = int(pid_raw)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"PaneMemberInfo.pid 必须可转 int,得到 {pid_raw!r}"
                ) from e
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
                        f"PaneMemberInfo.last_active_ts 解析失败: "
                        f"{ts_raw!r}: {e}"
                    ) from e
        return cls(
            backend_type=backend_type,
            pane_identifier=str(data.get("pane_identifier", "")),
            pid=pid,
            last_active_ts=ts,
        )


@dataclass(frozen=True)
class PaneInfo:
    """team 内所有 panes 的持久化元数据。

    字段语义:

    - `schema_version` — 数据格式版本号,写时填 `"1.0"`
    - `team` — 所属 team 名
    - `members` — `{member_name: PaneMemberInfo}`
    """

    schema_version: str = PANE_INFO_SCHEMA_VERSION
    team: str = ""
    members: dict[str, PaneMemberInfo] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "team": self.team,
            "members": {
                mname: m.to_dict() for mname, m in self.members.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaneInfo:
        if not isinstance(data, dict):
            raise ValueError(
                f"PaneInfo dict 必须是 mapping,得到 {type(data).__name__}"
            )
        schema_version = data.get("schema_version", PANE_INFO_SCHEMA_VERSION)
        if schema_version != PANE_INFO_SCHEMA_VERSION:
            raise ValueError(
                f"PaneInfo.schema_version 不支持: 期望 "
                f"{PANE_INFO_SCHEMA_VERSION!r},得到 {schema_version!r}"
            )
        team = str(data.get("team", ""))
        members_raw = data.get("members")
        if members_raw is None:
            members_raw = {}
        if not isinstance(members_raw, dict):
            raise ValueError(
                f"PaneInfo.members 必须是 mapping,得到 "
                f"{type(members_raw).__name__}"
            )
        members = {
            mname: PaneMemberInfo.from_dict(mdata)
            for mname, mdata in members_raw.items()
        }
        return cls(
            schema_version=schema_version,
            team=team,
            members=members,
        )

    def save(self, path: Path) -> None:
        """原子写 pane_info.json(write-then-rename)。

        失败(pane_info.json 不存在目录)由调用方处理;成功覆盖原文件。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        rand = random.randint(0, 9999)
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{rand}")
        try:
            tmp.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    @classmethod
    def load(cls, path: Path) -> PaneInfo | None:
        """读 pane_info.json;不存在或损坏 → None。

        不抛异常 — BackendManager.restore_panes 期望 None 走默认空
        PaneInfo.empty(team=...) 路径。
        """
        if not path.exists() or path.stat().st_size == 0:
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        try:
            return cls.from_dict(data)
        except ValueError:
            return None

    @classmethod
    def empty(cls, team: str) -> PaneInfo:
        """空 PaneInfo 工厂 — 调用方指定 team 名。"""
        return cls(schema_version=PANE_INFO_SCHEMA_VERSION, team=team, members={})


__all__ = [
    "PANE_INFO_SCHEMA_VERSION",
    "PaneInfo",
    "PaneMemberInfo",
]


# 显式暴露 dataclass field 的 type hint;`frozen=True` 时 `replace()` 是
# 修改入口。`datetime.now(timezone.utc)` 不在此导(避免循环)。
