"""v1.4 Pane Backend — MailboxLayer + build_member_agent 测试。

覆盖 `openspec/changes/v1-4-team-pane-backend/specs/team-management/spec.md`
中 MemberAgent + MailboxLayer Requirement:

- MailboxLayer.read_inbox_unread 去重(seen_bodies)
- MailboxLayer.write_outbox 写盘
- MailboxLayer.clear_seen 重置
- build_member_agent role='member' / 7 builtin + load_skill
- build_member_agent 不暴露 team_* Lead 工具
- build_member_agent fresh conversation(无 session resume)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _agent_helpers import make_minimal_config

from baozicode.teams.member_agent import MailboxLayer, build_member_agent
from baozicode.teams.member_loop import MemberMainLoop
from baozicode.teams.registry import TeamsRegistry
from baozicode.teams.schema import Member, Message, Team
from baozicode.teams.mailbox import Mailbox
from baozicode.tools import registry as tool_registry


@pytest.fixture(scope="session", autouse=True)
def _register_load_skill_singleton() -> None:
    """`load_skill` 在生产由 `App.on_mount` 异步注册;这里手动注册一次。

    `tool_type='internal'` 保证成员 Agent 可见(不受角色过滤影响),
    见 test_member_has_load_skill_internal。已注册则吞 ValueError。
    """
    async def _do() -> None:
        from baozicode.skills.loader import LOAD_SKILL_TOOL
        async def _stub_executor(_args: dict) -> Any:  # noqa: ANN401
            from baozicode.tools.base import ToolResult
            return ToolResult.success("", "")
        try:
            await tool_registry.register_tool(
                LOAD_SKILL_TOOL, _stub_executor, source_label="test"
            )
        except ValueError:
            pass

    asyncio.run(_do())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self, teams_dir: Path) -> None:
        self.teams_dir = teams_dir


@pytest.fixture
def teams_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".baozicode" / "teams"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def registry(teams_dir: Path) -> _FakeRegistry:
    return _FakeRegistry(teams_dir)


@pytest.fixture
def team_devops(teams_dir: Path) -> Team:
    """在 teams_dir/devops/ 建团队 + alice member 目录。"""
    team_dir = teams_dir / "devops"
    team_dir.mkdir(parents=True, exist_ok=True)
    alice_dir = team_dir / "alice"
    alice_dir.mkdir(parents=True, exist_ok=True)
    team = Team(
        name="devops",
        members={
            "alice": Member(
                name="alice", role="dev",
                workdir=".worktrees/alice/",
                backend="coroutine",
            ),
        },
    )
    team.save(team_dir / "team.json")
    return team


# ---------------------------------------------------------------------------
# MailboxLayer
# ---------------------------------------------------------------------------


class TestMailboxLayer:
    """MailboxLayer hook — read_inbox_unread / write_outbox / clear_seen。"""

    def test_read_empty_inbox(
        self, registry, team_devops
    ) -> None:
        layer = MailboxLayer(registry, "devops", "alice")  # type: ignore[arg-type]
        assert layer.read_inbox_unread() == []

    def test_read_inbox_dedup(
        self, registry, team_devops
    ) -> None:
        """同一 sender + body 的消息多次出现 → 只返一次。"""
        layer = MailboxLayer(registry, "devops", "alice")  # type: ignore[arg-type]
        member_dir = registry.teams_dir / "devops" / "alice"
        Mailbox.append_message(
            member_dir, "inbox",
            Message(sender="lead", body="task=abc: do something"),
        )
        # 第一次:返一条
        first = layer.read_inbox_unread()
        assert len(first) == 1
        assert first[0].body == "task=abc: do something"
        # 第二次:已 seen,返 []
        second = layer.read_inbox_unread()
        assert second == []

    def test_write_outbox(
        self, registry, team_devops
    ) -> None:
        layer = MailboxLayer(registry, "devops", "alice")  # type: ignore[arg-type]
        layer.write_outbox("hello from alice")
        member_dir = registry.teams_dir / "devops" / "alice"
        outbox = Mailbox.read_messages(member_dir, "outbox")
        assert len(outbox) == 1
        assert outbox[0].sender == "alice"
        assert outbox[0].body == "hello from alice"

    def test_mark_inbox_read_clears_seen(
        self, registry, team_devops
    ) -> None:
        """mark_inbox_read 后,清 seen → 同 body 再次追加可被读出。"""
        layer = MailboxLayer(registry, "devops", "alice")  # type: ignore[arg-type]
        member_dir = registry.teams_dir / "devops" / "alice"
        Mailbox.append_message(
            member_dir, "inbox",
            Message(sender="lead", body="task=abc: do X"),
        )
        first = layer.read_inbox_unread()
        assert len(first) == 1
        layer.mark_inbox_read(first)
        # 同样 body 再 append → 因 mark_inbox_read 清了 seen,
        # 第二次读仍可见
        Mailbox.append_message(
            member_dir, "inbox",
            Message(sender="lead", body="task=abc: do X"),
        )
        second = layer.read_inbox_unread()
        assert len(second) == 1

    def test_clear_seen_resets(
        self, registry, team_devops
    ) -> None:
        layer = MailboxLayer(registry, "devops", "alice")  # type: ignore[arg-type]
        member_dir = registry.teams_dir / "devops" / "alice"
        Mailbox.append_message(
            member_dir, "inbox",
            Message(sender="lead", body="task=abc: do X"),
        )
        layer.read_inbox_unread()  # 标记 seen
        layer.clear_seen()
        # clear 后,新读应可见
        Mailbox.append_message(
            member_dir, "inbox",
            Message(sender="lead", body="task=abc: do X"),
        )
        assert len(layer.read_inbox_unread()) == 1

    def test_member_dir_property(
        self, registry, team_devops
    ) -> None:
        layer = MailboxLayer(registry, "devops", "alice")  # type: ignore[arg-type]
        assert layer.member_dir == registry.teams_dir / "devops" / "alice"


# ---------------------------------------------------------------------------
# build_member_agent
# ---------------------------------------------------------------------------


class _StubLLMClient:
    """最小 LLMClient stub —— Member Agent 测试不需要真 LLM。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def stream(self, messages, system, tools, **kwargs):
        # 简单 yield 一个 done event
        from baozicode.agent.events import AgentEvent
        yield AgentEvent(type="done", stop_reason="COMPLETED", text="")


