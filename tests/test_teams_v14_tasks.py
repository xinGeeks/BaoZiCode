"""v1.4 Team Tools — tasks.jsonl 文件层测试。

覆盖 `openspec/changes/v1-4-team-tools/specs/team-management/spec.md` 中
shared task list 相关 acceptance scenario:

- `Task` frozen dataclass 字段校验 / JSON round-trip / 6 status 字面量
- `Tasks.append` happy / 并发锁 / stale 锁偷取
- `Tasks.read_all` 坏行跳过 / 空文件
- `Tasks.update_status` happy / `started_at` 首次填 / `completed_at`
  terminal 填 / 找不到返 False
- `Tasks.find_ready` 全 deps done / 失败 dep blocker / 不存在 dep
- `Tasks.detect_cycles` 自环 / 二元环 / 三元环 / 无环
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from baozicode.teams.tasks import Task, TaskCycleError, Tasks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def team_dir(tmp_path: Path) -> Path:
    """一个临时 team 目录。"""
    d = tmp_path / "devops"
    d.mkdir()
    return d


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Task dataclass 字段校验
# ---------------------------------------------------------------------------


class TestTaskFields:
    """Requirement: Task frozen dataclass + 字段校验。"""

    def test_default_pending(self) -> None:
        t = Task(id="3f4a7c01", body="write health check")
        assert t.status == "pending"
        assert t.depends_on == ()
        assert t.assignee is None
        assert t.started_at is None
        assert t.completed_at is None
        assert t.error is None

    def test_frozen_immutable(self) -> None:
        t = Task(id="3f4a7c01", body="x")
        with pytest.raises((AttributeError, Exception)):
            t.status = "done"  # type: ignore[misc]

    def test_all_six_statuses_accepted(self) -> None:
        for s in ("pending", "ready", "in_progress", "done", "failed", "canceled"):
            t = Task(id="3f4a7c01", body="x", status=s)
            assert t.status == s

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValueError, match="Task.status 非法"):
            Task(id="3f4a7c01", body="x", status="running")  # type: ignore[arg-type]

    def test_depends_on_must_be_tuple(self) -> None:
        with pytest.raises(ValueError, match="Task.depends_on 必须是 tuple"):
            Task(id="3f4a7c01", body="x", depends_on=["a"])  # type: ignore[arg-type]

    def test_depends_on_non_str_rejected(self) -> None:
        with pytest.raises(ValueError, match="Task.depends_on 含非 str"):
            Task(id="3f4a7c01", body="x", depends_on=(123,))  # type: ignore[arg-type]

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="Task.id 不能为空"):
            Task(id="", body="x")

    def test_naive_datetime_normalized_to_utc(self) -> None:
        naive = datetime(2026, 7, 10, 8, 0, 0)
        t = Task(id="3f4a7c01", body="x", created_at=naive)
        assert t.created_at.tzinfo is timezone.utc


class TestTaskJsonRoundTrip:
    """Requirement: Task JSONL 序列化。"""

    def test_to_dict_round_trip(self) -> None:
        t = Task(
            id="3f4a7c01",
            body="write health check",
            status="in_progress",
            depends_on=("t-001", "t-002"),
            assignee="alice",
        )
        d = t.to_dict()
        assert d["id"] == "3f4a7c01"
        assert d["body"] == "write health check"
        assert d["status"] == "in_progress"
        assert d["depends_on"] == ["t-001", "t-002"]  # 转 list
        assert d["assignee"] == "alice"

        t2 = Task.from_dict(d)
        assert t2.id == t.id
        assert t2.body == t.body
        assert t2.status == t.status
        assert t2.depends_on == t.depends_on
        assert t2.assignee == t.assignee

    def test_to_json_line_no_trailing_newline(self) -> None:
        t = Task(id="3f4a7c01", body="x")
        line = t.to_json_line()
        assert not line.endswith("\n")
        # 反序列化也能跑
        data = json.loads(line)
        assert data["id"] == "3f4a7c01"

    def test_from_dict_preserves_isoformat(self) -> None:
        t = Task(id="3f4a7c01", body="x")
        d = t.to_dict()
        # 字符串 ISO 格式
        assert isinstance(d["created_at"], str)
        # from_dict 解析回 datetime
        t2 = Task.from_dict(d)
        assert isinstance(t2.created_at, datetime)


# ---------------------------------------------------------------------------
# Tasks.append
# ---------------------------------------------------------------------------


class TestAppendHappy:
    """Requirement: Atomic append creates tasks.jsonl。"""

    def test_append_creates_file(self, team_dir: Path) -> None:
        t = Task(id="3f4a7c01", body="write health check")
        Tasks.append(team_dir, t)
        target = team_dir / "tasks.jsonl"
        assert target.exists()
        lines = target.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["id"] == "3f4a7c01"

    def test_multiple_appends_in_order(self, team_dir: Path) -> None:
        for i in range(3):
            Tasks.append(team_dir, Task(id=f"3f4a7c0{i}", body=f"task {i}"))
        all_tasks = Tasks.read_all(team_dir)
        assert [t.id for t in all_tasks] == ["3f4a7c00", "3f4a7c01", "3f4a7c02"]

    def test_no_tmp_residue_after_append(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="3f4a7c01", body="x"))
        tmps = list(team_dir.glob(".tasks.jsonl.*"))
        assert tmps == []


class TestAppendStaleLock:
    """Requirement: Stale lock stolen — 旧 lock 自动清掉。"""

    def test_stale_lock_stolen(self, team_dir: Path) -> None:
        # 模拟旧 lock(35 秒前)
        lock = team_dir / ".tasks.lock"
        lock.write_text("99999\n", encoding="utf-8")
        old_time = time.time() - 35
        os.utime(lock, (old_time, old_time))

        # 应直接成功(stale 偷锁)
        Tasks.append(team_dir, Task(id="3f4a7c01", body="x"))
        assert (team_dir / "tasks.jsonl").exists()


# ---------------------------------------------------------------------------
# Tasks.read_all
# ---------------------------------------------------------------------------


class TestReadAll:
    """Requirement: read_all returns list[Task];坏行跳过;空文件 → []。"""

    def test_empty_dir_returns_empty(self, team_dir: Path) -> None:
        assert Tasks.read_all(team_dir) == []

    def test_zero_byte_returns_empty(self, team_dir: Path) -> None:
        (team_dir / "tasks.jsonl").write_text("", encoding="utf-8")
        assert Tasks.read_all(team_dir) == []

    def test_skip_bad_lines(self, team_dir: Path) -> None:
        lines = [
            json.dumps({"id": "a", "body": "ok", "status": "pending"}),
            "{not json",
            json.dumps({"id": "b", "body": "ok2", "status": "pending"}),
        ]
        (team_dir / "tasks.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        result = Tasks.read_all(team_dir)
        assert [t.id for t in result] == ["a", "b"]

    def test_skip_bad_lines_strict_raises(self, team_dir: Path) -> None:
        (team_dir / "tasks.jsonl").write_text(
            "{bad\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="JSON 解析失败"):
            Tasks.read_all(team_dir, skip_bad_lines=False)


# ---------------------------------------------------------------------------
# Tasks.update_status
# ---------------------------------------------------------------------------


class TestUpdateStatusHappy:
    """Requirement: update_status 改字段 + started_at / completed_at。"""

    def test_update_status_basic(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="3f4a7c01", body="x"))
        ok = Tasks.update_status(team_dir, "3f4a7c01", "in_progress", assignee="alice")
        assert ok is True

        tasks = Tasks.read_all(team_dir)
        assert tasks[0].status == "in_progress"
        assert tasks[0].assignee == "alice"

    def test_started_at_set_on_first_in_progress(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="3f4a7c01", body="x"))
        before = _utc_now()
        Tasks.update_status(team_dir, "3f4a7c01", "in_progress")
        after = _utc_now()

        tasks = Tasks.read_all(team_dir)
        sa = tasks[0].started_at
        assert sa is not None
        if sa.tzinfo is None:
            sa = sa.replace(tzinfo=timezone.utc)
        assert before <= sa <= after

    def test_started_at_not_overwritten_on_second_in_progress(
        self, team_dir: Path
    ) -> None:
        Tasks.append(team_dir, Task(id="3f4a7c01", body="x"))
        Tasks.update_status(team_dir, "3f4a7c01", "in_progress")
        first = Tasks.read_all(team_dir)[0].started_at

        # 第二次 in_progress 不应覆盖
        Tasks.update_status(team_dir, "3f4a7c01", "in_progress")
        second = Tasks.read_all(team_dir)[0].started_at
        assert second == first

    def test_completed_at_set_on_terminal(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="3f4a7c01", body="x"))
        Tasks.update_status(team_dir, "3f4a7c01", "done")
        tasks = Tasks.read_all(team_dir)
        assert tasks[0].completed_at is not None

    def test_completed_at_set_on_failed_with_error(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="3f4a7c01", body="x"))
        Tasks.update_status(
            team_dir, "3f4a7c01", "failed", error="network timeout"
        )
        tasks = Tasks.read_all(team_dir)
        assert tasks[0].status == "failed"
        assert tasks[0].error == "network timeout"
        assert tasks[0].completed_at is not None

    def test_update_status_not_found_returns_false(self, team_dir: Path) -> None:
        ok = Tasks.update_status(team_dir, "nonexistent", "done")
        assert ok is False

    def test_update_status_atomic_no_tmp_residue(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="3f4a7c01", body="x"))
        Tasks.update_status(team_dir, "3f4a7c01", "done")
        tmps = list(team_dir.glob(".tasks.jsonl.*"))
        assert tmps == []


# ---------------------------------------------------------------------------
# Tasks.find_ready
# ---------------------------------------------------------------------------


class TestFindReady:
    """Requirement: find_ready 拓扑门控 — 全部 deps done 才 ready。"""

    def test_no_deps_is_ready(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="t-001", body="x"))
        ready = Tasks.find_ready(team_dir)
        assert [t.id for t in ready] == ["t-001"]

    def test_dep_done_makes_ready(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.append(team_dir, Task(id="t-002", body="b", depends_on=("t-001",)))

        # t-001 还没 done → t-002 不在 ready
        ready = Tasks.find_ready(team_dir)
        assert [t.id for t in ready] == ["t-001"]

        # 标 t-001 done
        Tasks.update_status(team_dir, "t-001", "done")
        ready = Tasks.find_ready(team_dir)
        assert [t.id for t in ready] == ["t-002"]

    def test_dep_failed_blocks(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.append(team_dir, Task(id="t-003", body="c", depends_on=("t-001",)))
        Tasks.update_status(team_dir, "t-001", "failed", error="boom")
        ready = Tasks.find_ready(team_dir)
        assert [t.id for t in ready] == []  # failed 阻塞

    def test_dep_canceled_passes(self, team_dir: Path) -> None:
        """canceled = 跳过(语义上视为 satisfied)。"""
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.append(team_dir, Task(id="t-002", body="b", depends_on=("t-001",)))
        Tasks.update_status(team_dir, "t-001", "canceled")
        ready = Tasks.find_ready(team_dir)
        assert [t.id for t in ready] == ["t-002"]

    def test_dep_pending_blocks(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.append(team_dir, Task(id="t-002", body="b", depends_on=("t-001",)))
        Tasks.update_status(team_dir, "t-001", "in_progress")
        ready = Tasks.find_ready(team_dir)
        assert [t.id for t in ready] == []  # in_progress 不算 done

    def test_missing_dep_blocks(self, team_dir: Path) -> None:
        """deps 引用不存在的 task → 不算 satisfied(不做 cycle,detect_cycles 单独负责)。"""
        Tasks.append(
            team_dir, Task(id="t-002", body="b", depends_on=("nonexistent",))
        )
        ready = Tasks.find_ready(team_dir)
        assert ready == []

    def test_in_progress_tasks_not_ready(self, team_dir: Path) -> None:
        """find_ready 只看 pending。"""
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.update_status(team_dir, "t-001", "in_progress")
        ready = Tasks.find_ready(team_dir)
        assert ready == []


# ---------------------------------------------------------------------------
# Tasks.detect_cycles
# ---------------------------------------------------------------------------


class TestDetectCycles:
    """Requirement: detect_cycles 返 list[list[str]] 环路径。"""

    def test_no_cycles_returns_empty(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.append(team_dir, Task(id="t-002", body="b", depends_on=("t-001",)))
        Tasks.append(team_dir, Task(id="t-003", body="c", depends_on=("t-002",)))
        cycles = Tasks.detect_cycles(team_dir)
        assert cycles == []

    def test_self_loop_detected(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="t-001", body="a", depends_on=("t-001",)))
        cycles = Tasks.detect_cycles(team_dir)
        assert len(cycles) >= 1
        # 自环:[t-001]
        assert any("t-001" in c for c in cycles)

    def test_two_cycle_detected(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="t-001", body="a", depends_on=("t-002",)))
        Tasks.append(team_dir, Task(id="t-002", body="b", depends_on=("t-001",)))
        cycles = Tasks.detect_cycles(team_dir)
        assert len(cycles) >= 1
        cycle_sets = [set(c) for c in cycles]
        assert any({"t-001", "t-002"} <= s for s in cycle_sets)

    def test_three_cycle_detected(self, team_dir: Path) -> None:
        Tasks.append(team_dir, Task(id="t-001", body="a", depends_on=("t-002",)))
        Tasks.append(team_dir, Task(id="t-002", body="b", depends_on=("t-003",)))
        Tasks.append(team_dir, Task(id="t-003", body="c", depends_on=("t-001",)))
        cycles = Tasks.detect_cycles(team_dir)
        assert len(cycles) >= 1
        cycle_sets = [set(c) for c in cycles]
        assert any({"t-001", "t-002", "t-003"} <= s for s in cycle_sets)

    def test_diamond_no_cycle(self, team_dir: Path) -> None:
        """菱形: t-001 → t-002 + t-003 → t-004 — 不是环。"""
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.append(
            team_dir,
            Task(id="t-002", body="b", depends_on=("t-001",)),
        )
        Tasks.append(
            team_dir,
            Task(id="t-003", body="c", depends_on=("t-001",)),
        )
        Tasks.append(
            team_dir,
            Task(id="t-004", body="d", depends_on=("t-002", "t-003")),
        )
        cycles = Tasks.detect_cycles(team_dir)
        assert cycles == []


# ---------------------------------------------------------------------------
# TaskCycleError(占位,后面 team_task_create 端用)
# ---------------------------------------------------------------------------


class TestTaskCycleError:
    """Error 类型当前由 callers(团队工具)用,本节只验证异常类存在。"""

    def test_exception_subclass(self) -> None:
        assert issubclass(TaskCycleError, ValueError)

    def test_can_raise(self) -> None:
        with pytest.raises(TaskCycleError, match="cycle"):
            raise TaskCycleError("self cycle")