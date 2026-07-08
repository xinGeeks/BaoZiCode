"""SessionArchiver — 单 session 的 JSONL append-only 写入。

按 openspec/changes/v0-8-memory-and-sessions/specs/session-archive/spec.md:
- 文件:`<root>/<session_id>.jsonl`,每条 Message 一行
- 写入:`open("a")` + `flush() + os.fsync()` 保崩溃安全
- 序列化失败 → log warning + skip(不阻断 conversation)
- `.gitignore` 由本模块在 init 时确保含 `.baozicode/sessions/`
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from baozicode.llm.base import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

log = logging.getLogger(__name__)


_GITIGNORE_LINE = ".baozicode/sessions/"
_GITIGNORE_PATH = Path(".gitignore")


def _default_converter(value: object) -> object:
    """`json.dumps(default=...)` 的回调:兜底 datetime / Path 等。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _encode_blocks(blocks: list[object]) -> list[dict]:
    """把 Message.content 里的 ContentBlock 列表转成可 JSON 序列化的 dict 列表。"""
    out: list[dict] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            out.append({"type": "text", "text": b.text})
        elif isinstance(b, ToolUseBlock):
            out.append(
                {
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": b.input,
                }
            )
        elif isinstance(b, ToolResultBlock):
            entry: dict = {
                "type": "tool_result",
                "tool_use_id": b.tool_use_id,
                "content": b.content,
                "is_error": b.is_error,
            }
            if b.offloaded_to is not None:
                entry["offloaded_to"] = str(b.offloaded_to)
            if b.original_size:
                entry["original_size"] = b.original_size
            out.append(entry)
        else:
            # 未知类型 — 退化为 repr,序列化失败时上层 try 兜底
            out.append({"type": "unknown", "repr": repr(b)})
    return out


def _encode_message(message: Message) -> dict:
    """Message → dict(JSON-serializable)。"""
    role = message.role
    blocks: list[dict] = []
    tool_call_id: str | None = None
    if isinstance(message.content, str):
        # 纯文本 user/assistant message
        if message.content:
            blocks.append({"type": "text", "text": message.content})
    else:
        blocks = _encode_blocks(message.content)
        # tool role 单条 ToolResultBlock → 提取 tool_call_id
        if role == "tool" and len(blocks) == 1 and blocks[0].get("type") == "tool_result":
            tool_call_id = blocks[0].get("tool_use_id")
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "blocks": blocks,
        "tool_call_id": tool_call_id,
    }


class SessionArchiver:
    """单个 session 的 JSONL 追加器。

    - 单实例对应一个 `<root>/<session_id>.jsonl`
    - 多线程安全(append 持 lock)
    - 序列化错误:log warning 后跳过,不动 conversation
    """

    def __init__(self, root: Path, session_id: str) -> None:
        self.root = Path(root)
        self.session_id = session_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f"{session_id}.jsonl"
        self._lock = Lock()
        self._ensure_gitignore()

    # ---- public API ----

    def append(self, message: Message) -> bool:
        """追加一行 JSON。失败返回 False(已 log warning)。"""
        try:
            payload = _encode_message(message)
            line = json.dumps(payload, ensure_ascii=False, default=_default_converter)
        except (TypeError, ValueError) as exc:
            log.warning(
                "sessions: serialize message failed for session %s: %s",
                self.session_id, exc,
            )
            return False
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as exc:
                log.warning(
                    "sessions: append failed for session %s: %s",
                    self.session_id, exc,
                )
                return False
        return True

    def close(self) -> None:
        """占位 hook — 当前实现是 open/close per-write,无需做。

        留作 future fsync batching 的扩展点。
        """
        return None

    # ---- internals ----

    def _ensure_gitignore(self) -> None:
        """确保 `<project_root>/.gitignore` 含 `.baozicode/sessions/`(idempotent)。

        找的是 `<root>.parent.parent.parent.parent`(从 sessions_dir 反推 project_root)
        — 假设 sessions_dir = `<project>/.baozicode/sessions/`,所以往上数 3 层。
        """
        # sessions_root = <project>/.baozicode/sessions
        #   parent = .baozicode
        #   parent.parent = <project>
        project_root = self.root.parent.parent
        gitignore = project_root / _GITIGNORE_PATH
        if not gitignore.exists():
            return
        try:
            existing = gitignore.read_text(encoding="utf-8")
        except OSError:
            return
        for line in existing.splitlines():
            stripped = line.strip()
            if (
                stripped == _GITIGNORE_LINE
                or stripped.startswith(_GITIGNORE_LINE + " ")
                or stripped.startswith(_GITIGNORE_LINE + "#")
            ):
                return
        new_content = existing
        if new_content and not new_content.endswith("\n"):
            new_content += "\n"
        new_content += _GITIGNORE_LINE + "\n"
        try:
            gitignore.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            log.warning("sessions: failed to update .gitignore: %s", exc)


__all__ = ["SessionArchiver", "_encode_message"]