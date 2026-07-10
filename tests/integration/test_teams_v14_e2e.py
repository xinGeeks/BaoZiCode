"""v1.4 Team Foundation — 端到端集成测试。

跨模块协同:TeamsRegistry + TeamStore + Mailbox + mailbox_lock 在一个
真实 filesystem 场景下跑通完整 lifecycle。

覆盖:
- `bootstrap → create_team → add_member → append_message → read_messages`
  → `destroy` 全链路
- 多个 member + 双向 inbox/outbox 验证
- state 持久化 + 跨实例读取
- lockfile 端到端:append 期间别的 append 阻塞,但最终都成功
- empty team.json 容错
- 同名 member 多次添加只成功第一次
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.teams import (
    Mailbox,
    Member,
    MemberAlreadyExists,
    Message,
    TeamsRegistry,
    TeamStore,
)
from baozicode.teams.lockfile import mailbox_lock


def _stub_config(teams_dir: Path):
    """构造最小 AppConfig stub,只暴露 teams.dir。"""

    class _TeamsCfg:
        def __init__(self, dir_: Path) -> None:
            self.dir = str(dir_)
            self.enabled = True

    class _Cfg:
        def __init__(self, dir_: Path) -> None:
            self.teams = _TeamsCfg(dir_)

    return _Cfg(teams_dir)


# ---------------------------------------------------------------------------
# 全 lifecycle
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_create_add_message_destroy(self, tmp_path: Path) -> None:
        cfg = _stub_config(tmp_path)
        reg = TeamsRegistry.bootstrap(cfg)

        # Create
        store = reg.create_team("devops", lead="alice")
        assert store.team_dir.exists()

        # Add members
        store.add_member(Member(name="bob", role="backend", backend="coroutine"))
        store.add_member(Member(name="carol", role="frontend", backend="coroutine"))

        # Append messages
        bob_dir = store.team_dir / "bob"
        carol_dir = store.team_dir / "carol"
        Mailbox.append_message(
            bob_dir, "inbox", Message(sender="lead", body="implement /health")
        )
        Mailbox.append_message(
            carol_dir, "inbox", Message(sender="lead", body="design landing page")
        )
        Mailbox.append_message(
            bob_dir, "outbox", Message(sender="bob", body="PR #42 opened")
        )

        # Read back
        bob_inbox = Mailbox.read_messages(bob_dir, "inbox")
        carol_inbox = Mailbox.read_messages(carol_dir, "inbox")
        bob_outbox = Mailbox.read_messages(bob_dir, "outbox")

        assert len(bob_inbox) == 1
        assert bob_inbox[0].body == "implement /health"
        assert len(carol_inbox) == 1
        assert carol_inbox[0].body == "design landing page"
        assert len(bob_outbox) == 1
        assert bob_outbox[0].body == "PR #42 opened"

        # State defaults
        from baozicode.teams import MemberState

        bob_state = Mailbox.read_state(bob_dir)
        assert bob_state.status == "offline"

        # Destroy
        reg.delete_team("devops", confirm=True)
        assert not (tmp_path / "devops").exists()
        assert reg.get("devops") is None


class TestReloadFromDisk:
    """TeamStore 实例只持有内存缓存,真实状态在 disk;验证 reload 后仍能读。"""

    def test_reload_members_after_disk_persist(self, tmp_path: Path) -> None:
        cfg = _stub_config(tmp_path)
        reg = TeamsRegistry.bootstrap(cfg)
        store = reg.create_team("devops")
        store.add_member(Member(name="alice", role="backend"))
        store.add_member(Member(name="bob", role="frontend"))

        # Drop in-memory TeamStore,reload from disk
        fresh = TeamStore.from_name(tmp_path, "devops")
        assert set(fresh.list_members()) == {"alice", "bob"}

        # 进一步:team.json 已被新 instance 读到
        team = fresh.show()
        assert team.name == "devops"
        assert team.members["alice"].role == "backend"


class TestConcurrentAppend:
    """多线程并发 append_message — 锁保证不丢不交错。"""

    def test_10_threads_50_messages_each(self, tmp_path: Path) -> None:
        import threading

        cfg = _stub_config(tmp_path)
        reg = TeamsRegistry.bootstrap(cfg)
        store = reg.create_team("devops")
        store.add_member(Member(name="alice", role="backend"))
        member_dir = store.team_dir / "alice"

        n_threads = 10
        n_per_thread = 50
        errors: list[Exception] = []

        def worker(thread_id: int) -> None:
            try:
                for i in range(n_per_thread):
                    Mailbox.append_message(
                        member_dir,
                        "inbox",
                        Message(sender=f"t{thread_id}", body=f"m{i}"),
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        messages = Mailbox.read_messages(member_dir, "inbox")
        assert len(messages) == n_threads * n_per_thread


class TestLockfileEndToEnd:
    """mailbox_lock context manager 在 mailbox.append_message 端到端跑通。"""

    def test_external_lock_blocks_appends(self, tmp_path: Path) -> None:
        cfg = _stub_config(tmp_path)
        reg = TeamsRegistry.bootstrap(cfg)
        store = reg.create_team("devops")
        store.add_member(Member(name="alice", role="backend"))
        member_dir = store.team_dir / "alice"

        # 拿住外部锁,append_message 应该超时(MailboxLockTimeout)
        from baozicode.teams.lockfile import MailboxLockTimeout

        lock_path = member_dir / ".lock"
        with mailbox_lock(lock_path, timeout=2.0):
            with pytest.raises(MailboxLockTimeout):
                Mailbox.append_message(
                    member_dir,
                    "inbox",
                    Message(sender="lead", body="blocked"),
                    lock_timeout=1.0,
                    lock_stale_seconds=30.0,
                )

        # 释放锁后正常 append
        Mailbox.append_message(
            member_dir, "inbox", Message(sender="lead", body="after release")
        )
        msgs = Mailbox.read_messages(member_dir, "inbox")
        assert len(msgs) == 1
        assert msgs[0].body == "after release"


class TestEdgeCases:
    def test_add_member_after_partial_state_corruption(self, tmp_path: Path) -> None:
        """state.json 内容损坏 → read_state 走 default 不抛。"""
        cfg = _stub_config(tmp_path)
        reg = TeamsRegistry.bootstrap(cfg)
        store = reg.create_team("devops")
        store.add_member(Member(name="alice", role="backend"))
        alice_dir = store.team_dir / "alice"

        # 写一个损坏 state.json
        (alice_dir / "state.json").write_text("{not json", encoding="utf-8")

        # read_state 损坏 → 应该走 default,不是抛
        from baozicode.teams import Mailbox, MemberState

        s = Mailbox.read_state(alice_dir)
        assert s.status == "offline"

    def test_destroy_already_destroyed_raises(self, tmp_path: Path) -> None:
        cfg = _stub_config(tmp_path)
        reg = TeamsRegistry.bootstrap(cfg)
        reg.create_team("devops")
        reg.delete_team("devops", confirm=True)
        with pytest.raises(Exception):  # TeamNotFound
            reg.delete_team("devops", confirm=True)

    def test_add_duplicate_member_raises(self, tmp_path: Path) -> None:
        cfg = _stub_config(tmp_path)
        reg = TeamsRegistry.bootstrap(cfg)
        store = reg.create_team("devops")
        store.add_member(Member(name="alice", role="backend"))
        with pytest.raises(MemberAlreadyExists):
            store.add_member(Member(name="alice", role="frontend"))