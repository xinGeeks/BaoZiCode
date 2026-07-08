"""v0.8 memory/updater.py 测试 — 异步更新逻辑。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncIterator

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.config.schema import MemoryConfig
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.memory import MemoryUpdater
from baozicode.memory.overflow import MemoryOverflowHandler


class _ScriptedLLM(LLMClient):
    """按 responses 序列返回 stream 文本。"""

    def __init__(self, responses: list[str], fail_on_call: int | None = None) -> None:
        self.responses = responses
        self.call_count = 0
        self.last_messages: list[Message] = []
        self.fail_on_call = fail_on_call

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        self.last_messages = list(messages)
        if self.fail_on_call is not None and self.call_count == self.fail_on_call:
            raise RuntimeError("simulated LLM outage")
        text = self.responses[self.call_count - 1]
        if text:
            yield ContentDelta(type="text", text=text)


@pytest.fixture
def small_memory_config(tmp_project_root: Path) -> MemoryConfig:
    return MemoryConfig(
        user_dir=tmp_project_root / "user_memory",
        project_dir=tmp_project_root / "project_memory",
        recent_turns_for_update=5,
    )


@pytest.fixture
def user_store(tmp_project_root: Path, small_memory_config: MemoryConfig):
    from baozicode.memory import MemoryStore
    return MemoryStore(small_memory_config.user_dir, scope="user")


@pytest.fixture
def project_store(tmp_project_root: Path, small_memory_config: MemoryConfig):
    from baozicode.memory import MemoryStore
    return MemoryStore(small_memory_config.project_dir, scope="project")


# ---- happy path ----


async def test_add_operation_creates_user_note(
    user_store, project_store, small_memory_config
) -> None:
    """LLM 输出 add(user-pref) → user store 出现新 note。"""
    llm = _ScriptedLLM(['''```json
{
  "operations": [
    {
      "action": "add",
      "type": "user-pref",
      "slug": "uses-chinese",
      "title": "用户用中文",
      "content": "用户偏好中文。",
      "tags": ["language"]
    }
  ]
}
```'''])
    updater = MemoryUpdater(
        llm=llm,
        user_store=user_store,
        project_store=project_store,
        config=small_memory_config,
        current_session_id_fn=lambda: "sess-1",
    )
    snapshot = [
        Message(role="user", content="用中文回复我"),
        Message(role="assistant", content="好的"),
    ]
    await updater.update(snapshot)

    note = user_store.read_note("uses-chinese")
    assert note is not None
    assert note.type.value == "user-pref"
    assert note.source_session == "sess-1"
    assert updater.applied_ops_count == 1
    print("[OK] add → user store 新增 1 条 note")


async def test_add_operation_routes_to_project(
    user_store, project_store, small_memory_config
) -> None:
    """project / reference 类型应路由到 project store。"""
    llm = _ScriptedLLM(['''```json
{
  "operations": [
    {
      "action": "add",
      "type": "project",
      "slug": "uses-pydantic-v2",
      "title": "用 Pydantic v2",
      "content": "项目依赖 Pydantic v2。",
      "tags": []
    }
  ]
}
```'''])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config,
    )
    await updater.update([Message(role="user", content="我们用 Pydantic v2")])

    assert project_store.read_note("uses-pydantic-v2") is not None
    assert user_store.read_note("uses-pydantic-v2") is None
    print("[OK] add(project) → project store")


async def test_update_operation_appends(
    user_store, project_store, small_memory_config
) -> None:
    """update 操作应 append 到已有 note。"""
    from datetime import datetime, timezone
    from baozicode.memory import Note, NoteType
    user_store.add_note(Note(
        type=NoteType.USER_PREF, slug="uses-chinese", title="中文",
        content="原文", created_at=datetime.now(timezone.utc),
        source_session="sess-1",
    ))

    llm = _ScriptedLLM(['''```json
{
  "operations": [
    {
      "action": "update",
      "slug": "uses-chinese",
      "append": "补充: 也用极简标点。"
    }
  ]
}
```'''])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config, current_session_id_fn=lambda: "sess-2",
    )
    await updater.update([Message(role="user", content="也用极简标点")])

    note = user_store.read_note("uses-chinese")
    assert note is not None
    assert "原文" in note.content
    assert "极简标点" in note.content
    print("[OK] update → 追加而非覆盖")


async def test_delete_operation_removes_note(
    user_store, project_store, small_memory_config
) -> None:
    """delete 操作(本 session 的 note)应被删除。"""
    from datetime import datetime, timezone
    from baozicode.memory import Note, NoteType
    user_store.add_note(Note(
        type=NoteType.USER_PREF, slug="old-pref", title="旧偏好",
        content="x", created_at=datetime.now(timezone.utc),
        source_session="sess-1",
    ))

    llm = _ScriptedLLM(['''```json
{
  "operations": [
    {"action": "delete", "slug": "old-pref", "reason": "已废弃"}
  ]
}
```'''])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config, current_session_id_fn=lambda: "sess-1",
    )
    await updater.update([Message(role="user", content="删掉那条")])

    assert user_store.read_note("old-pref") is None
    print("[OK] delete → 删除本 session note")


async def test_cross_session_delete_rejected(
    user_store, project_store, small_memory_config
) -> None:
    """跨 session 删他 session 写的 note 应被拒。"""
    from datetime import datetime, timezone
    from baozicode.memory import Note, NoteType
    user_store.add_note(Note(
        type=NoteType.USER_PREF, slug="old-pref", title="旧偏好",
        content="x", created_at=datetime.now(timezone.utc),
        source_session="sess-OLD",
    ))

    llm = _ScriptedLLM(['''```json
{
  "operations": [
    {"action": "delete", "slug": "old-pref", "reason": ""}
  ]
}
```'''])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config, current_session_id_fn=lambda: "sess-NEW",
    )
    await updater.update([Message(role="user", content="尝试删别的 session")])

    # 应保留
    assert user_store.read_note("old-pref") is not None
    print("[OK] 跨 session delete 被拒")


# ---- error paths ----


async def test_llm_stream_error_silent_skip(
    user_store, project_store, small_memory_config
) -> None:
    """LLM stream 抛异常 → 静默跳过, 不崩。"""
    llm = _ScriptedLLM([""], fail_on_call=1)
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config,
    )
    # 不应抛
    await updater.update([Message(role="user", content="hi")])
    assert updater.llm_error_count == 1
    assert updater.applied_ops_count == 0
    print("[OK] LLM 错误 → 静默跳过")


async def test_invalid_json_silent_skip(
    user_store, project_store, small_memory_config
) -> None:
    """LLM 输出无 fenced JSON → 静默跳过。"""
    llm = _ScriptedLLM(["我没有可写入的笔记。"])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config,
    )
    await updater.update([Message(role="user", content="hi")])
    assert updater.parse_failures == 1
    assert updater.applied_ops_count == 0
    print("[OK] 无 fenced JSON → 静默跳过")


async def test_empty_operations_array_is_ok(
    user_store, project_store, small_memory_config
) -> None:
    """LLM 输出 {\"operations\": []} → 不报错, 不写盘。"""
    llm = _ScriptedLLM(['''```json
{"operations": []}
```'''])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config,
    )
    await updater.update([Message(role="user", content="闲聊")])
    assert updater.applied_ops_count == 0
    print("[OK] 空 operations → 正常处理")