class _StubAppConfig:
    """最小 AppConfig stub — 实际用 make_minimal_config()。"""
    pass


class _StubMergedPermissions:
    """最小 MergedPermissions stub。"""
    def __init__(self) -> None:
        self.mode = "default"
        self.rules = []
        self.real_root = Path("/")
        self.path_sandbox_enabled = False


class TestBuildMemberAgent:
    """build_member_agent: role='member' + 7 builtin + load_skill。"""

    def test_agent_has_member_role(
        self, registry, team_devops
    ) -> None:
        llm = _StubLLMClient()
        config = make_minimal_config()
        perms = _StubMergedPermissions()
        agent = build_member_agent(
            registry,  # type: ignore[arg-type]
            "devops",
            team_devops.members["alice"],
            llm_client=llm,  # type: ignore[arg-type]
            config=config,
            permissions=perms,  # type: ignore[arg-type]
        )
        assert agent.role == "member"

    def test_member_has_7_builtin_tools(
        self, registry, team_devops
    ) -> None:
        """7 builtin + load_skill(tool_type='internal' 不受角色过滤)。"""
        llm = _StubLLMClient()
        config = make_minimal_config()
        perms = _StubMergedPermissions()
        agent = build_member_agent(
            registry, "devops",
            team_devops.members["alice"],
            llm_client=llm, config=config, permissions=perms,
        )
        tool_names = {t.name for t in agent._all_tools}
        # 7 builtin 工具
        assert "Read" in tool_names
        assert "Write" in tool_names
        assert "Edit" in tool_names
        assert "Bash" in tool_names
        assert "Grep" in tool_names
        assert "Glob" in tool_names
        assert "WebFetch" in tool_names

    def test_member_has_no_team_tools(
        self, registry, team_devops
    ) -> None:
        """member role 拿不到 team_* Lead 工具。"""
        llm = _StubLLMClient()
        config = make_minimal_config()
        perms = _StubMergedPermissions()
        agent = build_member_agent(
            registry, "devops",
            team_devops.members["alice"],
            llm_client=llm, config=config, permissions=perms,
        )
        tool_names = {t.name for t in agent._all_tools}
        assert not any(n.startswith("team_") for n in tool_names)

    def test_member_has_load_skill_internal(
        self, registry, team_devops
    ) -> None:
        """`load_skill` tool_type='internal' 不受角色过滤,可见。"""
        llm = _StubLLMClient()
        config = make_minimal_config()
        perms = _StubMergedPermissions()
        agent = build_member_agent(
            registry, "devops",
            team_devops.members["alice"],
            llm_client=llm, config=config, permissions=perms,
        )
        tool_names = {t.name for t in agent._all_tools}
        assert "load_skill" in tool_names

    def test_conversation_is_fresh(
        self, registry, team_devops
    ) -> None:
        """fresh ConversationManager — 不 resume session。"""
        llm = _StubLLMClient()
        config = make_minimal_config()
        perms = _StubMergedPermissions()
        agent = build_member_agent(
            registry, "devops",
            team_devops.members["alice"],
            llm_client=llm, config=config, permissions=perms,
        )
        assert agent._conversation is not None
        assert agent._conversation._archiver is None

    def test_mailbox_notifier_not_set(
        self, registry, team_devops
    ) -> None:
        """Member Agent **不** 接 mailbox_notifier(那是 Lead 的 helper)。"""
        llm = _StubLLMClient()
        config = make_minimal_config()
        perms = _StubMergedPermissions()
        agent = build_member_agent(
            registry, "devops",
            team_devops.members["alice"],
            llm_client=llm, config=config, permissions=perms,
        )
        assert agent._mailbox_notifier is None


