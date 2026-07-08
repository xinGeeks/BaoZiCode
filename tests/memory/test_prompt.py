"""v0.8 memory/prompt.py 测试 — extraction prompt 构造。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.llm.base import Message, TextBlock, ToolUseBlock
from baozicode.memory.prompt import (
    build_extraction_messages,
    build_extraction_prompt,
    build_extraction_system,
)
from baozicode.memory.schema import IndexEntry, MemoryIndex, NoteType


def test_system_contains_four_note_types() -> None:
    """系统提示应包含 4 类笔记的标识。"""
    sys_prompt = build_extraction_system()
    for t in ("user-pref", "correction", "project", "reference"):
        assert t in sys_prompt, f"system 缺 {t}"
    print("[OK] system 包含 4 类笔记")


def test_system_contains_three_actions() -> None:
    """系统提示应包含 add / update / delete 三种操作。"""
    sys_prompt = build_extraction_system()
    for action in ("add", "update", "delete"):
        assert f"**{action}**" in sys_prompt, f"system 缺 **{action}**"
    print("[OK] system 包含 add/update/delete")


def test_system_contains_fenced_json_example() -> None:
    """系统提示应包含 ```json``` 格式示例。"""
    sys_prompt = build_extraction_system()
    assert "```json" in sys_prompt
    assert "```" in sys_prompt
    print("[OK] system 包含 fenced JSON 示例")


def test_system_forbids_tool_calls() -> None:
    """系统提示明确说不调任何工具。"""
    sys_prompt = build_extraction_system()
    assert "不调任何工具" in sys_prompt or "无 function call" in sys_prompt
    print("[OK] system 明确不调工具")


def test_prompt_includes_recent_messages_text() -> None:
    """用户提示应包含 recent_messages 的内容。"""
    msgs = [
        Message(role="user", content="我想要中文回复"),
        Message(role="assistant", content="好的,以后用中文。"),
    ]
    prompt = build_extraction_prompt(
        msgs, MemoryIndex(), MemoryIndex()
    )
    assert "中文回复" in prompt
    assert "[user]" in prompt
    assert "[assistant]" in prompt
    print("[OK] prompt 含 recent_messages 内容")


def test_prompt_includes_user_and_project_index() -> None:
    """用户提示应分别包含两层 index。"""
    user_idx = MemoryIndex(entries=[
        IndexEntry(slug="uses-chinese", type=NoteType.USER_PREF,
                   title="中文回复", one_liner="用户用中文")
    ])
    project_idx = MemoryIndex(entries=[
        IndexEntry(slug="uses-pydantic", type=NoteType.PROJECT,
                   title="用 Pydantic v2", one_liner="项目依赖 Pydantic v2")
    ])
    msgs = [Message(role="user", content="hi")]
    prompt = build_extraction_prompt(msgs, user_idx, project_idx)

    assert "uses-chinese" in prompt
    assert "uses-pydantic" in prompt
    assert "用户级" in prompt
    assert "项目级" in prompt
    print("[OK] prompt 含两层 index")


def test_prompt_marks_empty_index() -> None:
    """空 index 在 prompt 里应明确标记,而不是消失。"""
    msgs = [Message(role="user", content="hi")]
    prompt = build_extraction_prompt(msgs, MemoryIndex(), MemoryIndex())
    assert "_(空)_" in prompt or "空" in prompt
    print("[OK] prompt 标记空 index")


def test_prompt_truncates_long_conversation() -> None:
    """超长对话应被截断(8000 字符上限),避免 token 爆炸。"""
    big = "x" * 50_000
    msgs = [Message(role="user", content=big)]
    prompt = build_extraction_prompt(msgs, MemoryIndex(), MemoryIndex())
    # 截断后 prompt 长度应远小于原始
    assert len(prompt) < 10_000
    assert "截断" in prompt
    print(f"[OK] prompt 截断到 {len(prompt)} 字符")


def test_messages_contain_system_and_user() -> None:
    """build_extraction_messages 应返回 [system, user] 两条消息。"""
    msgs_in = [Message(role="user", content="hi")]
    out = build_extraction_messages(
        msgs_in, MemoryIndex(), MemoryIndex()
    )
    assert len(out) == 2
    assert out[0].role == "system"
    assert out[1].role == "user"
    # system 内容应包含笔记类型
    sys_content = out[0].content if isinstance(out[0].content, str) else ""
    assert "user-pref" in sys_content


def test_prompt_handles_structured_message_content() -> None:
    """content 是 list[ContentBlock] 时也应能拍平。"""
    msgs = [
        Message(role="assistant", content=[
            TextBlock(text="我读了这个文件: "),
            ToolUseBlock(id="t1", name="Read", input={"file_path": "x.py"}),
        ])
    ]
    prompt = build_extraction_prompt(msgs, MemoryIndex(), MemoryIndex())
    assert "tool_call: Read" in prompt
    print("[OK] prompt 处理结构化 content")


def test_prompt_with_no_messages_returns_minimal() -> None:
    """空 messages 不应崩。"""
    prompt = build_extraction_prompt([], MemoryIndex(), MemoryIndex())
    # 应还有 "现有笔记" 段
    assert "已有笔记" in prompt
