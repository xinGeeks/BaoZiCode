"""memory extraction prompt — 给 LLM 一段对话,让 LLM 输出 add/update/delete 操作。

按 openspec/changes/v0-8-memory-and-sessions/specs/auto-memory/spec.md
"Note extraction prompt" 段:
- 系统提示 4 类笔记定义 + 操作类型(add/update/delete)+ fenced JSON 格式
- 用户提示 = 当前对话末尾 N 轮 + 两层 index 让 LLM 去重
- LLM 严格只输出 fenced JSON,**不调任何工具**
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baozicode.llm.base import Message
from baozicode.memory.schema import MemoryIndex

if TYPE_CHECKING:
    pass


# 4 类笔记 — 短描述 + 例子;LLM 据此分类
_NOTE_TYPES_GUIDE = """\
四类笔记:

1. **user-pref** — 用户偏好 / 习惯
   例:「用户习惯用中文回复」「用户偏好简洁的代码注释」「用户总是要求加类型注解」

2. **correction** — 用户对模型输出的纠正
   例:「不要用 tabs, 用 4 空格缩进」「不要在 PR 描述里写 emoji」「用户否决过 SQL 改写, 保持原始查询」

3. **project** — 项目相关的事实 / 决策
   例:「本项目用 Pydantic v2, 不是 v1」「测试用 pytest-asyncio mode=auto」「项目根 .baozicode/ 下放 sessions / memory」

4. **reference** — 外部参考资料(URL / 文档 / 命令)
   例:「OpenAI 文档 https://platform.openai.com/docs」「重构参考 https://refactoring.guru」「docker compose 启动命令: docker compose up -d」
"""


_OPERATIONS_GUIDE = """\
允许的操作(必须输出为 JSON 数组, 顺序无关):

- **add** — 新笔记(整条新建)
  字段: `action: "add"`, `type: <4 类之一>`, `slug: <kebab-case>`, `title: <一行>`, `content: <正文, 可多行 markdown>`, `tags: [<可选>]`

- **update** — 给已有笔记追加内容(append-only)
  字段: `action: "update"`, `slug: <已有笔记 slug>`, `append: <要追加的文本>`

- **delete** — 删除已有笔记(谨慎, 优先用 update)
  字段: `action: "delete"`, `slug: <要删的 slug>`, `reason: <一句话理由>`

**去重**: 同一件事不要 add 多次, 用 update 追加到现有笔记。
**slug 规则**: 全部小写, 用 `-` 分隔, 不超过 60 字符, 不可与已有 index 重复。
"""


_OUTPUT_FORMAT = """\
**输出格式**: 必须严格按下方示例, 放在一个 ```json``` 代码块里, 不写任何其他文字:

```json
{
  "operations": [
    {
      "action": "add",
      "type": "user-pref",
      "slug": "responds-in-chinese",
      "title": "用户用中文回复",
      "content": "用户在所有项目里都用中文与模型交流。",
      "tags": ["language"]
    },
    {
      "action": "update",
      "slug": "responds-in-chinese",
      "append": "2026-07-08 补充: 用户偏好极简标点, 不喜欢 '...' 缩写。"
    }
  ]
}
```

**严格规则**:
- 不调任何工具(无 function call)
- 不在 ```json``` 块外写解释 / 思考
- 没有任何需要新增 / 更新 / 删除的笔记时, 输出 `{"operations": []}`
"""


def build_extraction_system() -> str:
    """返回 NOTE_EXTRACTION_SYSTEM — 系统提示(三段拼装)。"""
    return (
        "你是一个笔记提取助手。你的任务是从一段对话中提取值得长期记住的事实, "
        "以结构化 JSON 操作的形式输出, 写入两层笔记目录。\n\n"
        + _NOTE_TYPES_GUIDE + "\n"
        + _OPERATIONS_GUIDE + "\n"
        + _OUTPUT_FORMAT
    )


def _format_turns(messages: list[Message], max_chars: int = 8000) -> str:
    """把 messages 序列化成人类可读文本, 截断到 max_chars。"""
    chunks: list[str] = []
    total = 0
    for m in messages:
        role = m.role
        content = m.content
        if isinstance(content, list):
            # 把 content blocks 拍平成文本
            parts: list[str] = []
            for b in content:
                if b.type == "text":
                    parts.append(b.text)
                elif b.type == "tool_use":
                    parts.append(f"[tool_call: {b.name}({b.input})]")
                elif b.type == "tool_result":
                    c = b.content if isinstance(b.content, str) else str(b.content)
                    parts.append(f"[tool_result: {c[:200]}]")
            text = "\n".join(parts)
        else:
            text = content
        chunk = f"[{role}] {text}"
        if total + len(chunk) > max_chars:
            chunks.append("... (后续对话已截断)")
            break
        chunks.append(chunk)
        total += len(chunk)
    return "\n\n".join(chunks)


def build_extraction_prompt(
    recent_messages: list[Message],
    user_index: MemoryIndex,
    project_index: MemoryIndex,
) -> str:
    """构造 user-role 提示 — 给 LLM 看对话 + 两层 index 让其去重。

    Args:
        recent_messages: Agent Loop 末尾 N 条消息(默认 N=recent_turns_for_update=5)
        user_index: 用户级 index(跨项目)
        project_index: 项目级 index(仅本项目)

    Returns:
        一个 user-role Message, content 是拼好的 prompt
    """
    turns_text = _format_turns(recent_messages)

    user_block = user_index.format_for_prompt() or "_(空)_"
    project_block = project_index.format_for_prompt() or "_(空)_"

    return (
        "## 当前对话末尾(用于提取)\n\n"
        f"{turns_text}\n\n"
        "---\n\n"
        "## 已有笔记(用于去重)\n\n"
        f"### 用户级\n{user_block}\n\n"
        f"### 项目级\n{project_block}\n\n"
        "---\n\n"
        "请按系统提示的规则, 输出 ```json``` 代码块。"
    )


def build_extraction_messages(
    recent_messages: list[Message],
    user_index: MemoryIndex,
    project_index: MemoryIndex,
) -> list[Message]:
    """便利函数: 返回 [system, user] 两条 Message, 直接喂给 llm.stream。"""
    return [
        Message(role="system", content=build_extraction_system()),
        Message(role="user", content=build_extraction_prompt(
            recent_messages, user_index, project_index
        )),
    ]


__all__ = [
    "build_extraction_messages",
    "build_extraction_prompt",
    "build_extraction_system",
]