async def test_update_unknown_slug_skipped(
    user_store, project_store, small_memory_config
) -> None:
    """update 不存在的 slug → 跳过。"""
    llm = _ScriptedLLM(['''```json
{
  "operations": [
    {"action": "update", "slug": "nonexistent", "append": "x"}
  ]
}
```'''])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config,
    )
    await updater.update([Message(role="user", content="hi")])
    assert updater.applied_ops_count == 0
    print("[OK] update 不存在的 slug → 跳过")


# ---- overflow 联动 ----


async def test_overflow_check_called_after_update(
    user_store, project_store, small_memory_config
) -> None:
    """update 完后应调 overflow.check_and_act。"""
    called: list[str] = []

    class _SpyOverflow:
        def check_and_act(self, store, *, auto_compress_runner=None):
            called.append(store.scope)
            from baozicode.memory.overflow import OverflowAction
            return OverflowAction.NOOP

        async def _auto_compress(self, store):  # noqa: D401
            return None

    llm = _ScriptedLLM(['''```json
{"operations": []}
```'''])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config,
        overflow=_SpyOverflow(),  # type: ignore[arg-type]
    )
    await updater.update([Message(role="user", content="hi")])
    assert "project" in called
    print("[OK] update 后调 overflow.check_and_act(project store)")


async def test_no_session_id_fn_disables_cross_session_check(
    user_store, project_store, small_memory_config
) -> None:
    """current_session_id_fn=None 时不抛, 也不强加跨 session 保护。"""
    from datetime import datetime, timezone
    from baozicode.memory import Note, NoteType
    user_store.add_note(Note(
        type=NoteType.USER_PREF, slug="x", title="x",
        content="x", created_at=datetime.now(timezone.utc),
        source_session="sess-OLD",
    ))

    llm = _ScriptedLLM(['''```json
{
  "operations": [
    {"action": "delete", "slug": "x", "reason": ""}
  ]
}
```'''])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config,
        current_session_id_fn=None,  # 关键
    )
    # 不应抛
    await updater.update([Message(role="user", content="hi")])
    # fn=None 时 delete 没保护机制, note 被删除
    assert user_store.read_note("x") is None
    print("[OK] current_session_id_fn=None → 不强加保护")


async def test_multiple_operations_in_one_call(
    user_store, project_store, small_memory_config
) -> None:
    """一次 LLM 输出 3 个 operations 应全部应用。"""
    llm = _ScriptedLLM(['''```json
{
  "operations": [
    {"action": "add", "type": "user-pref", "slug": "p1",
     "title": "p1", "content": "c1", "tags": []},
    {"action": "add", "type": "project", "slug": "p2",
     "title": "p2", "content": "c2", "tags": []},
    {"action": "add", "type": "reference", "slug": "p3",
     "title": "p3", "content": "c3", "tags": []}
  ]
}
```'''])
    updater = MemoryUpdater(
        llm=llm, user_store=user_store, project_store=project_store,
        config=small_memory_config,
    )
    await updater.update([Message(role="user", content="x")] * 3)

    assert user_store.read_note("p1") is not None
    assert project_store.read_note("p2") is not None
    assert project_store.read_note("p3") is not None
    assert updater.applied_ops_count == 3
    print("[OK] 一次 LLM 调用应用 3 个 ops")
