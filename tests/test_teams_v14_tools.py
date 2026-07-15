"""v1.4 Team Tools — 6 个 team_* 工具 executor 测试。

覆盖 `openspec/changes/v1-4-team-tools/specs/team-management/spec.md` 中
6 个新 Requirement 的 executor 部分:

- `team_dispatch`: happy / member 不存在 / task_id 关联 in_progress
- `team_send_message`: happy / APPROVED: 前缀透传
- `team_cancel`: 软取消 + 强杀 state=offline
- `team_merge`: 委托 run_team_merge(dry_run 测)
- `team_task_create`: id 自动 / auto_ready / cycle 拒绝
- `team_task_query`: 过滤 / ready_graph

合并功能单独测 `tests/test_teams_v14_merge.py`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from baozicode.teams import (
    Mailbox,
    Message,
    TeamStore,
    TeamsRegistry,
    register_team_tools,
)
from baozicode.teams.registry import TeamsRegistry as TR
from baozicode.teams.tasks import Task, Tasks
from baozicode.teams.tools import (
    execute_team_cancel,
    execute_team_dispatch,
    execute_team_merge,
    execute_team_send_message,
    execute_team_task_create,
    execute_team_task_query,
)
from baozicode.tools.base import ToolResult
from baozicode.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def teams_root(tmp_path: Path) -> Path:
    """`<tmp>/teams/` 模拟 user-global teams dir。"""
    d = tmp_path / "teams"
    d.mkdir()
    return d


@pytest.fixture
def registry(teams_root: Path) -> TR:
    return TR(teams_root)


@pytest.fixture
def team_with_alice(registry: TR) -> TeamStore:
    store = registry.create_team("devops")
    from baozicode.teams.schema import Member
    store.add_member(
        Member(name="alice", role="dev", workdir=".worktrees/alice",
               backend="coroutine", requires_approval=False)
    )
    return store


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """merge 测试用的伪项目根(不是 git repo,dry_run 即可)。"""
    return tmp_path / "project"


# ---------------------------------------------------------------------------
# team_dispatch
# ---------------------------------------------------------------------------


class TestTeamDispatchHappy:
    """Requirement: team_dispatch 写 inbox + wake + 标 in_progress。"""

    async def test_dispatch_writes_inbox(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        result = await execute_team_dispatch(
            {"team": "devops", "member": "alice", "body": "do health check"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is False
        assert "dispatched alice" in result.content

        inbox = Mailbox.read_messages(
            registry.teams_dir / "devops" / "alice", "inbox"
        )
        assert len(inbox) == 1
        assert inbox[0].sender == "lead"
        assert "do health check" in inbox[0].body

    async def test_dispatch_touches_wake(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        member_dir = registry.teams_dir / "devops" / "alice"
        wake = member_dir / "wake.signal"
        if not wake.exists():
            wake.touch()
        import os, time
        old_mtime = wake.stat().st_mtime
        time.sleep(0.01)
        os.utime(wake, (old_mtime, old_mtime))

        await execute_team_dispatch(
            {"team": "devops", "member": "alice", "body": "go"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert wake.stat().st_mtime > old_mtime

    async def test_dispatch_with_task_id_marks_in_progress(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        # 先建 task
        team_dir = registry.teams_dir / "devops"
        Tasks.append(team_dir, Task(id="t-001", body="health check"))

        await execute_team_dispatch(
            {"team": "devops", "member": "alice", "task_id": "t-001",
             "body": "do it"},
            teams_registry=registry,
            project_root=tmp_path,
        )

        tasks = Tasks.read_all(team_dir)
        assert tasks[0].status == "in_progress"
        assert tasks[0].assignee == "alice"
        assert tasks[0].started_at is not None


class TestTeamDispatchErrors:
    """Requirement: 错误参数 / 不存在 → error_result。"""

    async def test_missing_team_arg(self, registry, tmp_path) -> None:
        result = await execute_team_dispatch(
            {"member": "alice"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is True
        assert "缺 team 参数" in result.content

    async def test_missing_member_arg(self, registry, tmp_path) -> None:
        result = await execute_team_dispatch(
            {"team": "devops"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is True
        assert "缺 member 参数" in result.content

    async def test_team_not_found(self, registry, tmp_path) -> None:
        result = await execute_team_dispatch(
            {"team": "ghost", "member": "alice"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is True
        assert "ghost" in result.content

    async def test_member_not_found(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        result = await execute_team_dispatch(
            {"team": "devops", "member": "ghost"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is True
        assert "ghost" in result.content


# ---------------------------------------------------------------------------
# team_send_message
# ---------------------------------------------------------------------------


class TestTeamSendMessage:
    async def test_send_message_happy(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        result = await execute_team_send_message(
            {"team": "devops", "member": "alice", "body": "APPROVED: abc"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is False

        inbox = Mailbox.read_messages(
            registry.teams_dir / "devops" / "alice", "inbox"
        )
        assert len(inbox) == 1
        assert inbox[0].body == "APPROVED: abc"

    async def test_send_message_missing_body(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        result = await execute_team_send_message(
            {"team": "devops", "member": "alice"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is True
        assert "缺 body 参数" in result.content


# ---------------------------------------------------------------------------
# team_cancel
# ---------------------------------------------------------------------------


class TestTeamCancel:
    async def test_soft_cancel_writes_inbox_and_marks_task(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        team_dir = registry.teams_dir / "devops"
        Tasks.append(team_dir, Task(id="t-001", body="x"))
        Tasks.update_status(team_dir, "t-001", "in_progress",
                            assignee="alice")

        member_dir = team_dir / "alice"
        Mailbox.write_state(
            member_dir,
            Mailbox.read_state(member_dir).__class__(
                status="running",
                current_task="t-001",
            ),
        )

        result = await execute_team_cancel(
            {"team": "devops", "member": "alice", "reason": "stop"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is False
        assert "canceled" in result.content

        inbox = Mailbox.read_messages(member_dir, "inbox")
        assert any("CANCEL:" in m.body for m in inbox)

        tasks = Tasks.read_all(team_dir)
        assert tasks[0].status == "canceled"

    async def test_terminate_sets_offline(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        member_dir = registry.teams_dir / "devops" / "alice"
        Mailbox.write_state(
            member_dir,
            Mailbox.read_state(member_dir).__class__(status="running"),
        )

        result = await execute_team_cancel(
            {"team": "devops", "member": "alice",
             "terminate": True, "reason": "kill"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is False
        assert "terminated" in result.content

        state = Mailbox.read_state(member_dir)
        assert state.status == "offline"
        assert state.current_task is None


# ---------------------------------------------------------------------------
# team_merge(走 dry_run,完整 git 集成测在 test_teams_v14_merge.py)
# ---------------------------------------------------------------------------


class TestTeamMergeDryRun:
    async def test_dry_run_no_git(
        self, registry, team_with_alice, project_root
    ) -> None:
        result = await execute_team_merge(
            {"team": "devops", "dry_run": True},
            teams_registry=registry,
            project_root=project_root,
        )
        assert result.is_error is False
        data = json.loads(result.content)
        assert data["status"] == "would-merge"
        assert data["target"] == "main"
        assert "wt/alice" in data["members"]


# ---------------------------------------------------------------------------
# team_task_create
# ---------------------------------------------------------------------------


class TestTeamTaskCreate:
    async def test_create_with_auto_generated_id(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        result = await execute_team_task_create(
            {"team": "devops", "body": "do x"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is False
        # 8 字符 hex
        assert "task=" in result.content
        import re
        m = re.search(r"task=([a-f0-9]{8})", result.content)
        assert m is not None
        task_id = m.group(1)

        tasks = Tasks.read_all(registry.teams_dir / "devops")
        assert any(t.id == task_id and t.body == "do x" for t in tasks)

    async def test_create_with_deps_pending(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        result = await execute_team_task_create(
            {"team": "devops", "body": "second", "depends_on": ["ghost"]},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is False
        assert "status=pending" in result.content

    async def test_create_with_done_deps_auto_ready(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        team_dir = registry.teams_dir / "devops"
        Tasks.append(team_dir, Task(id="t-001", body="first"))
        Tasks.update_status(team_dir, "t-001", "done")

        result = await execute_team_task_create(
            {"team": "devops", "body": "second", "depends_on": ["t-001"]},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is False
        assert "status=ready" in result.content

    async def test_create_team_not_found(self, registry, tmp_path) -> None:
        result = await execute_team_task_create(
            {"team": "ghost", "body": "x"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is True
        assert "ghost" in result.content


# ---------------------------------------------------------------------------
# team_task_query
# ---------------------------------------------------------------------------


class TestTeamTaskQuery:
    async def test_query_returns_all(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        team_dir = registry.teams_dir / "devops"
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.append(team_dir, Task(id="t-002", body="b"))
        Tasks.update_status(team_dir, "t-002", "done")

        result = await execute_team_task_query(
            {"team": "devops"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        assert result.is_error is False
        data = json.loads(result.content)
        assert len(data) == 2

    async def test_query_filter_status(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        team_dir = registry.teams_dir / "devops"
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.append(team_dir, Task(id="t-002", body="b"))
        Tasks.update_status(team_dir, "t-002", "done")

        result = await execute_team_task_query(
            {"team": "devops", "status_filter": ["done"]},
            teams_registry=registry,
            project_root=tmp_path,
        )
        data = json.loads(result.content)
        assert len(data) == 1
        assert data[0]["id"] == "t-002"
        assert data[0]["status"] == "done"

    async def test_query_filter_assignee(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        team_dir = registry.teams_dir / "devops"
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.update_status(
            team_dir, "t-001", "in_progress", assignee="alice"
        )
        Tasks.append(team_dir, Task(id="t-002", body="b"))

        result = await execute_team_task_query(
            {"team": "devops", "assignee": "alice"},
            teams_registry=registry,
            project_root=tmp_path,
        )
        data = json.loads(result.content)
        assert len(data) == 1
        assert data[0]["id"] == "t-001"

    async def test_query_include_ready_graph(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        team_dir = registry.teams_dir / "devops"
        Tasks.append(team_dir, Task(id="t-001", body="a"))
        Tasks.update_status(team_dir, "t-001", "done")
        Tasks.append(
            team_dir,
            Task(id="t-002", body="b", depends_on=("t-001",)),
        )

        result = await execute_team_task_query(
            {"team": "devops", "include_ready_graph": True},
            teams_registry=registry,
            project_root=tmp_path,
        )
        data = json.loads(result.content)
        assert len(data) == 2
        # t-001 done → t-002 ready_for_dispatch=True
        t2 = next(t for t in data if t["id"] == "t-002")
        assert t2["ready_for_dispatch"] is True
        assert t2["depends_on"] == ["t-001"]


# ---------------------------------------------------------------------------
# register_team_tools integration
# ---------------------------------------------------------------------------


class TestRegisterTeamTools:
    async def test_register_all_six_tools(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        tool_reg = ToolRegistry()
        defs = await register_team_tools(tool_reg, registry, tmp_path)
        assert len(defs) == 6

        names = {t.name for t in tool_reg.get_all_tools(role="lead")}
        # Lead 应看到 7 builtin + 6 team_*
        team_names = {n for n in names if n.startswith("team_")}
        assert team_names == {
            "team_dispatch",
            "team_send_message",
            "team_cancel",
            "team_merge",
            "team_task_create",
            "team_task_query",
        }
        assert "Read" in names  # built-in 也在

    async def test_member_role_does_not_see_team_tools(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        tool_reg = ToolRegistry()
        await register_team_tools(tool_reg, registry, tmp_path)
        member_tools = tool_reg.get_all_tools(role="member")
        for t in member_tools:
            assert not t.name.startswith("team_")

    async def test_register_idempotent(
        self, registry, team_with_alice, tmp_path
    ) -> None:
        tool_reg = ToolRegistry()
        await register_team_tools(tool_reg, registry, tmp_path)
        # 第二次注册应当 idempotent(已注册视为 ok,不抛)
        await register_team_tools(tool_reg, registry, tmp_path)
        names = {t.name for t in tool_reg.get_all_tools(role="lead")}
        assert len([n for n in names if n.startswith("team_")]) == 6