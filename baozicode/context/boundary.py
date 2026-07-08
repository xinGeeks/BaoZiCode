"""v0.7 上下文压缩边界消息 — system-reminder 包装与摘要前后缀。

两个独立工具函数,供 CompactEngine 和 TUI 共用:

- `wrap_summary_message(summary_text)`:把 LLM 生成的 6 段摘要包成 sticky system-reminder,
  注入到 summary message 的 content 里。`type="context_summary"`,`ttl="sticky"`,
  让 prompt builder 知道这是持久化的上下文。
- `post_compaction_reminder()`:压缩完后追加的边界提醒,告诉 LLM 摘要代替不了原文,
  需要细节就重新调工具。
"""

from __future__ import annotations

__all__ = ["wrap_summary_message", "post_compaction_reminder"]


def wrap_summary_message(summary_text: str) -> str:
    """把摘要文本包装成 sticky system-reminder 字符串。

    返回的字符串直接作为 `Message(role="user", content=...)` 的 content:
        <system-reminder type="context_summary" ttl="sticky">
        {summary_text}
        </system-reminder>

    注意:这里返回的是 string,不是 Message — CompactEngine 决定如何嵌入。
    """
    return (
        '<system-reminder type="context_summary" ttl="sticky">\n'
        f"{summary_text}\n"
        "</system-reminder>"
    )


def post_compaction_reminder() -> str:
    """压缩后追加的提醒:告诉 LLM 摘要不可信,需要细节就重读文件。"""
    return (
        "<system-reminder type=\"post_compaction\">"
        "需要文件细节时请用 Read/Grep/Bash 重新调用对应工具,"
        "不要根据摘要脑补代码或路径"
        "</system-reminder>"
    )