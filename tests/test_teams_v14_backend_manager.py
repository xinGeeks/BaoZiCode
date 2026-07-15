"""v1.4 Pane Backend — `BackendManager` 居中调度测试。

覆盖 `openspec/changes/v1-4-team-pane-backend/specs/team-management/spec.md`
中 BackendManager Requirement:

- `detect_available_backends` 4 probe 缓存 + timeout
- `effective_backend` sys.platform upgrade + 显式 pane-* 强制 +
  pane 不可用降级
- `spawn_if_offline` dedup race + write state + pane_info 持久化 +
  spawn 失败 fallback
- `is_alive` 从 `_handles` 查
- `kill` 写 state=offline + handle.kill + pane_info 清 pid
- `restore_panes` hydrate 还活的 / 清死的
- `cleanup_team` 杀全部 member + 删 pane_info
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from baozicode.teams.backend_manager import BackendManager, _BACKEND_CLASS
from baozicode.teams.pane import (
    CoroutineBackend,
    PaneTmuxBackend,
)
from baozicode.teams.pane_info import PaneInfo, PaneMemberInfo
from baozicode.teams.registry import TeamsRegistry
from baozicode.teams.schema import Member


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeRegistry:
    """最小 TeamsRegistry stub —— 仅 BackendManager 用 teams_dir。"""

    def __init__(self, teams_dir: Path) -> None:
        self.teams_dir = teams_dir


@pytest.fixture
def teams_dir(tmp_path: Path) -> Path:
    """`<tmp>/.baozicode/teams/` 模拟 user-global teams dir。"""
    d = tmp_path / ".baozicode" / "teams"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def registry(teams_dir: Path) -> _FakeRegistry:
    return _FakeRegistry(teams_dir)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def manager(registry, project_root) -> BackendManager:
    return BackendManager(
        registry,  # type: ignore[arg-type]
        project_root=project_root,
        backend_detection_timeout=1.0,
    )


@pytest.fixture
def alice_member() -> Member:
    return Member(
        name="alice", role="dev",
        workdir=".worktrees/alice/", backend="coroutine",
    )


@pytest.fixture
def bob_explicit_pane() -> Member:
    return Member(
        name="bob", role="dev",
        workdir=".worktrees/bob/", backend="pane-tmux",
    )


@pytest.fixture
def team_dir(teams_dir: Path) -> Path:
    """`<teams_dir>/devops/` — 实际建 team 目录(member dir 由 spawn 建)。"""
    d = teams_dir / "devops"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set_availability(
    manager: BackendManager, **avail: bool,
) -> None:
    """手工设置 manager._detected_available 缓存,跳过 probe。"""
    manager._detected_available = {
        "pane-tmux": avail.get("pane-tmux", False),
        "pane-iterm2": avail.get("pane-iterm2", False),
        "pane-windows-terminal": avail.get("pane-windows-terminal", False),
        "coroutine": True,
        "worktree-coroutine": True,
    }


# ---------------------------------------------------------------------------
# detect_available_backends
# ---------------------------------------------------------------------------


class TestDetectAvailableBackends:
    """缓存 + 4 probe + coroutine 永远 True。"""

    def test_returns_dict_with_all_backend_types(self, manager) -> None:
        async def run() -> None:
            result = await manager.detect_available_backends()
            assert "pane-tmux" in result
            assert "pane-iterm2" in result
            assert "pane-windows-terminal" in result
            assert "coroutine" in result
            assert "worktree-coroutine" in result
        asyncio.run(run())

    def test_coroutine_always_true(self, manager) -> None:
        async def run() -> None:
            result = await manager.detect_available_backends()
            assert result["coroutine"] is True
            assert result["worktree-coroutine"] is True
        asyncio.run(run())

    def test_cache_second_call_returns_same_dict(self, manager) -> None:
        async def run() -> None:
            r1 = await manager.detect_available_backends()
            r2 = await manager.detect_available_backends()
            assert r1 is r2  # 同一对象引用
        asyncio.run(run())

    def test_probe_failure_does_not_raise(self, monkeypatch, manager) -> None:
        """probe 阶段任何异常 → 全 False,不抛。"""
        def boom(*args, **kwargs):
            raise OSError("simulated probe failure")

        import subprocess as _sp
        monkeypatch.setattr(_sp, "run", boom)
        async def run() -> None:
            result = await manager.detect_available_backends()
            assert result["pane-tmux"] is False
            assert result["pane-iterm2"] is False
            assert result["pane-windows-terminal"] is False
            assert result["coroutine"] is True  # coroutine 不 probe
        asyncio.run(run())


# ---------------------------------------------------------------------------
# effective_backend
# ---------------------------------------------------------------------------


class TestEffectiveBackend:
    """sys.platform upgrade + 显式 pane-* 强用 + 降级。"""

    def test_explicit_pane_uses_if_healthy(
        self, manager, bob_explicit_pane
    ) -> None:
        _set_availability(manager, **{"pane-tmux": True})
        async def run() -> None:
            result = await manager.effective_backend(bob_explicit_pane)
            assert result == "pane-tmux"
        asyncio.run(run())

    def test_explicit_pane_downgrades_when_unhealthy(
        self, manager, bob_explicit_pane
    ) -> None:
        _set_availability(manager, **{"pane-tmux": False})
        async def run() -> None:
            result = await manager.effective_backend(bob_explicit_pane)
            assert result == "coroutine"
        asyncio.run(run())

    def test_worktree_coroutine_preserved(self, manager) -> None:
        m = Member(name="alice", role="dev",
                   workdir=".worktrees/alice/",
                   backend="worktree-coroutine")
        _set_availability(manager, **{"pane-tmux": True})
        async def run() -> None:
            result = await manager.effective_backend(m)
            assert result == "worktree-coroutine"
        asyncio.run(run())

    def test_coroutine_default_upgrades_to_pane_on_linux(
        self, manager, alice_member, monkeypatch
    ) -> None:
        import platform as _platform
        monkeypatch.setattr(_platform, "system", lambda: "Linux")
        _set_availability(manager, **{"pane-tmux": True})
        async def run() -> None:
            result = await manager.effective_backend(alice_member)
            assert result == "pane-tmux"
        asyncio.run(run())

    def test_coroutine_default_upgrades_to_iterm_on_macos(
        self, manager, alice_member, monkeypatch
    ) -> None:
        import platform as _platform
        monkeypatch.setattr(_platform, "system", lambda: "Darwin")
        _set_availability(manager, **{"pane-iterm2": True})
        async def run() -> None:
            result = await manager.effective_backend(alice_member)
            assert result == "pane-iterm2"
        asyncio.run(run())

    def test_coroutine_default_upgrades_to_wt_on_windows(
        self, manager, alice_member, monkeypatch
    ) -> None:
        import platform as _platform
        monkeypatch.setattr(_platform, "system", lambda: "Windows")
        _set_availability(manager, **{"pane-windows-terminal": True})
        async def run() -> None:
            result = await manager.effective_backend(alice_member)
            assert result == "pane-windows-terminal"
        asyncio.run(run())

    def test_coroutine_fallback_when_no_pane_available(
        self, manager, alice_member
    ) -> None:
        _set_availability(manager)  # 全 False,coroutine 仍 True
        async def run() -> None:
            result = await manager.effective_backend(alice_member)
            assert result == "coroutine"
        asyncio.run(run())


# ---------------------------------------------------------------------------
# spawn_if_offline
# ---------------------------------------------------------------------------


class TestSpawnIfOffline:
    """首 dispatch 触发派生;dedup;写 state;pane_info 持久化。"""

    def test_first_spawn_creates_handle(
        self, manager, team_dir, alice_member
    ) -> None:
        _set_availability(manager)  # 走 coroutine
        async def run() -> None:
            handle = await manager.spawn_if_offline("devops", alice_member)
            assert isinstance(handle, CoroutineBackend)
            assert (team_dir / "alice" / "state.json").exists()
        asyncio.run(run())

    def test_second_spawn_returns_cached_handle(
        self, manager, team_dir, alice_member
    ) -> None:
        _set_availability(manager)
        async def run() -> None:
            h1 = await manager.spawn_if_offline("devops", alice_member)
            h2 = await manager.spawn_if_offline("devops", alice_member)
            assert h1 is h2
        asyncio.run(run())

    def test_pane_info_persisted(
        self, manager, team_dir, alice_member
    ) -> None:
        _set_availability(manager)
        async def run() -> None:
            await manager.spawn_if_offline("devops", alice_member)
            pane_info_path = team_dir / "pane_info.json"
            assert pane_info_path.exists()
            info = PaneInfo.load(pane_info_path)
            assert info is not None
            assert "alice" in info.members
            assert info.members["alice"].backend_type == "coroutine"
        asyncio.run(run())

    def test_state_json_has_backend_pid_for_coroutine(
        self, manager, team_dir, alice_member
    ) -> None:
        """coroutine backend pid=None。"""
        _set_availability(manager)
        async def run() -> None:
            await manager.spawn_if_offline("devops", alice_member)
            state_path = team_dir / "alice" / "state.json"
            from baozicode.teams import Mailbox
            state = Mailbox.read_state(state_path.parent)
            assert state.status == "idle"
            assert state.backend_pid is None  # coroutine 永 None
        asyncio.run(run())

    def test_concurrent_spawn_dedup(
        self, manager, team_dir, alice_member
    ) -> None:
        """两条并发 dispatch 应只 spawn 一次。"""
        _set_availability(manager)
        async def run() -> None:
            results = await asyncio.gather(
                manager.spawn_if_offline("devops", alice_member),
                manager.spawn_if_offline("devops", alice_member),
            )
            assert results[0] is results[1]
        asyncio.run(run())

    def test_spawn_failure_falls_back_to_coroutine(
        self, manager, team_dir, alice_member, monkeypatch
    ) -> None:
        """显式 pane-tmux + spawn 失败 → fallback coroutine。"""
        from baozicode.teams.pane import PaneTmuxBackend
        _set_availability(manager, **{"pane-tmux": True})
        # 强制 PaneTmuxBackend.spawn 抛
        def boom(self):
            raise RuntimeError("simulated tmux failure")
        monkeypatch.setattr(PaneTmuxBackend, "spawn", boom)
        async def run() -> None:
            handle = await manager.spawn_if_offline("devops", alice_member)
            assert isinstance(handle, CoroutineBackend)
        asyncio.run(run())


# ---------------------------------------------------------------------------
# is_alive
# ---------------------------------------------------------------------------


class TestIsAlive:
    """从 `_handles` 查;无 entry → False。"""

    def test_no_handle_returns_false(self, manager) -> None:
        assert manager.is_alive("devops", "alice") is False

    def test_alive_coroutine(
        self, manager, team_dir, alice_member
    ) -> None:
        _set_availability(manager)
        async def run() -> None:
            await manager.spawn_if_offline("devops", alice_member)
            assert manager.is_alive("devops", "alice") is True
        asyncio.run(run())

    def test_dead_coroutine(
        self, manager, team_dir, alice_member
    ) -> None:
        _set_availability(manager)
        async def run() -> None:
            await manager.spawn_if_offline("devops", alice_member)
            await manager.kill("devops", "alice", reason="test")
            assert manager.is_alive("devops", "alice") is False
        asyncio.run(run())


# ---------------------------------------------------------------------------
# kill
# ---------------------------------------------------------------------------


class TestKill:
    """写 state=offline + handle.kill + pane_info 清 pid。"""

    def test_kill_writes_offline_state(
        self, manager, team_dir, alice_member
    ) -> None:
        _set_availability(manager)
        async def run() -> None:
            await manager.spawn_if_offline("devops", alice_member)
            await manager.kill("devops", "alice", reason="test")
            from baozicode.teams import Mailbox
            state = Mailbox.read_state(team_dir / "alice")
            assert state.status == "offline"
        asyncio.run(run())

    def test_kill_returns_false_when_no_handle(self, manager) -> None:
        async def run() -> None:
            ok = await manager.kill("devops", "alice", reason="test")
            assert ok is False
        asyncio.run(run())

    def test_kill_clears_pane_info_pid(
        self, manager, team_dir, alice_member
    ) -> None:
        _set_availability(manager)
        async def run() -> None:
            await manager.spawn_if_offline("devops", "alice" if False else alice_member)
            # 手工注入 pid(模拟 pane backend,因为 coroutine 永远 None)
            pane_path = team_dir / "pane_info.json"
            from dataclasses import replace
            from datetime import datetime, timezone
            info = PaneInfo.load(pane_path)
            assert info is not None
            new_members = dict(info.members)
            entry = new_members["alice"]
            new_members["alice"] = PaneMemberInfo(
                backend_type=entry.backend_type,
                pane_identifier=entry.pane_identifier,
                pid=99999,
                last_active_ts=entry.last_active_ts,
            )
            replace(info, members=new_members).save(pane_path)
            # 重新 hydrate — 让 kill 走 handle.kill
            handle = manager._handles[("devops", "alice")]
            handle.pid = 99999  # 模拟 pane
            await manager.kill("devops", "alice", reason="test")
            # 验证 pane_info pid 已清
            info2 = PaneInfo.load(pane_path)
            assert info2 is not None
            assert info2.members["alice"].pid is None
        asyncio.run(run())


# ---------------------------------------------------------------------------
# restore_panes
# ---------------------------------------------------------------------------


class TestRestorePanes:
    """读 pane_info.json;hydrate 还活的 / 清死的。"""

    def test_no_pane_info_returns_zero(self, manager, team_dir) -> None:
        result = manager.restore_panes("devops")
        assert result == 0

    def test_hydrate_alive_pid(
        self, manager, team_dir, monkeypatch
    ) -> None:
        """写一份 pane_info 含 pid,monkeypatch 让 _pid_alive 返 True。"""
        import baozicode.teams.backend_manager as bm_mod
        monkeypatch.setattr(bm_mod, "_pid_alive", lambda pid: True)
        info = PaneInfo(
            team="devops",
            members={
                "alice": PaneMemberInfo(
                    backend_type="pane-tmux", pid=1234,
                ),
            },
        )
        info.save(team_dir / "pane_info.json")
        result = manager.restore_panes("devops")
        assert result == 1
        assert manager.is_alive("devops", "alice") is True

    def test_remove_dead_pid(
        self, manager, team_dir, monkeypatch
    ) -> None:
        """pid 不活 → 移除 entry,pane_info 写回清理后版本。"""
        import baozicode.teams.backend_manager as bm_mod
        monkeypatch.setattr(bm_mod, "_pid_alive", lambda pid: False)
        info = PaneInfo(
            team="devops",
            members={
                "alice": PaneMemberInfo(
                    backend_type="pane-tmux", pid=99999,
                ),
                "bob": PaneMemberInfo(backend_type="coroutine"),
            },
        )
        info.save(team_dir / "pane_info.json")
        result = manager.restore_panes("devops")
        assert result == 0
        # 验证 pane_info 不再含 alice
        info2 = PaneInfo.load(team_dir / "pane_info.json")
        assert info2 is not None
        assert "alice" not in info2.members
        # bob 没 pid,本来就不应被 hydrate(只 hydrate 有 pid 且活的)
        assert "bob" not in info2.members

    def test_mixed_alive_and_dead(
        self, manager, team_dir, monkeypatch
    ) -> None:
        """alice 活 + bob 死 → 仅 hydrate alice。"""
        import baozicode.teams.backend_manager as bm_mod
        def fake_alive(pid):
            return pid == 1234
        monkeypatch.setattr(bm_mod, "_pid_alive", fake_alive)
        info = PaneInfo(
            team="devops",
            members={
                "alice": PaneMemberInfo(backend_type="pane-tmux", pid=1234),
                "bob": PaneMemberInfo(backend_type="pane-tmux", pid=9999),
            },
        )
        info.save(team_dir / "pane_info.json")
        result = manager.restore_panes("devops")
        assert result == 1
        assert manager.is_alive("devops", "alice") is True
        assert manager.is_alive("devops", "bob") is False


# ---------------------------------------------------------------------------
# cleanup_team
# ---------------------------------------------------------------------------


class TestCleanupTeam:
    """team destroy 触发:kill 所有 member + 删 pane_info。"""

    def test_cleanup_with_no_handles(
        self, manager, team_dir
    ) -> None:
        async def run() -> None:
            killed = await manager.cleanup_team("devops")
            assert killed == 0
        asyncio.run(run())

    def test_cleanup_kills_all_handles(
        self, manager, team_dir, alice_member
    ) -> None:
        from baozicode.teams.schema import Member as M
        _set_availability(manager)
        bob = M(name="bob", role="dev",
                workdir=".worktrees/bob/", backend="coroutine")
        async def run() -> None:
            await manager.spawn_if_offline("devops", alice_member)
            await manager.spawn_if_offline("devops", bob)
            killed = await manager.cleanup_team("devops")
            assert killed == 2
            # pane_info 已删
            assert not (team_dir / "pane_info.json").exists()
        asyncio.run(run())

    def test_cleanup_deletes_pane_info(
        self, manager, team_dir
    ) -> None:
        # 预先写一份
        PaneInfo.empty(team="devops").save(team_dir / "pane_info.json")
        assert (team_dir / "pane_info.json").exists()
        async def run() -> None:
            await manager.cleanup_team("devops")
            assert not (team_dir / "pane_info.json").exists()
        asyncio.run(run())


# ---------------------------------------------------------------------------
# member_run_command 模板替换
# ---------------------------------------------------------------------------


class TestMemberCommandTemplate:
    """`member_run_command` 占位符替换。"""

    def test_default_command_has_placeholders(self, manager) -> None:
        cmd = manager._member_run_command
        assert "{team}" in cmd
        assert "{name}" in cmd

    def test_format_replaces_placeholders(self, manager) -> None:
        cmd = manager._format_command("devops", "alice")
        assert "{team}" not in cmd
        assert "{name}" not in cmd
        assert "devops" in cmd
        assert "alice" in cmd

    def test_custom_command(self, registry, project_root) -> None:
        mgr = BackendManager(
            registry,  # type: ignore[arg-type]
            project_root=project_root,
            member_run_command=[
                "python", "-m", "mypkg",
                "--team", "{team}", "--member", "{name}",
            ],
        )
        cmd = mgr._format_command("acme", "bob")
        assert cmd == ["python", "-m", "mypkg", "--team", "acme", "--member", "bob"]
