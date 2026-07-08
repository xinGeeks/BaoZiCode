"""v0.7 Layer 2 compact engine — LLM 生成 6 段摘要 + 解析器 + 熔断器。

流程:`compact(messages)` →
1. `_partition_tail`:把 messages 拆 head / tail,保留近期(满足 token + count 双阈值)
2. `_build_summary_prompt`:序列化 head,构造禁止调工具的摘要 prompt
3. `_call_summary_llm`:调用 LLM(tools=[],cache_breakpoints=None)
4. `_parse_summary`:从 `---ANALYSIS---` / `---SUMMARY---` / `---END_SUMMARY---` 三段里
   抽取 SUMMARY 段
5. 熔断:连续 N 次失败 → `CompactionError("compaction failed after N attempts")`
6. 成功 → 返回 `[summary_message, post_compaction_reminder, *tail]`
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from baozicode.context.boundary import post_compaction_reminder, wrap_summary_message
from baozicode.context.estimator import estimate_message_tokens, estimate_messages_tokens
from baozicode.context.schema import (
    CompactionError,
    CompactionTelemetry,
    ContextConfig,
)
from baozicode.llm.base import (
    ContentDelta,
    LLMClient,
    Message,
    TextBlock,
)

if TYPE_CHECKING:
    pass

__all__ = ["CompactEngine"]


class CompactEngine:
    """v0.7 Layer 2 摘要器 — 把 history 头部的 N 条消息替换成 LLM 生成的 6 段摘要。

    熔断:`_consecutive_failures` 计数,达到 `max_consecutive_failures` 抛
    `CompactionError`(已 import 到 `baozicode.context.CompactionError`)。
    """

    # 6 段 section 头 + 一行描述(prompt 模板用)
    _SECTION_HEADERS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("## Goal", "用户的总目标"),
        ("## Progress", "已完成的工作(改了哪些文件,跑通了什么)"),
        ("## Decisions", "关键决策及其理由(为什么这么做)"),
        ("## Files", "读/写过的关键文件 + 路径"),
        ("## Open Issues", "还没解决的疑问或报错"),
        ("## Next", "下一步打算做什么"),
    )

    def __init__(
        self,
        llm: LLMClient,
        config: ContextConfig,
        telemetry: CompactionTelemetry,
    ) -> None:
        self._llm = llm
        self._config = config
        self._telemetry = telemetry
        self._consecutive_failures = 0

    async def compact(self, messages: list[Message]) -> tuple[list[Message], int]:
        """主入口:对 messages 做摘要压缩,返回 (new_messages, tokens_after)。

        失败语义:
        - 单次失败(stream 异常 / 解析失败 / 摘要后 token 仍超阈值):重试一次
        - 连续 `max_consecutive_failures` 次失败 → 抛 `CompactionError`
        """
        head, tail = self._partition_tail(messages)
        if not head:
            # 没有可摘要的内容(全部留作 tail)— 不算失败,但也不触发
            return messages, estimate_messages_tokens(messages)
        tokens_before = estimate_messages_tokens(messages)
        budget = self._config.context_window_tokens - self._config.reserve_tokens
        # 重试循环
        last_error: Exception | None = None
        for attempt in range(self._config.max_consecutive_failures):
            try:
                summary_text = await self._call_summary_llm(head)
                parsed = self._parse_summary(summary_text)
                if parsed is None:
                    raise RuntimeError("missing ---SUMMARY--- section")
                if len(parsed) < 50:
                    raise RuntimeError(f"summary too short ({len(parsed)} chars)")
                # 构造新 messages: summary + post_compaction + tail
                new_messages: list[Message] = [
                    Message(role="user", content=wrap_summary_message(parsed)),
                    Message(role="user", content=post_compaction_reminder()),
                    *tail,
                ]
                tokens_after = estimate_messages_tokens(new_messages)
                if tokens_after > budget:
                    raise RuntimeError(
                        f"post-summary tokens ({tokens_after}) still exceed budget ({budget})"
                    )
                # 成功 — 重置熔断计数 + 更新 telemetry
                self._consecutive_failures = 0
                self._telemetry.compaction_count += 1
                self._telemetry.total_tokens_saved += max(0, tokens_before - tokens_after)
                self._telemetry.last_compact_at = datetime.now()
                return new_messages, tokens_after
            except Exception as exc:
                last_error = exc
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._config.max_consecutive_failures:
                    raise CompactionError(
                        f"compaction failed after {self._consecutive_failures} attempts: {exc}"
                    ) from exc
        # 理论上到不了这里(max_consecutive_failures ≥ 1 时最后一次循环会 raise)
        raise CompactionError(f"compaction failed: {last_error}")

    # ---- internals ----

    def _partition_tail(
        self, messages: list[Message]
    ) -> tuple[list[Message], list[Message]]:
        """从尾部往前数,直到同时满足 token + count 双阈值。

        例:`recent_window_min_messages=5, recent_window_tokens=10000`,
        假设 msg size [1000, 1000, ..., 1000] tokens → 至少 10 条才能凑够 10K。
        """
        if not messages:
            return [], []
        tail: list[Message] = []
        tail_tokens = 0
        # 从尾往头数
        for msg in reversed(messages):
            msg_tokens = _estimate_one(msg)
            # 任何时候,如果单条消息本身就 ≥ recent_window_tokens,
            # tail 就只这一条(token 阈值主导;count 算"满足")
            if msg_tokens >= self._config.recent_window_tokens:
                tail = [msg]
                tail_tokens = msg_tokens
                break
            prospective_tokens = tail_tokens + msg_tokens
            prospective_count = len(tail) + 1
            token_ok = prospective_tokens >= self._config.recent_window_tokens
            count_ok = prospective_count >= self._config.recent_window_min_messages
            tail.insert(0, msg)
            tail_tokens = prospective_tokens
            if token_ok and count_ok:
                break
        head = messages[: len(messages) - len(tail)]
        return head, tail

    def _build_summary_prompt(self, head: list[Message]) -> str:
        """序列化 head + 摘要指令。"""
        # 序列化 head(简化为 to_dict)
        serialized: list[str] = []
        for i, msg in enumerate(head):
            serialized.append(f"[message {i + 1}] role={msg.role}")
            if isinstance(msg.content, str):
                serialized.append(msg.content)
            else:
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        serialized.append(block.text)
                    else:
                        serialized.append(
                            f"<{type(block).__name__}: "
                            f"{json.dumps(block.to_dict() if hasattr(block, 'to_dict') else str(block))}>"
                        )
            serialized.append("")
        head_text = "\n".join(serialized)
        # 6 段 section 行
        section_lines = "\n".join(
            f"{header} — {desc}" for header, desc in self._SECTION_HEADERS
        )
        return (
            "你是上下文压缩助手。下面是一段较早的对话历史,"
            "请提炼成结构化摘要,后续会替代原文参与后续对话。\n\n"
            "【禁止】调用任何工具(Read / Grep / Bash / Write 等一律不可用),\n"
            "本任务纯文本生成,不能影响文件系统。\n\n"
            "【步骤】\n"
            "1. 先在 ---ANALYSIS--- 和 ---END_ANALYSIS--- 之间写一段自由分析,"
            "梳理头部的关键事实、决策、文件、待办(草稿,不会被保留)。\n"
            f"2. 然后在 ---SUMMARY--- 和 ---END_SUMMARY--- 之间输出最终摘要,"
            f"总长 ≤ {self._config.max_summary_tokens} tokens,"
            "使用以下六个固定段落(顺序固定,缺则写 \"(none)\"):\n"
            f"{section_lines}\n\n"
            "【待摘要的对话】\n"
            f"{head_text}\n"
            "=== 开始 ===\n"
            "---ANALYSIS---\n"
        )

    async def _call_summary_llm(self, head: list[Message]) -> str:
        """调用 LLM 生成摘要。stream() 异常时让上层 except 捕获。"""
        prompt = self._build_summary_prompt(head)
        out: list[str] = []
        async for delta in self._llm.stream(
            messages=[Message(role="user", content=prompt)],
            system="You are a context-compaction assistant. Never call tools.",
            tools=[],
            cache_breakpoints=None,
        ):
            if delta.type == "text" and isinstance(delta.text, str):
                out.append(delta.text)
        return "".join(out)

    @staticmethod
    def _parse_summary(text: str) -> str | None:
        """从 LLM 输出抽取 SUMMARY 段。

        - 找 `---SUMMARY---`,再找其后最近的 `---END_SUMMARY---`(或 EOF)
        - 没有 SUMMARY 段 → None
        - 有 SUMMARY 但缺 END_SUMMARY → 取到末尾
        - ANALYSIS 段一律丢弃
        """
        start_marker = "---SUMMARY---"
        end_marker = "---END_SUMMARY---"
        start = text.find(start_marker)
        if start < 0:
            return None
        body_start = start + len(start_marker)
        end = text.find(end_marker, body_start)
        if end < 0:
            body = text[body_start:]
        else:
            body = text[body_start:end]
        return body.strip()


def _estimate_one(msg: Message) -> int:
    """helper:单条消息 token 估算(估算器一层薄包装)。"""
    from baozicode.context.estimator import estimate_message_tokens
    return estimate_message_tokens(msg)