# ---------------------------------------------------------------------------
# MemberMainLoop 长生命周期 polling
# ---------------------------------------------------------------------------


class _StubAgent:
    """可控的 Agent stub — `events` 是 AgentEvent 流(按顺序 yield)。

    `wait_during: bool` — 控制 run 是否阻塞等待(set terminate 触发 cancel)
    """

    def __init__(
        self, events: list[Any], wait_during: bool = False
    ) -> None:
        self._events = events
        self._wait = wait_during
        self.built_count = 0  # 标识每个 stub 实例(测 fresh-per-turn 用)
        self._built_id = id(self)

    @property
    def built_id(self) -> int:
        return self._built_id

    async def run(self, user_msg: str):
        from baozicode.agent.events import AgentEvent
        for ev in self._events:
            if self._wait:
                await asyncio.sleep(0.5)
            yield ev
        if not self._events or self._events[-1].type != "done":
            yield AgentEvent(type="done", stop_reason="COMPLETED", text="")


@pytest.fixture
def real_registry(teams_dir: Path) -> TeamsRegistry:
    """用真实 TeamsRegistry(供 MemberMainLoop.run() 调 .get(team))。"""
    return TeamsRegistry(teams_dir)


@pytest.fixture
def stub_build_agent(monkeypatch):
    """替换 `member_loop.build_member_agent` 为可控 stub factory。

    Returns:list[_StubAgent] —— 每轮 wake 创建一个新 stub,append 到 list。
    """
    built: list[_StubAgent] = []
    _factory_lock = asyncio.Lock() if False else None  # 保留位

    def _factory(*args, **kwargs) -> _StubAgent:
        # 默认 events:done —— 立即结束 turn
        agent = _StubAgent(events=[
            type("Ev", (), {"type": "done", "text": ""})(),
        ])
        built.append(agent)
        return agent

    monkeypatch.setattr(
        "baozicode.teams.member_loop.build_member_agent", _factory,
    )
    return built


