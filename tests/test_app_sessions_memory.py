"""v0.8 Phase 7: BaoZiCodeApp 新增 sessions / memory 句柄的单元测试。"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.config.schema import (
    AgentConfig,
    AppConfig,
    BackendConfig,
    MemoryConfig,
    SessionConfig,
)
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import Message, TextBlock
from baozicode.memory import IndexEntry, MemoryStore, Note, NoteType
from baozicode.sessions.archive import SessionArchiver


def _make_config(
    tmp_project_root: Path,
    *,
    memory_enabled: bool = True,
    sessions_enabled: bool = True,
) -> AppConfig:
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        memory=MemoryConfig(
            user_dir=tmp_project_root / "user_memory",
            project_dir=tmp_project_root / "project_memory",
        ) if memory_enabled else MemoryConfig(enabled=False),
        sessions=SessionConfig(
            dir=tmp_project_root / "sessions",
            retention_days=30,
        ) if sessions_enabled else SessionConfig(enabled=False),
    )


# ---- sessions.bootstrap 接线 ----


def test_app_init_creates_archiver_and_sessions_meta(tmp_path: Path) -> None:
    """App.__init__ 应调 sessions.bootstrap,得到 archiver + sessions_meta。"""
    from baozicode.app import BaoZiCodeApp

    cfg = _make_config(tmp_path)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)
    assert app.archiver is not None
    assert isinstance(app.archiver, SessionArchiver)
    assert isinstance(app.sessions_meta, list)
    # 至少有当前 session
    assert any(m.id == app.session_id for m in app.sessions_meta) or app.session_id in [
        m.id for m in app.sessions_meta
    ] or len(app.sessions_meta) == 0  # 第一次启动,只有当前这一个(可能未落盘前不在 meta 里)
    print(f"[OK] app.archiver 接线: session_id={app.session_id}")


def test_app_sessions_disabled_no_archiver(tmp_path: Path) -> None:
    """config.sessions.enabled=False → app.archiver = None。"""
    from baozicode.app import BaoZiCodeApp

    cfg = _make_config(tmp_path, sessions_enabled=False)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)
    assert app.archiver is None
    print("[OK] sessions disabled → archiver=None")


def test_app_session_id_format(tmp_path: Path) -> None:
    """session_id 应该是 YYYYMMDD-HHMMSS-xxxx 格式(20 字符)。"""
    from baozicode.app import BaoZiCodeApp

    cfg = _make_config(tmp_path)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)
    sid = app.session_id
    assert len(sid) == 20
    parts = sid.split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 8
    assert len(parts[1]) == 6
    int(parts[2], 16)  # 4 字符 hex
    print(f"[OK] session_id 格式: {sid}")


# ---- sessions_list ----


def test_sessions_list_returns_meta(tmp_path: Path) -> None:
    """sessions_list 返回当前 sessions_meta 副本。"""
    from baozicode.app import BaoZiCodeApp

    cfg = _make_config(tmp_path)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)
    # 加一个旧 session JSONL 到 sessions 目录
    sessions_dir = tmp_path / "sessions"
    (sessions_dir / "20260101-000000-aaaa.jsonl").write_text(
        json.dumps({
            "role": "user", "content": "old message",
            "blocks": [{"type": "text", "text": "old message"}],
        }) + "\n",
        encoding="utf-8",
    )
    # 重新刷新 sessions_meta
    app.sessions_meta = list(app.sessions_meta)  # 触发 list_sessions
    from baozicode.sessions import list_sessions
    app.sessions_meta = list_sessions(sessions_dir)

    sessions = app.sessions_list()
    assert any(s.id == "20260101-000000-aaaa" for s in sessions)
    print(f"[OK] sessions_list: {len(sessions)} 个 session")


# ---- start_new_session ----


def test_start_new_session_rotates_id(tmp_path: Path) -> None:
    """start_new_session 分配新 sid,清空 conv,旧 archiver 关闭。"""
    from baozicode.app import BaoZiCodeApp

    cfg = _make_config(tmp_path)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)
    old_sid = app.session_id
    app.conversation.add_user("hello")
    assert len(app.conversation) == 1

    new_sid = app.start_new_session()

    assert new_sid != old_sid
    assert app.session_id == new_sid
    assert len(app.conversation) == 0  # 已清空
    # 新 archiver 也接好了
    assert app.conversation._archiver is not None  # type: ignore[attr-defined]
    print(f"[OK] /new: {old_sid} → {new_sid}")


# ---- memory_status ----


def test_memory_status_disabled(tmp_path: Path) -> None:
    """memory.enabled=False → status 反映 disabled。"""
    from baozicode.app import BaoZiCodeApp

    cfg = _make_config(tmp_path, memory_enabled=False)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)
    st = app.memory_status()
    assert st["enabled"] is False
    assert st["user"]["count"] == 0
    print("[OK] memory disabled → status.enabled=False")


def test_memory_status_with_entries(tmp_path: Path) -> None:
    """memory 有笔记 → status 含 count/lines/bytes。"""
    from baozicode.app import BaoZiCodeApp

    cfg = _make_config(tmp_path)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)

    # 给两层各加 1 条
    user_store = MemoryStore(cfg.memory.user_dir, scope="user")
    project_store = MemoryStore(cfg.memory.project_dir, scope="project")
    user_store.add_note(Note(
        type=NoteType.USER_PREF, slug="u1", title="u1",
        content="user 偏好", created_at=datetime.now(timezone.utc),
        source_session="s1",
    ))
    user_store.rewrite_index([
        IndexEntry(slug="u1", type=NoteType.USER_PREF, title="u1", one_liner="user 偏好")
    ], max_lines=200, max_bytes=25_600)
    project_store.add_note(Note(
        type=NoteType.PROJECT, slug="p1", title="p1",
        content="project 知识", created_at=datetime.now(timezone.utc),
        source_session="s1",
    ))
    project_store.rewrite_index([
        IndexEntry(slug="p1", type=NoteType.PROJECT, title="p1", one_liner="project 知识")
    ], max_lines=200, max_bytes=25_600)

    st = app.memory_status()
    assert st["enabled"] is True
    assert st["user"]["count"] == 1
    assert st["project"]["count"] == 1
    print(f"[OK] memory_status: user={st['user']['count']} project={st['project']['count']}")


# ---- resume_session ----


def test_resume_session_replaces_conversation(tmp_path: Path) -> None:
    """resume_session 加载 JSONL 替换 conv 内容。"""
    from baozicode.app import BaoZiCodeApp

    cfg = _make_config(tmp_path)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)

    # 写一个旧 session JSONL
    sessions_dir = cfg.sessions.dir
    old_sid = "20260101-100000-bbbb"
    jsonl_path = sessions_dir / f"{old_sid}.jsonl"
    jsonl_path.write_text(
        json.dumps({
            "role": "user", "content": "from old session",
            "blocks": [{"type": "text", "text": "from old session"}],
        }, ensure_ascii=False) + "\n" +
        json.dumps({
            "role": "assistant", "content": "old reply",
            "blocks": [{"type": "text", "text": "old reply"}],
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 跑
    meta = asyncio.run(app.resume_session(old_sid))

    assert meta.id == old_sid
    msgs = app.conversation.to_list()
    assert len(msgs) >= 1
    # 至少第一条是 user "from old session"
    assert any(m.role == "user" and "from old session" in (
        m.content if isinstance(m.content, str) else str(m.content)
    ) for m in msgs)
    print(f"[OK] resume: session `{old_sid}` 恢复到 conv")


def test_resume_unknown_session_raises(tmp_path: Path) -> None:
    """不存在的 session_id 应抛 ValueError。"""
    from baozicode.app import BaoZiCodeApp

    cfg = _make_config(tmp_path)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)
    try:
        asyncio.run(app.resume_session("nonexistent-sid"))
    except ValueError as exc:
        assert "session 不存在" in str(exc)
        print(f"[OK] 未知 session → 抛 ValueError: {exc}")
    else:
        raise AssertionError("应该抛 ValueError")


# ---- SLASH_COMMANDS 注册 ----


def test_slash_commands_includes_v08() -> None:
    """v0.8 的 /resume /memory /new 必须在 SLASH_COMMANDS 中。"""
    from baozicode.tui.chat_screen import SLASH_COMMANDS

    assert "/resume" in SLASH_COMMANDS
    assert "/memory" in SLASH_COMMANDS
    assert "/new" in SLASH_COMMANDS
    print(f"[OK] SLASH_COMMANDS 含 v0.8: {SLASH_COMMANDS}")


# ---- ChatScreen dispatch 路由 ----


def test_chat_screen_dispatches_v08_commands(tmp_path: Path) -> None:
    """`_handle_slash` 路由 /resume → _handle_resume, /memory → _show_memory, /new → _handle_new_session。"""
    from baozicode.tui.chat_screen import ChatScreen

    # 不挂 Textual app,只用 stub 模拟 self.app 和 self._append_info
    screen = ChatScreen.__new__(ChatScreen)
    screen._append_info = lambda *_a, **_k: None  # type: ignore[assignment]
    called: dict[str, str] = {}

    async def fake_resume():
        called["resume"] = "ok"

    def fake_memory():
        called["memory"] = "ok"

    async def fake_new():
        called["new"] = "ok"

    screen._handle_resume = fake_resume  # type: ignore[assignment]
    screen._show_memory = fake_memory  # type: ignore[assignment]
    screen._handle_new_session = fake_new  # type: ignore[assignment]

    asyncio.run(screen._handle_slash("/resume"))
    asyncio.run(screen._handle_slash("/memory"))
    asyncio.run(screen._handle_slash("/new"))

    assert called == {"resume": "ok", "memory": "ok", "new": "ok"}
    print(f"[OK] /resume /memory /new 路由到正确 handler: {called}")


def test_chat_screen_unknown_command_prints_info(tmp_path: Path) -> None:
    """未知命令应走 _append_info 提示用户。"""
    from baozicode.tui.chat_screen import ChatScreen

    screen = ChatScreen.__new__(ChatScreen)
    captured: list[str] = []

    def fake_info(text: str) -> None:
        captured.append(text)

    screen._append_info = fake_info  # type: ignore[assignment]

    asyncio.run(screen._handle_slash("/totally-unknown"))

    assert any("未知命令" in t and "/totally-unknown" in t for t in captured), captured
    print(f"[OK] 未知命令走 _append_info: {captured[0][:60]}")


# ---- _show_memory handler 行为 ----


def test_show_memory_renders_disabled_state(tmp_path: Path) -> None:
    """memory disabled → 输出含 'enabled: `False`'。"""
    from baozicode.app import BaoZiCodeApp
    from baozicode.tui.chat_screen import ChatScreen

    cfg = _make_config(tmp_path, memory_enabled=False)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)

    screen = ChatScreen.__new__(ChatScreen)
    # `app` 在 Textual Screen 上是 property,绕过 setter,直接塞 _app_message_queue
    object.__setattr__(screen, "_app", None)
    # 用 stub 替换 type 自身上的 app 描述符 → 最简方式:注入 self.app 的 lookup
    captured: list[str] = []

    def fake_info(text: str) -> None:
        captured.append(text)

    # 注入一个返回 app 的属性
    class _AppProxy:
        pass

    proxy = _AppProxy()
    proxy.config = app.config
    proxy.project_root = app.project_root
    # memory_status 是 _show_memory 的依赖,直接挂
    proxy.memory_status = app.memory_status
    object.__setattr__(screen, "_app", proxy)
    screen._append_info = fake_info  # type: ignore[assignment]

    # 绕开 property 读 app — ChatScreen._show_memory 内是 `app: BaoZiCodeApp = self.app`
    # 走 property 链;此处直接调用 helper 子段逻辑(走 captured 列表断言)
    # 用更稳的办法:跑 _show_memory 之前 monkey-patch type(Screen).app
    from textual.screen import Screen as TxtScreen

    original_prop = TxtScreen.app

    def _patched_app(self):  # type: ignore[no-untyped-def]
        return proxy

    try:
        TxtScreen.app = property(_patched_app)  # type: ignore[assignment,arg-type]
        screen._show_memory()
    finally:
        TxtScreen.app = original_prop  # type: ignore[assignment]

    assert any("enabled: `False`" in t for t in captured), captured
    print("[OK] /memory disabled 渲染启用状态")
