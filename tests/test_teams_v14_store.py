"""v1.4 Team Foundation — TeamStore + TeamsRegistry 测试。

覆盖 `openspec/changes/v1-4-team-foundation/specs/team-management/spec.md`
中 store / registry 相关 acceptance scenario:

- `TeamStore.create` / `load` / `from_name` happy + 同名冲突
- `TeamStore.add_member` happy + 同名冲突 + 自动建子目录
- `TeamStore.destroy` 默认拒绝 / confirm=True 通过 / 目录不存在
- `TeamsRegistry.bootstrap` + `list_teams` + `get` + `create_team` +
  `delete_team`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from baozicode.teams import (
    Member,
    MemberAlreadyExists,
    MemberNotFound,
    TeamAlreadyExists,
    TeamNameBadChar,
    TeamNotFound,
    TeamStore,
    TeamsRegistry,
)


class _StubConfig:
    """测试用 AppConfig stub — 只暴露 teams 属性。"""

    def __init__(self, teams_dir: str | None) -> None:
        if teams_dir is None:
            self.teams = None
        else:
            self.teams = _StubTeamsConfig(teams_dir)


class _StubTeamsConfig:
    def __init__(self, dir_: str) -> None:
        self.dir = dir_


# ---------------------------------------------------------------------------
# TeamStore.create
# ---------------------------------------------------------------------------


class TestCreateTeam:
    def test_create_happy(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        assert store.team_dir == tmp_path / "devops"
        assert (store.team_dir / "team.json").exists()
        team = store.show()
        assert team.name == "devops"
        assert team.lead == "lead"

    def test_create_duplicate_raises(self, tmp_path: Path) -> None:
        TeamStore.create(tmp_path, "devops")
        with pytest.raises(TeamAlreadyExists):
            TeamStore.create(tmp_path, "devops")

    def test_create_invalid_name_raises(self, tmp_path: Path) -> None:
        with pytest.raises(TeamNameBadChar):
            TeamStore.create(tmp_path, "DevOps")


# ---------------------------------------------------------------------------
# TeamStore.load / from_name
# ---------------------------------------------------------------------------


class TestLoadTeam:
    def test_load_existing(self, tmp_path: Path) -> None:
        TeamStore.create(tmp_path, "devops")
        store = TeamStore.load(tmp_path / "devops")
        assert store.show().name == "devops"

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(TeamNotFound):
            TeamStore.load(tmp_path / "ghost")

    def test_from_name_happy(self, tmp_path: Path) -> None:
        TeamStore.create(tmp_path, "devops")
        store = TeamStore.from_name(tmp_path, "devops")
        assert store.show().name == "devops"

    def test_from_name_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(TeamNotFound):
            TeamStore.from_name(tmp_path, "ghost")


# ---------------------------------------------------------------------------
# TeamStore.add_member
# ---------------------------------------------------------------------------


def _alice() -> Member:
    return Member(name="alice", role="backend", backend="coroutine")


def _bob() -> Member:
    return Member(name="bob", role="frontend", backend="pane-tmux")


class TestAddMember:
    def test_add_member_creates_dir(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        store.add_member(_alice())
        member_dir = store.team_dir / "alice"
        assert member_dir.exists()
        # 必备文件全在
        assert (member_dir / "inbox.jsonl").exists()
        assert (member_dir / "outbox.jsonl").exists()
        assert (member_dir / "state.json").exists()
        assert (member_dir / "wake.signal").exists()
        # team.json 已持久化
        assert "alice" in store.show().members

    def test_add_member_default_state(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        store.add_member(_alice())
        from baozicode.teams import Mailbox

        state = Mailbox.read_state(store.team_dir / "alice")
        assert state.status == "offline"

    def test_add_member_duplicate_raises(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        store.add_member(_alice())
        with pytest.raises(MemberAlreadyExists):
            store.add_member(_alice())

    def test_add_multiple_members(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        store.add_member(_alice())
        store.add_member(_bob())
        assert set(store.list_members()) == {"alice", "bob"}

    def test_get_member(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        store.add_member(_alice())
        m = store.get_member("alice")
        assert m.name == "alice"
        assert m.role == "backend"

    def test_get_member_not_found_raises(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        with pytest.raises(MemberNotFound):
            store.get_member("ghost")


# ---------------------------------------------------------------------------
# TeamStore.destroy
# ---------------------------------------------------------------------------


class TestDestroy:
    def test_destroy_requires_confirm(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        with pytest.raises(PermissionError):
            store.destroy()  # 默认 confirm=False

    def test_destroy_with_confirm(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        store.destroy(confirm=True)
        assert not store.team_dir.exists()

    def test_destroy_missing_raises(self, tmp_path: Path) -> None:
        store = TeamStore.create(tmp_path, "devops")
        store.destroy(confirm=True)
        # 再 destroy → TeamNotFound
        with pytest.raises(TeamNotFound):
            store.destroy(confirm=True)


# ---------------------------------------------------------------------------
# TeamsRegistry
# ---------------------------------------------------------------------------


class TestRegistryBootstrap:
    def test_bootstrap_creates_dir(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        assert reg.teams_dir == tmp_path / "teams"
        assert reg.teams_dir.exists()

    def test_bootstrap_none_uses_default(self) -> None:
        cfg = _StubConfig(None)
        reg = TeamsRegistry.bootstrap(cfg)
        # 默认 ~/.config/baozicode/teams/
        assert reg.teams_dir.name == "teams"
        assert reg.teams_dir.parent.name == "baozicode"


class TestRegistryList:
    def test_list_empty(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        assert reg.list_teams() == []

    def test_list_after_creates(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        reg.create_team("devops")
        reg.create_team("acme")
        assert reg.list_teams() == ["acme", "devops"]  # 字典序

    def test_list_skips_dirs_without_team_json(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        reg.create_team("devops")
        # 加一个无效目录(无 team.json)
        (reg.teams_dir / "garbage").mkdir()
        assert reg.list_teams() == ["devops"]


class TestRegistryGet:
    def test_get_existing(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        reg.create_team("devops")
        store = reg.get("devops")
        assert store is not None
        assert store.show().name == "devops"

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        assert reg.get("ghost") is None


class TestRegistryCreateDelete:
    def test_create_via_registry(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        store = reg.create_team("devops")
        assert store.team_dir.exists()

    def test_create_duplicate_via_registry_raises(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        reg.create_team("devops")
        with pytest.raises(TeamAlreadyExists):
            reg.create_team("devops")

    def test_delete_via_registry(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        reg.create_team("devops")
        reg.delete_team("devops", confirm=True)
        assert reg.list_teams() == []

    def test_delete_missing_via_registry_raises(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        with pytest.raises(TeamNotFound):
            reg.delete_team("ghost", confirm=True)

    def test_delete_default_requires_confirm(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)
        reg.create_team("devops")
        with pytest.raises(PermissionError):
            reg.delete_team("devops")  # 没 confirm=True


# ---------------------------------------------------------------------------
# TeamStore + Registry 集成
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_full_workflow(self, tmp_path: Path) -> None:
        cfg = _StubConfig(str(tmp_path / "teams"))
        reg = TeamsRegistry.bootstrap(cfg)

        # Create team
        store = reg.create_team("devops")
        # Add members
        store.add_member(_alice())
        store.add_member(_bob())
        # Reload from disk
        fresh = TeamStore.from_name(reg.teams_dir, "devops")
        assert set(fresh.list_members()) == {"alice", "bob"}
        # Delete
        reg.delete_team("devops", confirm=True)
        assert reg.get("devops") is None
        assert not reg.teams_dir.joinpath("devops").exists()