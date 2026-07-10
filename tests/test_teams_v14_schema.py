"""v1.4 Team Foundation — schema 层测试。

覆盖 `openspec/changes/v1-4-team-foundation/specs/team-management/spec.md`
中 schema 相关 acceptance scenario:

- `TeamNameValidator` — accepted / too short / too long / bad char / bad
  start / bad end / double hyphen
- `Team` — frozen / JSON round-trip / member name uniqueness
- `Member` — BackendType enum rejects typo / default approval false /
  workdir auto-derived / from_dict round-trip
- `Message` — timestamp auto-filled / default read false / JSONL line
  parseable
- `MemberState` — read defaults / round-trip
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from baozicode.teams import (
    Member,
    MemberState,
    Message,
    Team,
    TeamNameBadChar,
    TeamNameBadEnd,
    TeamNameBadStart,
    TeamNameDoubleHyphen,
    TeamNameTooLong,
    TeamNameTooShort,
    TeamNameValidator,
    default_member_state,
    fill_message_timestamp,
)
from baozicode.teams.schema import (
    BackendType,
    MESSAGE_SCHEMA_VERSION,
    TEAM_SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# TeamNameValidator
# ---------------------------------------------------------------------------


class TestAcceptedNames:
    """Requirement: Accepted names —— 纯小写字母数字 + 中划线。"""

    @pytest.mark.parametrize(
        "name",
        [
            "devops",
            "acme-team",
            "team-001",
            "a1",
            "abc",
            "team-2-backends",
        ],
    )
    def test_accepted(self, name: str) -> None:
        TeamNameValidator.validate(name)  # 不抛


class TestTooShort:
    """Requirement: Reject empty / too short —— 长度 < 2。"""

    @pytest.mark.parametrize("name", ["", "a"])
    def test_too_short(self, name: str) -> None:
        with pytest.raises(TeamNameTooShort):
            TeamNameValidator.validate(name)


class TestTooLong:
    """Requirement: Reject too long —— 长度 > 30。"""

    def test_too_long(self) -> None:
        with pytest.raises(TeamNameTooLong):
            TeamNameValidator.validate("a" * 31)


class TestBadChar:
    """Requirement: Reject uppercase / special —— 字符集外。"""

    @pytest.mark.parametrize(
        "name",
        [
            "DevOps",         # 大写
            "team_one",       # 下划线
            "team.001",       # 点
            "team/sub",       # 斜杠
            "team\\sub",      # 反斜杠
            "team 001",       # 空格
            "team@001",       # @
            "team!001",       # !
        ],
    )
    def test_bad_char(self, name: str) -> None:
        with pytest.raises(TeamNameBadChar):
            TeamNameValidator.validate(name)


class TestBadStart:
    """Requirement: Reject start —— 数字 / `-` 开头。"""

    @pytest.mark.parametrize("name", ["-team", "001", "9abc"])
    def test_bad_start(self, name: str) -> None:
        with pytest.raises(TeamNameBadStart):
            TeamNameValidator.validate(name)


class TestBadEnd:
    """Requirement: Reject end —— `-` 结尾。"""

    @pytest.mark.parametrize("name", ["team-", "abc-"])
    def test_bad_end(self, name: str) -> None:
        with pytest.raises(TeamNameBadEnd):
            TeamNameValidator.validate(name)


class TestDoubleHyphen:
    """Requirement: Reject double hyphen —— `--` 连续。"""

    @pytest.mark.parametrize("name", ["acme--team", "a--b", "team--"])
    def test_double_hyphen(self, name: str) -> None:
        with pytest.raises(TeamNameDoubleHyphen):
            TeamNameValidator.validate(name)


class TestNonStringRejected:
    """Type 兜底 — 非字符串必须抛 TeamNameBadChar。"""

    @pytest.mark.parametrize("name", [None, 123, [], {}, 1.5])
    def test_non_string(self, name: object) -> None:
        with pytest.raises(TeamNameBadChar):
            TeamNameValidator.validate(name)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Team dataclass
# ---------------------------------------------------------------------------


def _make_member(name: str = "alice") -> Member:
    return Member(name=name, role="backend", backend="coroutine")


class TestTeamFrozen:
    """Requirement: Frozen 阻止 top-level 字段赋值。

    已知限制:frozen dataclass 不深度冻结 dict 字段;
    `team.members["alice"] = X` 不抛(dataclass 不知道 dict 内部变化)。
    工程上靠"构造后不修改" + __post_init__ 校验保证不变性。
    """

    def test_frozen_blocks_field_assignment(self) -> None:
        team = Team(name="devops", members={"alice": _make_member()})
        with pytest.raises(Exception):  # FrozenInstanceError
            team.name = "other"  # type: ignore[misc]

    def test_frozen_does_not_deep_freeze_dict_field(self) -> None:
        """已知限制 — Python frozen dataclass 不递归冻结 dict/mutable 字段。"""
        team = Team(name="devops", members={"alice": _make_member()})
        new_alice = _make_member("alice")
        # 不抛 — dict 字段不走 __setattr__
        team.members["alice"] = new_alice  # type: ignore[index]
        assert team.members["alice"] is new_alice


class TestTeamJSONRoundTrip:
    """Requirement: JSON round-trip — to_dict ↔ from_dict。"""

    def test_minimal_round_trip(self) -> None:
        team = Team(name="devops")
        data = team.to_dict()
        assert data["schema_version"] == TEAM_SCHEMA_VERSION
        assert data["name"] == "devops"
        assert data["lead"] == "lead"
        assert data["members"] == {}
        assert "created_at" in data
        restored = Team.from_dict(data)
        assert restored.name == team.name
        assert restored.lead == team.lead
        assert restored.created_at == team.created_at

    def test_full_round_trip(self) -> None:
        alice = _make_member("alice")
        bob = _make_member("bob")
        ts = datetime(2026, 7, 10, 8, 0, 0, tzinfo=timezone.utc)
        team = Team(
            name="devops",
            lead="lead",
            created_at=ts,
            members={"alice": alice, "bob": bob},
            metadata={"project": "acme"},
        )
        data = team.to_dict()
        assert data["members"]["alice"]["name"] == "alice"
        assert data["members"]["alice"]["backend"] == "coroutine"
        assert data["metadata"] == {"project": "acme"}
        restored = Team.from_dict(data)
        assert restored.members["alice"].name == "alice"
        assert restored.members["bob"].name == "bob"
        assert restored.metadata == {"project": "acme"}

    def test_to_json_pretty(self) -> None:
        team = Team(name="devops", members={"alice": _make_member()})
        text = team.to_json(indent=2)
        parsed = json.loads(text)
        assert parsed["name"] == "devops"
        assert parsed["members"]["alice"]["role"] == "backend"

    def test_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "team.json"
        team = Team(name="devops", members={"alice": _make_member()})
        team.save(path)
        assert path.exists()
        # atomic:无 .tmp 残留
        assert not (tmp_path / "team.json.tmp").exists()
        restored = Team.load(path)
        assert restored.name == "devops"
        assert "alice" in restored.members


class TestTeamMemberUniqueness:
    """Requirement: Member key/name 一致性 — dict key 与 Member.name 必须匹配。

    已知限制:Python dict 字面量自动去重(`{"alice": m1, "alice": m2}`
    只保留 m2),所以"重复 member 名"无法在 dict 输入触发;
    __post_init__ 强制 key 与 Member.name 一致,这是工程上的等同约束。
    """

    def test_member_key_name_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="不一致"):
            Team(
                name="devops",
                members={
                    "alice": _make_member("bob"),  # key 是 alice,member 叫 bob
                },
            )

    def test_dict_literal_dedup_documented(self) -> None:
        """Python dict 字面量自动去重,所以重复 member 名根本写不出来。"""
        # 这一行构造本身就没问题 — Python 把第二个 "alice" 覆盖第一个
        team = Team(
            name="devops",
            members={
                "alice": _make_member("alice"),
                "alice": _make_member("alice"),
            },
        )
        # dict 只有 1 个 alice
        assert len(team.members) == 1
        assert "alice" in team.members

    def test_metadata_must_be_dict(self) -> None:
        with pytest.raises(ValueError, match="metadata"):
            Team(name="devops", metadata="not a dict")  # type: ignore[arg-type]


class TestTeamNameEnforced:
    """Team 构造时也走 TeamNameValidator。"""

    def test_bad_team_name_in_constructor(self) -> None:
        with pytest.raises(TeamNameBadChar):
            Team(name="DevOps")


# ---------------------------------------------------------------------------
# Member dataclass
# ---------------------------------------------------------------------------


class TestMemberBackendType:
    """Requirement: BackendType enum rejects typo。"""

    @pytest.mark.parametrize(
        "backend",
        ["pane-Tmux", "PANE-TMUX", "tmux", "worktree", ""],
    )
    def test_invalid_backend_raises(self, backend: str) -> None:
        with pytest.raises(ValueError, match="backend"):
            Member(name="alice", role="backend", backend=backend)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "backend",
        [
            "pane-tmux",
            "pane-iterm2",
            "pane-windows-terminal",
            "coroutine",
            "worktree-coroutine",
        ],
    )
    def test_valid_backends_accepted(self, backend: BackendType) -> None:
        m = Member(name="alice", role="backend", backend=backend)
        assert m.backend == backend


class TestMemberDefaults:
    """Requirement: Default approval false + workdir auto-derived。"""

    def test_default_approval_false(self) -> None:
        m = Member(name="alice", role="backend", backend="coroutine")
        assert m.requires_approval is False
        assert m.config == {}

    def test_workdir_auto_derived(self) -> None:
        m = Member(name="alice", role="backend", backend="coroutine")
        assert m.workdir == ".worktrees/alice/"

    def test_explicit_workdir_kept(self) -> None:
        m = Member(
            name="alice",
            role="backend",
            workdir="custom/path/",
            backend="coroutine",
        )
        assert m.workdir == "custom/path/"

    def test_name_validated_in_constructor(self) -> None:
        with pytest.raises(TeamNameBadChar):
            Member(name="Alice", role="backend", backend="coroutine")  # 大写


class TestMemberFromDict:
    """Member.from_dict round-trip。"""

    def test_from_dict_minimal(self) -> None:
        m = Member.from_dict(
            {"name": "alice", "role": "backend", "backend": "coroutine"}
        )
        assert m.name == "alice"
        assert m.role == "backend"
        assert m.backend == "coroutine"
        assert m.workdir == ".worktrees/alice/"  # 自动补
        assert m.requires_approval is False

    def test_from_dict_full(self) -> None:
        m = Member.from_dict(
            {
                "name": "alice",
                "role": "frontend",
                "workdir": "custom/",
                "backend": "pane-tmux",
                "requires_approval": True,
                "config": {"session_name": "main"},
            }
        )
        assert m.workdir == "custom/"
        assert m.requires_approval is True
        assert m.config == {"session_name": "main"}


# ---------------------------------------------------------------------------
# Message dataclass
# ---------------------------------------------------------------------------


class TestMessageDefaults:
    """Requirement: Default read false + summary empty。"""

    def test_default_values(self) -> None:
        msg = Message(sender="lead", body="hi alice")
        assert msg.read is False
        assert msg.summary == ""
        assert msg.timestamp is None


class TestMessageTimestampAutoFill:
    """Requirement: Timestamp auto-filled。"""

    def test_fill_message_timestamp_replaces_none(self) -> None:
        msg = Message(sender="lead", body="hi")
        assert msg.timestamp is None
        filled = fill_message_timestamp(msg)
        assert filled.timestamp is not None
        # 替换不破坏原实例(frozen)
        assert msg.timestamp is None
        assert filled.body == "hi"

    def test_fill_message_timestamp_keeps_existing(self) -> None:
        ts = datetime(2026, 7, 10, 8, 0, 0, tzinfo=timezone.utc)
        msg = Message(sender="lead", body="hi", timestamp=ts)
        filled = fill_message_timestamp(msg)
        assert filled.timestamp == ts
        assert filled is msg  # 同一对象


class TestMessageJSONLFormat:
    """Requirement: JSONL line parseable。"""

    def test_to_json_line_parseable(self) -> None:
        ts = datetime(2026, 7, 10, 8, 0, 0, tzinfo=timezone.utc)
        msg = Message(
            sender="lead", body="hi", timestamp=ts, read=True, summary="greet"
        )
        line = msg.to_json_line()
        assert "\n" not in line  # 单行
        parsed = json.loads(line)
        restored = Message.from_dict(parsed)
        assert restored.sender == "lead"
        assert restored.body == "hi"
        assert restored.timestamp == ts
        assert restored.read is True
        assert restored.summary == "greet"

    def test_from_dict_handles_missing_timestamp(self) -> None:
        m = Message.from_dict({"sender": "x", "body": "y", "read": False, "summary": ""})
        assert m.timestamp is None

    def test_from_dict_handles_invalid_timestamp(self) -> None:
        with pytest.raises(ValueError, match="timestamp"):
            Message.from_dict({"sender": "x", "body": "y", "timestamp": "not-iso"})


# ---------------------------------------------------------------------------
# MemberState
# ---------------------------------------------------------------------------


class TestMemberStateDefaults:
    """MemberState 读时缺字段填默认。"""

    def test_from_none_returns_default(self) -> None:
        s = MemberState.from_dict(None)
        assert s.status == "offline"
        assert s.last_active_ts is None
        assert s.current_task is None
        assert s.backend_pid is None

    def test_from_empty_dict_returns_default(self) -> None:
        s = MemberState.from_dict({})
        assert s.status == "offline"

    def test_default_factory(self) -> None:
        s = default_member_state()
        assert s.status == "offline"


class TestMemberStateRoundTrip:
    """MemberState.to_dict ↔ from_dict round-trip。"""

    def test_full_round_trip(self) -> None:
        ts = datetime(2026, 7, 10, 8, 0, 0, tzinfo=timezone.utc)
        s = MemberState(
            status="running",
            last_active_ts=ts,
            current_task="task-001",
            backend_pid=12345,
        )
        data = s.to_dict()
        restored = MemberState.from_dict(data)
        assert restored.status == "running"
        assert restored.last_active_ts == ts
        assert restored.current_task == "task-001"
        assert restored.backend_pid == 12345

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="status"):
            MemberState.from_dict({"status": "unknown"})

    def test_invalid_backend_pid_rejected(self) -> None:
        with pytest.raises(ValueError, match="backend_pid"):
            MemberState.from_dict({"backend_pid": "not-a-number"})


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_schema_version_constants() -> None:
    """Schema 版本号固定,后续迁移靠这个判断。"""
    assert TEAM_SCHEMA_VERSION == "1.0"
    assert MESSAGE_SCHEMA_VERSION == "1.0"