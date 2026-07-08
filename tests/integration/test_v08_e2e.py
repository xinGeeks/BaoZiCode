"""v0.8 端到端集成测试 — 三套机制(指令 / 会话 / 笔记)串起来跑。

3 场景:
(a) 新会话启动 → 跑短对话 → JSONL 落盘完整 + (可选)自动笔记触发
(b) 新会话 → 跑对话 → 退出 → 启动并 --resume <sid> → 续接成功
(c) /resume 旧 session,time_gap > 阈值 → 插入 time_gap reminder

不依赖真实 LLM —— Agent 跑改用 stub LLM 直接产出预制消息,
被测的是 App bootstrap / archiver 落盘 / resume 加载 / reminder 注入这一条线。
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.app import BaoZiCodeApp
from baozicode.config.schema import (
    AppConfig,
    BackendConfig,
    MemoryConfig,
    SessionConfig,
)
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message, TextBlock
from baozicode.sessions.archive import SessionArchiver


def _make_config(
    tmp_project_root: Path,
    *,
    memory_enabled: bool = True,
    sessions_enabled: bool = True,
    time_gap_hours: int = 8,
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
        agent=__import__(
            "baozicode.config.schema", fromlist=["AgentConfig"]
        ).AgentConfig(time_gap_threshold_hours=time_gap_hours),
    )


class _ScriptedLLM(LLMClient):
    """按轮次 yield 预制 delta 序列的 stub LLM。

    第 N 轮 yield 序列中第 N 个元素(list[ContentDelta])。
    跑完一轮后会调 on_round_done(N) 让 caller 注入 user 消息 / 关 archiver。
    """

    def __init__(
        self,
        rounds: list[list[ContentDelta]],
        on_round_done=None,
    ) -> None:
        self._rounds = rounds
        self._on_round_done = on_round_done
        self._round_idx = 0

    async def stream(
        self,
        messages: list[Message],
        system=None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        idx = min(self._round_idx, len(self._rounds) - 1)
        deltas = self._rounds[idx]
        for d in deltas:
            yield d
        # 收尾给 UsageStats
        from baozicode.agent.events import UsageStats
        yield ContentDelta(type="usage", payload=UsageStats())
        if self._on_round_done is not None:
            self._on_round_done(idx)
        self._round_idx += 1


def _text_delta(text: str) -> ContentDelta:
    return ContentDelta(type="text", text=text)


def _done_delta() -> ContentDelta:
    return ContentDelta(type="done", text="completed")


# ---- 场景 (a):新会话 → 落 JSONL → (手动)笔记文件创建 ----


def test_e2e_a_new_session_appends_jsonl(tmp_path: Path) -> None:
    """场景 (a) — 启动新会话,跑一段对话,JSONL 落盘包含全部消息。

    验证:
    - App bootstrap 后 archiver 接到 conversation
    - add_user / add_assistant 透明写 JSONL
    - JSONL 行数 = 消息数;行内容可解析
    """
    cfg = _make_config(tmp_path)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)

    # 直接用 conversation(不跑 Agent,避免依赖 StreamCollector)
    app.conversation.add_user("hi")
    app.conversation.add_assistant("hello there")
    app.conversation.add_user("how are you?")
    app.conversation.add_assistant("doing well")

    # 关 archiver 刷盘
    if app.archiver is not None:
        app.archiver.close()

    # 检查 JSONL
    sessions_dir = tmp_path / "sessions"
    jsonl_files = list(sessions_dir.glob("*.jsonl"))
    assert len(jsonl_files) == 1, f"应有 1 个 JSONL,实际 {len(jsonl_files)}"
    jsonl = jsonl_files[0]
    lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 4, f"应有 4 条消息,实际 {len(lines)}"

    # 行可解析为 SessionEntry
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["role"] == "user"
    assert parsed[1]["role"] == "assistant"
    assert parsed[2]["role"] == "user"
    assert parsed[3]["role"] == "assistant"

    # 文本能取回
    assert parsed[0]["blocks"][0]["text"] == "hi"
    assert parsed[1]["blocks"][0]["text"] == "hello there"
    print(f"[OK] 场景 a:JSONL 落盘 {len(lines)} 条消息到 {jsonl.name}")


def test_e2e_a_notes_file_created_by_updater(tmp_path: Path) -> None:
    """场景 (a.2) — 跑一次 LLM 笔记提取,验证 user_dir / project_dir 内出现 .md 文件。

    验证:
    - MemoryUpdater.update() 调 LLM → 解析 JSON → add_note
    - user-pref → user_dir,project → project_dir(自动路由)
    """
    from baozicode.memory import (
        IndexEntry,
        MemoryStore,
        Note,
        NoteType,
    )
    from baozicode.memory.updater import MemoryUpdater

    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    user_store = MemoryStore(user_dir, scope="user")
    project_store = MemoryStore(project_dir, scope="project")

    # 写一个 LLM stub — 返回预制 fenced JSON
    class _NoteExtractionLLM(LLMClient):
        async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
            from baozicode.agent.events import UsageStats
            yield ContentDelta(
                type="text",
                text=(
                    "```json\n"
                    "{\n"
                    '  "operations": [\n'
                    '    {"action": "add", "type": "user-pref", '
                    '"slug": "uses-chinese", "title": "中文", '
                    '"content": "用户偏好中文", "one_liner": "user likes 中文"},\n'
                    '    {"action": "add", "type": "project", '
                    '"slug": "uses-pydantic", "title": "Pydantic v2", '
                    '"content": "项目用 Pydantic v2", "one_liner": "Pydantic v2"}\n'
                    "  ]\n"
                    "}\n"
                    "```"
                ),
            )
            yield ContentDelta(type="usage", text=UsageStats())
            yield ContentDelta(type="done", text="ok")

    updater = MemoryUpdater(
        llm=_NoteExtractionLLM(),  # type: ignore[arg-type]
        user_store=user_store,
        project_store=project_store,
        config=__import__(
            "baozicode.config.schema", fromlist=["MemoryConfig"]
        ).MemoryConfig(),
    )

    # 喂一些近 N 轮消息(snapshot 形式)
    recent = [
        Message(role="user", content="请用中文回复"),
        Message(role="assistant", content="好的"),
        Message(role="user", content="项目用 Pydantic v2"),
        Message(role="assistant", content="明白"),
    ]
    asyncio.run(updater.update(messages_snapshot=recent))

    # user_dir 应有 1 个 .md
    user_notes = list(user_dir.glob("*.md"))
    assert any("uses-chinese" in n.name for n in user_notes), \
        f"user_dir 缺 uses-chinese: {user_notes}"
    # project_dir 应有 1 个 .md
    project_notes = list(project_dir.glob("*.md"))
    assert any("uses-pydantic" in n.name for n in project_notes), \
        f"project_dir 缺 uses-pydantic: {project_notes}"
    print(f"[OK] 场景 a.2:笔记自动创建 user={len(user_notes)} project={len(project_notes)}")


# ---- 场景 (b):新会话 → 跑对话 → 退出 → resume 续接 ----


def test_e2e_b_resume_continues_conversation(tmp_path: Path) -> None:
    """场景 (b) — 起新 App 续接上次会话,conversation 内容应恢复。

    流程:
    1. App 1:跑 2 轮对话,close archiver
    2. 记录旧 sid
    3. App 2(新进程模拟):用同一个 sessions dir,跑 resume_session(sid)
    4. conversation 应包含原消息
    """
    cfg1 = _make_config(tmp_path)
    app1 = BaoZiCodeApp(config=cfg1, project_root=tmp_path)
    old_sid = app1.session_id

    app1.conversation.add_user("first message")
    app1.conversation.add_assistant("first reply")
    app1.conversation.add_user("second message")
    app1.conversation.add_assistant("second reply")
    if app1.archiver is not None:
        app1.archiver.close()

    # ---- "退出" → "重启" → resume ----
    # 新 App 实例(模拟下次启动);不传 resume_target 让 caller 显式调
    cfg2 = _make_config(tmp_path)
    app2 = BaoZiCodeApp(config=cfg2, project_root=tmp_path)
    assert app2.session_id != old_sid  # 新 sid

    # resume 旧 sid
    meta = asyncio.run(app2.resume_session(old_sid))
    assert meta.id == old_sid
    msgs = app2.conversation.to_list()
    assert len(msgs) == 4, f"resume 后应有 4 条消息,实际 {len(msgs)}"
    # 第一条应是 user "first message"
    assert any(
        m.role == "user" and "first message" in (
            m.content if isinstance(m.content, str) else str(m.content)
        )
        for m in msgs
    )
    # 最后一条应是 assistant "second reply"
    last = msgs[-1]
    assert last.role == "assistant"
    assert "second reply" in (
        last.content if isinstance(last.content, str) else str(last.content)
    )
    print(f"[OK] 场景 b:resume {old_sid} 后 conv 含 4 条消息")


def test_e2e_b_resume_writes_to_continued_session(tmp_path: Path) -> None:
    """场景 (b.2) — resume 后继续 add 消息,新 archiver 仍写到新 sid 文件。

    验证 resume 不只读,也让新 archiver 接管(由 caller 决定是否
    调 start_new_session 切 sid;此处 resume 后原 sid 继续写)。
    """
    cfg1 = _make_config(tmp_path)
    app1 = BaoZiCodeApp(config=cfg1, project_root=tmp_path)
    old_sid = app1.session_id
    app1.conversation.add_user("before")
    if app1.archiver is not None:
        app1.archiver.close()

    cfg2 = _make_config(tmp_path)
    app2 = BaoZiCodeApp(config=cfg2, project_root=tmp_path)
    asyncio.run(app2.resume_session(old_sid))
    # 续写
    app2.conversation.add_user("after resume")
    if app2.archiver is not None:
        app2.archiver.close()

    # 老 sid 的 JSONL 应有 2 条消息
    jsonl = tmp_path / "sessions" / f"{old_sid}.jsonl"
    lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2, f"应有 2 条,实际 {len(lines)}"
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["blocks"][0]["text"] == "before"
    assert parsed[1]["blocks"][0]["text"] == "after resume"
    print(f"[OK] 场景 b.2:resume 后续写 {jsonl.name} 含 2 条消息")


# ---- 场景 (c):resume 旧 session,time_gap > 阈值 ----


def test_e2e_c_time_gap_reminder_inserted(tmp_path: Path) -> None:
    """场景 (c) — /resume 一个 N 小时前写的 session,应插入 time_gap reminder。

    流程:
    1. 直接写一个旧 JSONL(last_message_at 比现在早 10h)
    2. App bootstrap,跑 resume_session(旧 sid)
    3. conversation 里应有一条 <system-reminder type="time_gap"> 边界消息
    """
    # 直接写一个 10 小时前的 JSONL
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    old_sid = "20260101-120000-abcd"
    old_time = datetime.now(timezone.utc) - timedelta(hours=10)
    old_time_str = old_time.isoformat()
    jsonl_path = sessions_dir / f"{old_sid}.jsonl"
    jsonl_path.write_text(
        json.dumps({
            "timestamp": old_time_str,
            "role": "user",
            "blocks": [{"type": "text", "text": "10 小时前的旧消息"}],
        }, ensure_ascii=False) + "\n" +
        json.dumps({
            "timestamp": old_time_str,
            "role": "assistant",
            "blocks": [{"type": "text", "text": "10 小时前的旧回复"}],
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # App 用默认 8h 阈值
    cfg = _make_config(tmp_path, time_gap_hours=8)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)

    meta = asyncio.run(app.resume_session(old_sid))
    assert meta.id == old_sid
    msgs = app.conversation.to_list()

    # 应有时间间隔提醒 — 找 type=time_gap
    has_gap_reminder = False
    for m in msgs:
        if m.role == "user":
            content = m.content if isinstance(m.content, str) else str(m.content)
            if "time_gap" in content and "已过" in content:
                has_gap_reminder = True
                break
    assert has_gap_reminder, (
        f"应插入 time_gap reminder,但 messages 为:\n"
        + "\n".join(repr(m.content) for m in msgs)
    )
    print(f"[OK] 场景 c:resume 10h 前 session → time_gap reminder 注入")


def test_e2e_c_no_gap_reminder_when_recent(tmp_path: Path) -> None:
    """场景 (c.2) — 1 小时前写的 session(低于 8h 阈值)→ 不插 time_gap。

    验证 time_gap 提醒的负向路径。
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    old_sid = "20260101-120000-bcde"
    recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_str = recent_time.isoformat()
    (sessions_dir / f"{old_sid}.jsonl").write_text(
        json.dumps({
            "timestamp": recent_str,
            "role": "user",
            "blocks": [{"type": "text", "text": "1 小时前"}],
        }, ensure_ascii=False) + "\n" +
        json.dumps({
            "timestamp": recent_str,
            "role": "assistant",
            "blocks": [{"type": "text", "text": "1 小时前回复"}],
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    cfg = _make_config(tmp_path, time_gap_hours=8)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)
    asyncio.run(app.resume_session(old_sid))
    msgs = app.conversation.to_list()

    has_gap_reminder = any(
        m.role == "user" and "time_gap" in (
            m.content if isinstance(m.content, str) else str(m.content)
        )
        for m in msgs
    )
    assert not has_gap_reminder, "1h 内 resume 不应插 time_gap reminder"
    print(f"[OK] 场景 c.2:1h 内 resume → 不插 time_gap reminder")


# ---- Bonus:三机制联动 — instructions 注入 + memory 注入 + sessions 续接 ----


def test_e2e_full_stack_initialized(tmp_path: Path) -> None:
    """联动测试 — 启动后三机制都接好了:instructions / memory / sessions。"""
    # 写一个项目根 BaoZiCode.md
    (tmp_path / "BaoZiCode.md").write_text(
        "# 项目根指令\n技术栈:Python 3.12\n",
        encoding="utf-8",
    )
    # user_dir 写一条 user-pref 笔记
    from baozicode.memory import (
        IndexEntry,
        MemoryStore,
        Note,
        NoteType,
    )

    user_dir = tmp_path / "user_memory"
    project_dir = tmp_path / "project_memory"
    user_store = MemoryStore(user_dir, scope="user")
    project_store = MemoryStore(project_dir, scope="project")
    user_store.rewrite_index(
        [IndexEntry(slug="u1", type=NoteType.USER_PREF, title="中文", one_liner="u")],
        max_lines=200,
        max_bytes=25_600,
    )

    cfg = _make_config(tmp_path)
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)

    # 三机制全部接好
    assert app.instructions is not None
    assert "项目根指令" in app.instructions.concatenated
    assert app.archiver is not None
    assert app.sessions_meta is not None
    # memory bootstrap 跑过(不会在 app 上挂 store,但 user/project_dir 存在)
    assert user_dir.exists()
    assert project_dir.exists()
    # memory_status 能拉到
    st = app.memory_status()
    assert st["enabled"] is True
    assert st["user"]["count"] == 1
    print(f"[OK] 三机制联动:instructions={len(app.instructions.concatenated)} chars"
          f" + memory enabled + archiver 接好")
