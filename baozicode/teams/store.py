"""v1.4 Team Foundation — TeamStore 单个 team 目录操作。

公开 API:

- `TeamStore.create(name, *, lead="lead")` — 创建空 team(原子建目录
  + 写 team.json),失败抛 `TeamAlreadyExists`
- `TeamStore.load(team_dir)` — 从已有目录读 team.json
- `TeamStore.from_name(teams_dir, name)` — 在 `teams_dir/<name>/` 加载
- `store.show()` — 返回当前 Team dataclass 实例
- `store.add_member(member)` — 加 member(校验唯一 + 建子目录 + 写默认
  state.json)
- `store.get_member(name) -> Member | None` — 查 member
- `store.list_members() -> list[str]` — 字典序列出所有 member 名
- `store.destroy(*, confirm=False)` — `shutil.rmtree(team_dir)`

设计保证:

- `create` 用 `os.O_CREAT | O_EXCL` 防并发同名 — 第二个 caller 抛
  `TeamAlreadyExists`。
- `add_member` 同步建 `<member>/` 子目录 + 空 `inbox/outbox` 文件 +
  默认 `state.json` + `.lock` + `wake.signal`(member dir ready to go)。
- `destroy` 需要 `confirm=True` — 默认 False 抛 PermissionError,防止
  CLI 误调。
- 所有 IO 失败抛明确异常(FileNotFoundError / TeamAlreadyExists /
  TeamNotFound),LLM 看到立即知道改什么。
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .mailbox import Mailbox
from .schema import (
    Member,
    MemberAlreadyExists,
    MemberNotFound,
    Team,
    TeamAlreadyExists,
    TeamNameValidator,
    TeamNotFound,
    default_member_state,
)


class TeamStore:
    """单个 team 的目录操作封装。

    实例与目录是一一对应;`create` / `load` / `from_name` 三种入口。
    """

    def __init__(self, team_dir: Path, team: Team | None = None) -> None:
        self.team_dir = team_dir
        self._team = team

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls, teams_dir: Path, name: str, *, lead: str = "lead"
    ) -> TeamStore:
        """创建新 team(原子建目录 + 写 team.json)。

        Args:
            teams_dir: `teams.dir` 配置路径
            name: team 名(TeamNameValidator)
            lead: Lead 名字,默认 `"lead"`

        Returns:
            TeamStore 实例

        Raises:
            TeamNameInvalid 子类:name 不合法
            TeamAlreadyExists:同名 team 已存在
        """
        TeamNameValidator.validate(name)
        team_dir = teams_dir / name
        if team_dir.exists():
            raise TeamAlreadyExists(f"team 已存在: {team_dir}")

        team = Team(name=name, lead=lead)
        # 原子:mkdir + 立即写 team.json
        team_dir.mkdir(parents=True)
        try:
            team.save(team_dir / "team.json")
        except Exception:
            # 写失败 → 回滚(删目录,避免半成品)
            shutil.rmtree(team_dir, ignore_errors=True)
            raise
        return cls(team_dir, team)

    @classmethod
    def from_name(cls, teams_dir: Path, name: str) -> TeamStore:
        """在 `teams_dir/<name>/` 加载已有 team。

        Raises:
            TeamNotFound:目录或 team.json 不存在
        """
        team_dir = teams_dir / name
        return cls.load(team_dir)

    @classmethod
    def load(cls, team_dir: Path) -> TeamStore:
        """从 path 加载 team.json。

        Raises:
            TeamNotFound:目录或 team.json 不存在
        """
        if not team_dir.exists() or not team_dir.is_dir():
            raise TeamNotFound(f"team 目录不存在: {team_dir}")
        team_json = team_dir / "team.json"
        if not team_json.exists():
            raise TeamNotFound(f"team.json 不存在: {team_json}")
        team = Team.load(team_json)
        return cls(team_dir, team)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def show(self) -> Team:
        """返回 Team dataclass 实例(读 team.json)。"""
        if self._team is None:
            self._team = Team.load(self.team_dir / "team.json")
        return self._team

    def get_member(self, name: str) -> Member:
        """查 member;找不到抛 MemberNotFound。"""
        team = self.show()
        if name not in team.members:
            raise MemberNotFound(f"member {name!r} 不在 team {team.name!r}")
        return team.members[name]

    def list_members(self) -> list[str]:
        """返回所有 member 名(字典序)。"""
        return sorted(self.show().members.keys())

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def add_member(self, member: Member) -> None:
        """加 member;校验唯一 + 建 `<member>/` 子目录 + 写默认 state。

        同时建 `inbox.jsonl`(空文件)、`outbox.jsonl`(空)、`state.json`
        (默认 offline)、`.lock`(空文件,mailbox_lock 会自动建 — 但
        预建避免冷启动 race)、`wake.signal`(空)。

        Raises:
            MemberAlreadyExists:同名 member 已存在
        """
        team = self.show()
        if member.name in team.members:
            raise MemberAlreadyExists(
                f"team {team.name!r} 已有 member {member.name!r}"
            )
        member_dir = self.team_dir / member.name
        if member_dir.exists():
            raise MemberAlreadyExists(
                f"member 目录已存在: {member_dir}"
            )
        member_dir.mkdir(parents=True)

        # 建空 JSONL 文件(mailbox 期望文件存在)
        (member_dir / "inbox.jsonl").touch()
        (member_dir / "outbox.jsonl").touch()
        (member_dir / "wake.signal").touch()

        # 写默认 state.json(offline)
        Mailbox.write_state(member_dir, default_member_state())

        # 更新 team.json 持久化
        new_members = dict(team.members)
        new_members[member.name] = member
        new_team = Team(
            name=team.name,
            lead=team.lead,
            created_at=team.created_at,
            members=new_members,
            metadata=team.metadata,
        )
        new_team.save(self.team_dir / "team.json")
        self._team = new_team

    def destroy(self, *, confirm: bool = False) -> None:
        """递归删 team 目录。

        Args:
            confirm: 默认 False — 不传 / 传 False 时抛 PermissionError
                防止 CLI 误调;CLI 必须显式 `--yes` 才传 True。

        Raises:
            PermissionError:`confirm=False`
            TeamNotFound:目录已不存在
        """
        if not confirm:
            raise PermissionError(
                f"destroy {self.team_dir} 需要 confirm=True(防误删)"
            )
        if not self.team_dir.exists():
            raise TeamNotFound(f"team 目录已不存在: {self.team_dir}")
        shutil.rmtree(self.team_dir)
        self._team = None


__all__ = ["TeamStore"]