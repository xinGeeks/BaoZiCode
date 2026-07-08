"""memory 索引溢出处理 — 三态机 NORMAL / WARN / AUTO_COMPRESS / HUMAN_NEEDED。

按 openspec/changes/v0-8-memory-and-sessions/specs/auto-memory/spec.md
"Overflow handling" 段:
- NORMAL (lines < warn_lines AND bytes < warn_bytes) → NOOP
- WARN    (warn ≤ lines < max OR warn ≤ bytes < max) → 提醒 + WARN
- AUTO_COMPRESS (lines ≥ max OR bytes ≥ max) 且本 session 还没压过 → 调度 LLM 压缩
- AUTO_COMPRESS 且本 session 已压过 → HUMAN_NEEDED(只能让用户手动整理)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from baozicode.llm.base import LLMClient, Message

if TYPE_CHECKING:
    from baozicode.config.schema import MemoryConfig
    from baozicode.memory.store import MemoryStore


log = logging.getLogger(__name__)


class OverflowState(str, Enum):
    NORMAL = "normal"
    WARN = "warn"
    AUTO_COMPRESS = "auto_compress"
    HUMAN_NEEDED = "human_needed"


class OverflowAction(str, Enum):
    NOOP = "noop"
    WARN = "warn"
    AUTO_COMPRESS_SCHEDULED = "auto_compress_scheduled"
    HUMAN_NEEDED = "human_needed"


@dataclass(frozen=True)
class _Thresholds:
    warn_lines: int
    max_lines: int
    warn_bytes: int
    max_bytes: int


def _thresholds_from(config: "MemoryConfig") -> _Thresholds:
    return _Thresholds(
        warn_lines=config.warning_lines,
        max_lines=config.index_max_lines,
        warn_bytes=config.warning_bytes,
        max_bytes=config.index_max_bytes,
    )


# 压缩 prompt — 让 LLM 合并重复 / 删空段 / 缩短描述
_COMPRESS_SYSTEM = (
    "你是一个笔记整理助手。给定一个笔记目录的索引(`MEMORY.md`), "
    "请输出整理后的 JSON 操作, 要求: 合并重复条目, 缩短过长 one_liner, "
    "删除完全空的段落, 保留每条笔记的核心信息。\n\n"
    "输出格式(fenced json, 不要写其他文字):\n\n"
    "```json\n"
    "{\n"
    "  \"operations\": [\n"
    "    {\"action\": \"delete\", \"slug\": \"...\", \"reason\": \"...\"},\n"
    "    {\"action\": \"update\", \"slug\": \"...\", \"append\": \"...\"}\n"
    "  ]\n"
    "}\n"
    "```\n\n"
    "约束: 压缩后总行数 ≤ 原始的 70%, 总字节数 ≤ 原始的 70%。"
)


class MemoryOverflowHandler:
    """三态机 — 在每次 updater.update() 后或外部显式调用 check_and_act。"""

    def __init__(
        self,
        config: "MemoryConfig",
        llm: LLMClient,
    ) -> None:
        self._cfg = config
        self._llm = llm
        self._state: OverflowState = OverflowState.NORMAL
        self._session_limiter: int = 0
        self._last_warning_at: datetime | None = None

    @property
    def state(self) -> OverflowState:
        return self._state

    def reset_session_limiter(self) -> None:
        """新 session 开始时调用, 重置本 session 已自动压缩次数。"""
        self._session_limiter = 0

    def _classify(self, lines: int, bytes_: int) -> OverflowState:
        if lines >= self._cfg.index_max_lines or bytes_ >= self._cfg.index_max_bytes:
            return OverflowState.AUTO_COMPRESS
        if lines >= self._cfg.warning_lines or bytes_ >= self._cfg.warning_bytes:
            return OverflowState.WARN
        return OverflowState.NORMAL

    def check_and_act(
        self, store: "MemoryStore", *, auto_compress_runner: Callable[["MemoryStore"], Awaitable[None]] | None = None
    ) -> OverflowAction:
        """读 store 索引, 决定是否触发压缩。

        Args:
            store: 目标 store
            auto_compress_runner: 注入的 async 压缩函数(由 caller 提供,
                通常调 self._auto_compress)。允许注入便于测试。
        """
        idx = store.read_index()
        new_state = self._classify(idx.total_lines, idx.total_bytes)

        # NORMAL → 任何更低的状态(不会发生,但保守)
        if new_state == OverflowState.NORMAL:
            if self._state != OverflowState.NORMAL:
                log.info("memory overflow: %s → NORMAL", self._state.value)
            self._state = OverflowState.NORMAL
            return OverflowAction.NOOP

        if new_state == OverflowState.WARN:
            if self._state == OverflowState.NORMAL:
                log.warning(
                    "memory overflow: 接近上限(lines=%d/%d, bytes=%d/%d) — "
                    "建议整理",
                    idx.total_lines, self._cfg.index_max_lines,
                    idx.total_bytes, self._cfg.index_max_bytes,
                )
                self._last_warning_at = datetime.now(timezone.utc)
            self._state = OverflowState.WARN
            return OverflowAction.WARN

        # new_state == AUTO_COMPRESS
        if self._session_limiter < self._cfg.auto_compress_per_session:
            self._session_limiter += 1
            self._state = OverflowState.AUTO_COMPRESS
            log.warning(
                "memory overflow: 超上限, 自动压缩 (%d/%d) — lines=%d bytes=%d",
                self._session_limiter, self._cfg.auto_compress_per_session,
                idx.total_lines, idx.total_bytes,
            )
            if auto_compress_runner is not None:
                asyncio.create_task(auto_compress_runner(store))
            return OverflowAction.AUTO_COMPRESS_SCHEDULED

        # 已用过本 session 的自动压缩额度
        self._state = OverflowState.HUMAN_NEEDED
        log.error(
            "memory overflow: 本 session 已自动压缩 %d 次仍超限, "
            "请手动整理(lines=%d bytes=%d)",
            self._session_limiter, idx.total_lines, idx.total_bytes,
        )
        return OverflowAction.HUMAN_NEEDED

    async def _auto_compress(
        self, store: "MemoryStore", operations_applier: "Callable[[list[dict]], None] | None" = None
    ) -> None:
        """调 LLM 合并重复 / 缩短描述 / 删空段。

        Args:
            store: 目标 store
            operations_applier: 注入的 operations 应用函数(由 MemoryUpdater 提供,
                走 _apply_operations 的写盘逻辑)。允许注入便于测试。
        """
        idx = store.read_index()
        # 没有 notes 直接 return(不应被 AUTO_COMPRESS 触发)
        if not idx.entries:
            return
        prompt_text = (
            "## 现有索引\n\n"
            f"```\n{store.root / 'MEMORY.md'}"
            "```\n\n"
            "## 现有 notes(节选)\n\n"
        )
        for e in idx.entries[:20]:  # 避免 token 爆炸
            note = store.read_note(e.slug)
            body = note.content[:500] if note else "(无法读取)"
            prompt_text += f"- [{e.type.value}] {e.slug} — {e.title}\n  {body}\n"
        prompt_text += "\n请输出整理操作(fenced json)。"

        try:
            response_text = ""
            async for delta in self._llm.stream(
                [Message(role="user", content=prompt_text)],
                system=_COMPRESS_SYSTEM,
                tools=[],
                cache_breakpoints=None,
            ):
                if delta.type == "text":
                    response_text += delta.text
        except Exception as exc:  # noqa: BLE001
            log.error("memory auto-compress LLM error: %s: %s", type(exc).__name__, exc)
            self._state = OverflowState.HUMAN_NEEDED
            return

        ops = _parse_fenced_json(response_text)
        if ops is None:
            log.error("memory auto-compress: LLM 输出无法解析, 进入 HUMAN_NEEDED")
            self._state = OverflowState.HUMAN_NEEDED
            return

        if operations_applier is not None:
            try:
                operations_applier(ops.get("operations", []))
            except Exception as exc:  # noqa: BLE001
                log.error("memory auto-compress: apply failed: %s: %s", type(exc).__name__, exc)
                self._state = OverflowState.HUMAN_NEEDED
                return

        # 重新评估
        new_idx = store.read_index()
        new_state = self._classify(new_idx.total_lines, new_idx.total_bytes)
        if new_state == OverflowState.NORMAL or new_state == OverflowState.WARN:
            log.info("memory auto-compress: 状态回到 %s", new_state.value)
            self._state = new_state
        else:
            log.error("memory auto-compress: 仍在超限状态, 升级到 HUMAN_NEEDED")
            self._state = OverflowState.HUMAN_NEEDED


# ---- 共享 helper(也供 updater 用)----


_FENCED_JSON_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def _parse_fenced_json(text: str) -> dict[str, Any] | None:
    """从 LLM 文本中提取 ```json ... ``` 块, 解析成 dict。失败 → None。"""
    if not text:
        return None
    m = _FENCED_JSON_RE.search(text)
    if m is None:
        return None
    try:
        parsed = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


__all__ = [
    "MemoryOverflowHandler",
    "OverflowAction",
    "OverflowState",
    "_parse_fenced_json",  # noqa: SLF001 — exported for MemoryUpdater reuse
]