def _make_stub_agent_with_events(events: list[Any]):
    """Build a stub agent yielding the given AgentEvent-like dicts."""
    class _CustomStub(_StubAgent):
        def __init__(self) -> None:
            super().__init__(events=events, wait_during=False)
    return _CustomStub()


def _done_event() -> Any:
    from baozicode.agent.events import AgentEvent
    return AgentEvent(type="done", payload={"stop_reason": "COMPLETED"})


def _text_event(text: str) -> Any:
    from baozicode.agent.events import AgentEvent
    return AgentEvent(type="text", payload=text)


class TestMemberMainLoop:
    """MemberMainLoop.run() 主循环行为。

    用 monkeypatch 控制 wake / agent / state 写。真实 TeamsRegistry +
    真实 Mailbox 文件读写。
    """

    @staticmethod
    def _make_wake_then_stop(
        holder: dict[str, Any],
        return_value: bool = True,
        wake_calls: dict[str, int] | None = None,
        stop_after: int | None = None,
    ) -> Any:
        """构造一个 `Mailbox.wait_for_wake` 桩。

        语义:
          - 每次 wake 调用递增 `wake_calls["n"]`(若提供)
          - 当 `wake_calls["n"] >= stop_after` 时,先 set terminate,
            然后返 `return_value`(让本轮 while 检查下一拍才生效)
          - `stop_after=None` → 永不 set terminate(测试自己负责退出)
          - 内部 `await asyncio.sleep(0)` 强制让出事件循环,防止
            wake 同步返回时 loop 自旋占用 CPU、阻塞外部 terminate
        """
        async def fake_wait_for_wake(*args, **kwargs):
            await asyncio.sleep(0)
            if wake_calls is not None:
                wake_calls["n"] += 1
            loop = holder.get("loop")
            if (
                stop_after is not None
                and wake_calls is not None
                and wake_calls["n"] >= stop_after
                and loop is not None
            ):
                loop.request_terminate()
            return return_value
        return fake_wait_for_wake

    def test_team_not_found_raises(
        self, real_registry: TeamsRegistry
    ) -> None:
        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "ghost", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            with pytest.raises(Exception) as ei:
                await loop.run()
            assert "ghost" in str(ei.value)
        asyncio.run(run())

    def test_member_not_found_raises(
        self, real_registry: TeamsRegistry, team_devops: Team
    ) -> None:
        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "ghost",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            with pytest.raises(Exception) as ei:
                await loop.run()
            assert "ghost" in str(ei.value)
        asyncio.run(run())

    def test_empty_inbox_does_not_spawn_agent(
        self, real_registry: TeamsRegistry, team_devops: Team,
        stub_build_agent, monkeypatch,
    ) -> None:
        """Wake 触发但 inbox 空 → 不构造 Agent,turn_count 保持 0。"""
        holder: dict[str, Any] = {}
        wake_calls: dict[str, int] = {"n": 0}
        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.wait_for_wake",
            self._make_wake_then_stop(
                holder, return_value=False,
                wake_calls=wake_calls, stop_after=1,
            ),
        )

        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            holder["loop"] = loop
            await loop.run()
            assert wake_calls["n"] == 1
            assert len(stub_build_agent) == 0
            assert loop.turn_count == 0
        asyncio.run(run())

    def test_inbox_message_triggers_turn(
        self, real_registry: TeamsRegistry, team_devops: Team,
        stub_build_agent, monkeypatch,
    ) -> None:
        """inbox 有消息 → 走一轮 turn → Agent 构造一次。"""
        holder: dict[str, Any] = {}
        wake_calls: dict[str, int] = {"n": 0}
        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.wait_for_wake",
            self._make_wake_then_stop(
                holder, return_value=True,
                wake_calls=wake_calls, stop_after=2,
            ),
        )

        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            holder["loop"] = loop
            Mailbox.append_message(
                real_registry.teams_dir / "devops" / "alice",
                "inbox",
                Message(sender="lead", body="task=abc: hello"),
            )
            await loop.run()
            assert len(stub_build_agent) == 1
            assert loop.turn_count == 1
        asyncio.run(run())

    def test_fresh_agent_per_turn(
        self, real_registry: TeamsRegistry, team_devops: Team,
        stub_build_agent, monkeypatch,
    ) -> None:
        """每轮 wake → 构造新 Agent 实例(fresh per turn)。"""
        holder: dict[str, Any] = {}
        wake_calls: dict[str, int] = {"n": 0}
        # 写两条不同 body 让两轮都 spawn agent
        member_dir = real_registry.teams_dir / "devops" / "alice"
        Mailbox.append_message(
            member_dir, "inbox",
            Message(sender="lead", body="task=abc: first"),
        )
        Mailbox.append_message(
            member_dir, "inbox",
            Message(sender="lead", body="task=xyz: second"),
        )
        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.wait_for_wake",
            self._make_wake_then_stop(
                holder, return_value=True,
                wake_calls=wake_calls, stop_after=3,
            ),
        )

        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            holder["loop"] = loop
            await loop.run()
            assert wake_calls["n"] == 3
            assert len(stub_build_agent) == 2
            ids = {a.built_id for a in stub_build_agent}
            assert len(ids) == 2  # 两个不同实例
        asyncio.run(run())

    def test_request_terminate_exits_cleanly(
        self, real_registry: TeamsRegistry, team_devops: Team,
        monkeypatch,
    ) -> None:
        """`request_terminate()` → loop 退出 + state=offline 写回。"""
        holder: dict[str, Any] = {}
        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.wait_for_wake",
            self._make_wake_then_stop(holder, return_value=False),
        )

        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            holder["loop"] = loop
            # 后台驱动 run,稍后 request_terminate
            task = asyncio.create_task(loop.run())
            await asyncio.sleep(0.05)
            loop.request_terminate()
            await asyncio.wait_for(task, timeout=2.0)
            state = Mailbox.read_state(
                real_registry.teams_dir / "devops" / "alice"
            )
            assert state.status == "offline"
            assert loop.is_terminated is True
        asyncio.run(run())

    def test_state_writes_running_and_idle(
        self, real_registry: TeamsRegistry, team_devops: Team,
        stub_build_agent, monkeypatch,
    ) -> None:
        """一轮 turn → state 从 idle 切到 running 再回 idle。"""
        states_seen: list[str] = []

        real_write_state = Mailbox.write_state

        def tracking_write_state(member_dir, state):
            states_seen.append(state.status)
            return real_write_state(member_dir, state)

        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.write_state",
            tracking_write_state,
        )

        holder: dict[str, Any] = {}
        wake_calls: dict[str, int] = {"n": 0}
        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.wait_for_wake",
            self._make_wake_then_stop(
                holder, return_value=True,
                wake_calls=wake_calls, stop_after=2,
            ),
        )

        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            holder["loop"] = loop
            Mailbox.append_message(
                real_registry.teams_dir / "devops" / "alice",
                "inbox",
                Message(sender="lead", body="task=abc: test"),
            )
            await loop.run()
            assert "running" in states_seen
            assert "idle" in states_seen
            final_state = Mailbox.read_state(
                real_registry.teams_dir / "devops" / "alice"
            )
            assert final_state.status in ("idle", "offline")
        asyncio.run(run())

    def test_chdir_to_workdir(
        self, real_registry: TeamsRegistry, team_devops: Team,
        monkeypatch,
    ) -> None:
        """run() 启动时 chdir 到 member.workdir 解析后的绝对路径。"""
        recorded: list[str] = []

        import os as _os

        real_chdir = _os.chdir

        def tracking_chdir(p):
            recorded.append(str(p))
            return real_chdir(p)

        monkeypatch.setattr("os.chdir", tracking_chdir)

        holder: dict[str, Any] = {}
        wake_calls: dict[str, int] = {"n": 0}
        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.wait_for_wake",
            self._make_wake_then_stop(
                holder, return_value=False,
                wake_calls=wake_calls, stop_after=1,
            ),
        )

        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            holder["loop"] = loop
            await loop.run()
            assert any("alice" in p for p in recorded), recorded
        asyncio.run(run())

    def test_exception_in_turn_does_not_hang_loop(
        self, real_registry: TeamsRegistry, team_devops: Team,
        monkeypatch,
    ) -> None:
        """Agent.run() 抛异常 → loop 不挂,继续下一轮。"""

        def boom_factory(*args, **kwargs):
            class _BoomAgent:
                async def run(self, user_msg):
                    raise RuntimeError("simulated agent failure")
                    yield  # noqa: unreachable, makes it a generator
            return _BoomAgent()

        monkeypatch.setattr(
            "baozicode.teams.member_loop.build_member_agent", boom_factory,
        )

        holder: dict[str, Any] = {}
        wake_calls: dict[str, int] = {"n": 0}
        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.wait_for_wake",
            self._make_wake_then_stop(
                holder, return_value=True,
                wake_calls=wake_calls, stop_after=3,
            ),
        )

        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            holder["loop"] = loop
            Mailbox.append_message(
                real_registry.teams_dir / "devops" / "alice",
                "inbox",
                Message(sender="lead", body="task=abc: boom"),
            )
            await asyncio.wait_for(loop.run(), timeout=2.0)
            assert loop.is_terminated is True
        asyncio.run(run())

    def test_text_event_writes_to_outbox(
        self, real_registry: TeamsRegistry, team_devops: Team,
        monkeypatch,
    ) -> None:
        """Agent.run() yield text event → outbox 写一条对应消息。"""

        def custom_factory(*args, **kwargs):
            class _TextAgent:
                async def run(self, user_msg):
                    yield _text_event("hello from member")
                    yield _done_event()
            return _TextAgent()

        monkeypatch.setattr(
            "baozicode.teams.member_loop.build_member_agent", custom_factory,
        )

        holder: dict[str, Any] = {}
        wake_calls: dict[str, int] = {"n": 0}
        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.wait_for_wake",
            self._make_wake_then_stop(
                holder, return_value=True,
                wake_calls=wake_calls, stop_after=2,
            ),
        )

        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            holder["loop"] = loop
            Mailbox.append_message(
                real_registry.teams_dir / "devops" / "alice",
                "inbox",
                Message(sender="lead", body="task=abc: say hi"),
            )
            await loop.run()
            outbox = Mailbox.read_messages(
                real_registry.teams_dir / "devops" / "alice", "outbox",
            )
            assert len(outbox) == 1
            assert outbox[0].body == "hello from member"
            assert outbox[0].sender == "alice"
        asyncio.run(run())

    def test_seen_messages_marked_read(
        self, real_registry: TeamsRegistry, team_devops: Team,
        stub_build_agent, monkeypatch,
    ) -> None:
        """turn 跑过后,mark_inbox_read 移除 seen → 同 body 再次可见。"""
        holder: dict[str, Any] = {}
        wake_calls: dict[str, int] = {"n": 0}
        monkeypatch.setattr(
            "baozicode.teams.member_loop.Mailbox.wait_for_wake",
            self._make_wake_then_stop(
                holder, return_value=True,
                wake_calls=wake_calls, stop_after=3,
            ),
        )

        async def run() -> None:
            loop = MemberMainLoop(
                real_registry, "devops", "alice",
                llm_client=_StubLLMClient(),  # type: ignore[arg-type]
                config=make_minimal_config(),
                permissions=_StubMergedPermissions(),  # type: ignore[arg-type]
            )
            holder["loop"] = loop
            member_dir = real_registry.teams_dir / "devops" / "alice"
            Mailbox.append_message(
                member_dir, "inbox",
                Message(sender="lead", body="task=abc: test"),
            )
            await loop.run()
            # turn 1 处理完;再写同样 body(模拟 Lead 重发)
            Mailbox.append_message(
                member_dir, "inbox",
                Message(sender="lead", body="task=abc: test"),
            )
            await loop.run()
            # 两次 wake → 两次 build_member_agent(因为 mark_inbox_read
            # 清了 seen,第二轮 wake 看到同 body 仍当 unread)
            assert len(stub_build_agent) == 2
        asyncio.run(run())
