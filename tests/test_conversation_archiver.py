"""v0.8 ConversationManager 接入 SessionArchiver 的集成测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import Message
from baozicode.sessions.archive import SessionArchiver
from baozicode.tools.base import ToolCall, ToolResult


class _FakeArchiver:
    """记录每次 append 调用 + 是否吞异常的 spy。"""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[Message] = []
        self.fail = fail
        self.append_count = 0

    def append(self, msg: Message) -> bool:
        self.append_count += 1
        if self.fail:
            return False  # 模拟 append 失败,ConversationManager 不应抛
        self.calls.append(msg)
        return True


def test_no_archiver_does_not_break(tmp_path: Path) -> None:
    """archiver=None 时,所有 add_* 都不应抛、行为退回 v0.7。"""
    conv = ConversationManager(archiver=None)
    conv.add_user("hi")
    conv.add_assistant("hello")
    conv.add_tool_call(ToolCall(id="t1", name="Read", arguments={"file_path": "a.py"}))
    conv.add_tool_result(
        ToolResult(tool_call_id="t1", content="ok", is_error=False)
    )
    assert len(conv) == 4
    # 反向 clear/set_messages 也不应抛
    conv.clear()
    assert len(conv) == 0
    print("[OK] no archiver: add/clear 行为正常")


def test_archiver_receives_every_add() -> None:
    """每条 add_* 调一次 archiver.append。"""
    spy = _FakeArchiver()
    conv = ConversationManager(archiver=spy)  # type: ignore[arg-type]

    conv.add_user("u1")
    conv.add_assistant("a1")
    conv.add_message(Message(role="user", content="u2"))
    conv.add_tool_call(ToolCall(id="t1", name="Read", arguments={"file_path": "x"}))
    conv.add_tool_result(
        ToolResult(tool_call_id="t1", content="ok", is_error=False)
    )

    assert spy.append_count == 5
    assert [m.role for m in spy.calls] == [
        "user", "assistant", "user", "assistant", "tool"
    ]


def test_archiver_append_failure_does_not_propagate() -> None:
    """archiver.append 返回 False(模拟 IO 失败),ConversationManager 不应抛。"""
    spy = _FakeArchiver(fail=True)
    conv = ConversationManager(archiver=spy)  # type: ignore[arg-type]

    # 应静默继续
    conv.add_user("u")
    conv.add_assistant("a")
    # 消息已入库(archiver 失败不影响内存)
    assert len(conv) == 2


def test_set_archiver_late_binding() -> None:
    """构造后再注入 archiver,新调 add_* 才会 append。"""
    conv = ConversationManager()  # 不传
    conv.add_user("u1")  # 此时没 archiver

    spy = _FakeArchiver()
    conv.set_archiver(spy)  # type: ignore[arg-type]
    conv.add_user("u2")

    assert spy.append_count == 1
    assert spy.calls[0].content == "u2"


def test_set_archiver_none_disables() -> None:
    """set_archiver(None) 后,add_* 停止 append。"""
    spy = _FakeArchiver()
    conv = ConversationManager(archiver=spy)  # type: ignore[arg-type]
    conv.add_user("u1")
    assert spy.append_count == 1

    conv.set_archiver(None)
    conv.add_user("u2")
    assert spy.append_count == 1  # 没新增


def test_clear_does_not_call_archiver() -> None:
    """clear() 是运行时切 session,不应触发 archiver.append(避免 JSONL 重复)。"""
    spy = _FakeArchiver()
    conv = ConversationManager(archiver=spy)  # type: ignore[arg-type]
    conv.add_user("u1")
    conv.add_assistant("a1")
    assert spy.append_count == 2

    conv.clear()
    assert spy.append_count == 2  # 不变


def test_set_messages_does_not_call_archiver() -> None:
    """set_messages() 是 wholesale 替换,不应触发 archiver(避免与原 add_* 重复)。"""
    spy = _FakeArchiver()
    conv = ConversationManager(archiver=spy)  # type: ignore[arg-type]
    conv.add_user("u1")
    assert spy.append_count == 1

    # 模拟 Layer 2 摘要后 set_messages
    conv.set_messages([
        Message(role="user", content="[summary]"),
        Message(role="assistant", content="ok"),
    ])
    assert spy.append_count == 1  # 不变
    assert len(conv) == 2


def test_add_turn_uses_snapshot_to_message() -> None:
    """add_turn(snapshot) 应走 snapshot.to_message() 重建 assistant 消息。"""
    from baozicode.agent.collector import StreamCollector
    from baozicode.llm.base import ContentDelta

    spy = _FakeArchiver()
    conv = ConversationManager(archiver=spy)  # type: ignore[arg-type]

    coll = StreamCollector()
    # 模拟 LLM 吐一个 tool_use
    import asyncio
    async def _feed():
        async for _ in coll.absorb(ContentDelta(type="tool_use", text=ToolCall(
            id="tc-1", name="Bash", arguments={"command": "ls"}
        ))):
            pass
    asyncio.run(_feed())
    snapshot = coll.snapshot()

    conv.add_turn(snapshot)
    assert spy.append_count == 1
    msg = spy.calls[0]
    assert msg.role == "assistant"
    # content 应是 list 含 ToolUseBlock(id="tc-1", name="Bash")
    assert isinstance(msg.content, list)
    assert msg.content[0].id == "tc-1"
    assert msg.content[0].name == "Bash"


def test_real_session_archiver_end_to_end(tmp_path: Path) -> None:
    """用真实 SessionArchiver + 临时 sessions 目录,验证 add_* 落盘到 JSONL。"""
    from baozicode.sessions.archive import SessionArchiver

    sessions_root = tmp_path / "sessions"
    arch = SessionArchiver(sessions_root, session_id="20260708-153000-a1b2")
    conv = ConversationManager(archiver=arch)

    conv.add_user("hello")
    conv.add_assistant("hi there")

    # 关闭 archiver 让 buffer 落盘
    arch.close()

    jsonl_path = sessions_root / "20260708-153000-a1b2.jsonl"
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert '"role": "user"' in lines[0]
    assert '"role": "assistant"' in lines[1]
    print(f"[OK] e2e: 2 条消息落盘到 {jsonl_path.name}")
