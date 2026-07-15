"""v1.4 Pane Backend — `pane_info.json` 持久化层测试。

覆盖 `openspec/changes/v1-4-team-pane-backend/specs/team-management/spec.md`
中 PaneInfo 持久化 Requirement:

- schema_version 默认 / 自定义
- PaneMemberInfo to_dict / from_dict 字段往返
- PaneInfo save / load 原子写
- 缺字段走默认(None / "")
- 多 member 保存读回
- load 路径不存在 → None
- 损坏 JSON → None(不抛)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.teams.pane_info import (
    PANE_INFO_SCHEMA_VERSION,
    PaneInfo,
    PaneMemberInfo,
)


# ---------------------------------------------------------------------------
# PaneMemberInfo
# ---------------------------------------------------------------------------


class TestPaneMemberInfo:
    """Requirement: PaneMemberInfo 字段往返(to_dict / from_dict)。"""

    def test_defaults(self) -> None:
        m = PaneMemberInfo(backend_type="coroutine")
        assert m.backend_type == "coroutine"
        assert m.pane_identifier == ""
        assert m.pid is None
        assert m.last_active_ts is None

    def test_to_dict_with_pid_and_ts(self) -> None:
        from datetime import datetime, timezone
        ts = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
        m = PaneMemberInfo(
            backend_type="pane-tmux",
            pane_identifier="%0",
            pid=12345,
            last_active_ts=ts,
        )
        d = m.to_dict()
        assert d == {
            "backend_type": "pane-tmux",
            "pane_identifier": "%0",
            "pid": 12345,
            "last_active_ts": "2026-07-15T10:00:00+00:00",
        }

    def test_from_dict_round_trip(self) -> None:
        original = PaneMemberInfo(
            backend_type="pane-iterm2",
            pane_identifier="W42",
            pid=9999,
        )
        d = original.to_dict()
        restored = PaneMemberInfo.from_dict(d)
        assert restored == original

    def test_from_dict_empty_returns_defaults(self) -> None:
        m = PaneMemberInfo.from_dict({})
        assert m.backend_type == "coroutine"
        assert m.pane_identifier == ""
        assert m.pid is None

    def test_from_dict_rejects_bad_backend(self) -> None:
        with pytest.raises(ValueError, match="backend_type"):
            PaneMemberInfo.from_dict({"backend_type": "pane-X"})

    def test_from_dict_rejects_bad_pid(self) -> None:
        with pytest.raises(ValueError, match="pid"):
            PaneMemberInfo.from_dict(
                {"backend_type": "coroutine", "pid": "not-int"}
            )

    def test_from_dict_rejects_non_mapping(self) -> None:
        with pytest.raises(ValueError, match="必须是 mapping"):
            PaneMemberInfo.from_dict("not a dict")  # type: ignore[arg-type]

    def test_to_dict_pid_none(self) -> None:
        m = PaneMemberInfo(backend_type="coroutine", pid=None)
        d = m.to_dict()
        assert d["pid"] is None


# ---------------------------------------------------------------------------
# PaneInfo
# ---------------------------------------------------------------------------


class TestPaneInfo:
    """Requirement: PaneInfo 顶层 schema_version + team + members。"""

    def test_default_empty(self) -> None:
        p = PaneInfo()
        assert p.schema_version == PANE_INFO_SCHEMA_VERSION
        assert p.team == ""
        assert p.members == {}

    def test_empty_factory(self) -> None:
        p = PaneInfo.empty(team="devops")
        assert p.team == "devops"
        assert p.members == {}

    def test_to_dict_with_members(self) -> None:
        p = PaneInfo(
            team="devops",
            members={
                "alice": PaneMemberInfo(backend_type="pane-tmux", pid=100),
                "bob": PaneMemberInfo(backend_type="coroutine"),
            },
        )
        d = p.to_dict()
        assert d["schema_version"] == PANE_INFO_SCHEMA_VERSION
        assert d["team"] == "devops"
        assert d["members"]["alice"]["backend_type"] == "pane-tmux"
        assert d["members"]["alice"]["pid"] == 100
        assert d["members"]["bob"]["backend_type"] == "coroutine"

    def test_from_dict_round_trip(self) -> None:
        original = PaneInfo(
            team="devops",
            members={
                "alice": PaneMemberInfo(
                    backend_type="pane-windows-terminal",
                    pane_identifier="abc-1234",
                    pid=42,
                ),
            },
        )
        d = original.to_dict()
        restored = PaneInfo.from_dict(d)
        assert restored == original

    def test_from_dict_bad_schema_version(self) -> None:
        with pytest.raises(ValueError, match="schema_version"):
            PaneInfo.from_dict({"schema_version": "2.0", "team": "x"})

    def test_from_dict_non_mapping_members(self) -> None:
        with pytest.raises(ValueError, match="members"):
            PaneInfo.from_dict({"members": []})

    def test_from_dict_rejects_non_mapping(self) -> None:
        with pytest.raises(ValueError, match="必须是 mapping"):
            PaneInfo.from_dict("not a dict")  # type: ignore[arg-type]

    def test_to_dict_round_trip_preserves_all_fields(self) -> None:
        from datetime import datetime, timezone
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        p = PaneInfo(
            team="acme",
            members={
                "alice": PaneMemberInfo(
                    backend_type="worktree-coroutine",
                    pane_identifier="",
                    pid=7777,
                    last_active_ts=ts,
                ),
            },
        )
        d = p.to_dict()
        restored = PaneInfo.from_dict(d)
        assert restored.members["alice"].last_active_ts == ts
        assert restored.members["alice"].pid == 7777


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------


class TestPaneInfoSaveLoad:
    """Requirement: pane_info.json 原子写 + 缺文件返 None。"""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        path = tmp_path / "pane_info.json"
        p = PaneInfo.empty(team="devops")
        p.save(path)
        assert path.exists()
        assert path.stat().st_size > 0

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "pane_info.json"
        original = PaneInfo(
            team="devops",
            members={
                "alice": PaneMemberInfo(backend_type="pane-tmux", pid=1234),
                "bob": PaneMemberInfo(backend_type="coroutine"),
            },
        )
        original.save(path)
        loaded = PaneInfo.load(path)
        assert loaded == original

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.json"
        assert PaneInfo.load(path) is None

    def test_load_empty_file_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        assert PaneInfo.load(path) is None

    def test_load_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert PaneInfo.load(path) is None

    def test_save_creates_parent_dir(self, tmp_path: Path) -> None:
        path = tmp_path / "subdir" / "pane_info.json"
        p = PaneInfo.empty(team="devops")
        p.save(path)
        assert path.exists()

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "pane_info.json"
        PaneInfo.empty(team="v1").save(path)
        PaneInfo.empty(team="v2").save(path)
        loaded = PaneInfo.load(path)
        assert loaded is not None
        assert loaded.team == "v2"

    def test_save_writes_valid_json(self, tmp_path: Path) -> None:
        """保存后的文件可被标准 json.loads 解析。"""
        import json
        path = tmp_path / "pane_info.json"
        PaneInfo(
            team="devops",
            members={
                "alice": PaneMemberInfo(
                    backend_type="pane-tmux", pane_identifier="%0", pid=1,
                ),
            },
        ).save(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["team"] == "devops"
        assert data["members"]["alice"]["backend_type"] == "pane-tmux"

    def test_save_no_temp_leftover(self, tmp_path: Path) -> None:
        """save 后无临时文件残留。"""
        path = tmp_path / "pane_info.json"
        PaneInfo.empty(team="devops").save(path)
        # 检查目录内无 .tmp 残留
        leftovers = list(tmp_path.glob("*.tmp.*"))
        assert leftovers == []
