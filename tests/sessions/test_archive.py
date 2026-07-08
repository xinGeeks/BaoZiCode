"""v0.8 SessionArchiver tests — JSONL append + fsync + 序列化失败跳过。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.llm.base import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from baozicode.sessions.archive import SessionArchiver


def test_archiver_init_creates_file_path(tmp_sessions_root: Path) -> None:
    """__init__ 应 mkdir + 解析 self.path(不预创建空文件)。"""
    a = SessionArchiver(tmp_sessions_root, session_id="20260708-153000-a1b2")
    assert tmp_sessions_root.is_dir()
    assert a.path == tmp_sessions_root / "20260708-153000-a1b2.jsonl"


def test_append_writes_one_json_line(archiver: SessionArchiver) -> None:
    msg = Message(role="user", content="hello")
    assert archiver.append(msg) is True
    raw = archiver.path.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["role"] == "user"
    assert parsed["blocks"][0]["type"] == "text"
    assert parsed["blocks"][0]["text"] == "hello"
    assert "timestamp" in parsed


def test_multiple_appends_produce_n_lines(archiver: SessionArchiver) -> None:
    for i in range(5):
        archiver.append(Message(role="user", content=f"msg {i}"))
    raw = archiver.path.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 5
    # 每行独立可解析
    for ln in lines:
        json.loads(ln)


def test_append_tool_result_records_tool_call_id(archiver: SessionArchiver) -> None:
    block = ToolResultBlock(
        tool_use_id="call-abc", content="result", is_error=False
    )
    msg = Message(role="tool", content=[block])
    archiver.append(msg)
    parsed = json.loads(archiver.path.read_text(encoding="utf-8").splitlines()[0])
    assert parsed["role"] == "tool"
    assert parsed["tool_call_id"] == "call-abc"
    assert parsed["blocks"][0]["tool_use_id"] == "call-abc"


def test_append_tool_use_block_in_assistant(archiver: SessionArchiver) -> None:
    msg = Message(
        role="assistant",
        content=[
            TextBlock(text="Reading..."),
            ToolUseBlock(id="t1", name="read", input={"path": "x"}),
        ],
    )
    archiver.append(msg)
    parsed = json.loads(archiver.path.read_text(encoding="utf-8").splitlines()[0])
    assert parsed["role"] == "assistant"
    block_types = [b["type"] for b in parsed["blocks"]]
    assert block_types == ["text", "tool_use"]
    assert parsed["blocks"][1]["id"] == "t1"


def test_bad_message_serialization_logged_and_skipped(
    archiver: SessionArchiver, caplog
) -> None:
    """message.content 含不可序列化对象 → log warning,跳过(返回 False),不抛。"""

    class Weird:
        def __repr__(self) -> str:
            return "Weird()"

    # 直接用 _encode_message 喂不可 json 的对象(因为 TextBlock 不可 pickle 我们的奇怪类型)
    from baozicode.sessions.archive import _encode_message

    # 构造一个会让 json.dumps 失败的对象(例如含 lambda)
    msg = Message(role="user", content=["not a block"])  # type: ignore[arg-type]
    # _encode_message 不会 json.dumps 直接 — 因为它把 blocks 视为 dict 处理
    # 这里改成调 append 自身看效果:把 message.content 强制喂坏
    msg2 = Message(role="user", content="正常文本")  # 这个能序列化
    # 用一个 content=不可序列化 lambda 测试
    msg3 = Message(role="user", content=[TextBlock(text="好")])
    # 走 encode 时如果 TextBlock 的 text 含不可 json 字符...
    # 简化:用 monkey-patch 让 _encode_message 抛
    import baozicode.sessions.archive as archive_mod

    orig = archive_mod._encode_message
    archive_mod._encode_message = lambda _msg: (_ for _ in ()).throw(
        TypeError("synthetic")
    )
    try:
        result = archiver.append(msg2)
    finally:
        archive_mod._encode_message = orig
    assert result is False
    # 文件未写入
    assert not archiver.path.exists()


def test_gitignore_appended_idempotent(
    tmp_project_root: Path, tmp_sessions_root: Path
) -> None:
    """首次启动 → .gitignore 追加 .baozicode/sessions/;第二次启动不重复。"""
    gitignore = tmp_project_root / ".gitignore"
    gitignore.write_text("__pycache__/\n.env\n", encoding="utf-8")

    SessionArchiver(tmp_sessions_root, session_id="sid-1")
    text1 = gitignore.read_text(encoding="utf-8")
    assert ".baozicode/sessions/" in text1

    SessionArchiver(tmp_sessions_root, session_id="sid-2")
    text2 = gitignore.read_text(encoding="utf-8")
    assert text1 == text2  # 第二次没再追加
    assert text2.count(".baozicode/sessions/") == 1


def test_archiver_close_is_noop(archiver: SessionArchiver) -> None:
    """close() 是个 placeholder,目前 no-op。"""
    assert archiver.close() is None