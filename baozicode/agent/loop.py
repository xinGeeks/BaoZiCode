"""Agent 主循环 — v0.3 抽出,v0.4 与 PromptBuilder 集成。

D1 决策:async generator 推 AgentEvent
D2 决策:5 种停止条件共用 _terminate(reason) 出口
D3 决策:StreamCollector 双路,TurnSnapshot 是唯一可信源
D7 决策:Plan Mode 用 side_effect 过滤工具
D8 决策:用户取消用 asyncio.Event
v0.4 决策:Agent 通过 AppConfig 拿配置,PromptBuilder.build() 在 __init__ 一次性构造,
        每轮 _inject_reminders() 在 messages[-2] 处插入 <system-reminder> 块
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import AsyncIterator
from typing import Any

from collections.abc import Awaitable, Callable

from baozicode.agent.collector import StreamCollector
from baozicode.agent.events import AgentEvent, ProgressPhase, StopReason, UsageStats
from baozicode.agent.guards import (
    GuardState,
    check_deny_threshold,
    check_failed_loop,
    check_unknown_tool,
    record_denial,
)
from baozicode.agent.scheduler import annotate_calls, schedule
from baozicode.config.schema import AppConfig
from baozicode.llm.base import LLMClient, Message
from baozicode.prompt import PromptBuilder, PlanModeReminder
from baozicode.prompt.types import BuiltPrompt
from baozicode.tools.base import ToolCall, ToolDefinition, ToolResult
from baozicode.tools.registry import execute_tool_call

PermissionCallback = Callable[[ToolCall], Awaitable[bool]]


_PLAN_MODE_REMINDER_BODY = (
    "当前处于 Plan Mode 阶段:你只能使用无副作用工具(Read/Grep/Glob/WebFetch),"
    "用以编写或修订实施计划。正在编写 / 修订计划 = 是,"
    "直接执行修改系统 / 写文件的工具调用 = 否。"
)


class Agent:
    """v0.3 引入的 Agent,v0.4 与 PromptBuilder 集成。

    run(user_message) 是 async generator — TUI 订阅事件流,不驱动循环。
    cancel() 设置 _cancel_event,主循环在安全点检查。
    plan_mode=True 时只暴露 side_effect=False 的工具(Read/Grep/Glob/WebFetch)。

    v0.4 改造点:
    - __init__ 的 system_prompt: str 参数改为 config: AppConfig
    - 构造时调一次 PromptBuilder.build() → self._prompt
    - llm.stream 的 system/tools 参数统一从 self._prompt 读
    - 每轮调 _inject_reminders 把 <system-reminder> 块塞到 messages[-2]
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[ToolDefinition],
        conversation: Any,
        permissions: Any,
        config: AppConfig,
        *,
        max_iterations: int = 20,
        plan_mode: bool = False,
        permission_callback: PermissionCallback | None = None,
    ) -> None:
        if config is None:
            raise ValueError("Agent requires AppConfig in v0.4+")
        self._llm = llm_client
        self._all_tools = tools
        self._conversation = conversation
        self._permissions = permissions
        self._config = config
        self._plan_mode = plan_mode
        self._max_iterations = max_iterations
        self._permission_callback = permission_callback
        self._cancel_event = asyncio.Event()
        # PromptBuilder 构造一次,后续 run 都复用
        self._prompt: BuiltPrompt = PromptBuilder().build(
            self._config, plan_mode=self._plan_mode, tools=self._all_tools
        )

    # ---- public API ----

    def cancel(self) -> None:
        """请求取消当前 run。在安全点生效,不打断正在执行的 tool。"""
        self._cancel_event.set()

    @property
    def available_tools(self) -> list[ToolDefinition]:
        """v0.4: 始终从 self._prompt.augmented_tools 读。

        plan_mode=True 的过滤在 PromptBuilder.build() 内完成(只保留 side_effect=False),
        所以这里直接返回即可。
        """
        return list(self._prompt.augmented_tools)

    @property
    def plan_mode(self) -> bool:
        return self._plan_mode

    @property
    def prompt(self) -> BuiltPrompt:
        """暴露给测试 / TUI 调试用的 BuiltPrompt 句柄。"""
        return self._prompt

    def _inject_reminders(self, messages: list[Message], iteration: int) -> list[Message]:
        """在 messages[-2] 处插入所有 active reminders(<system-reminder> user-role 消息)。

        Args:
            messages: 当前对话历史(最后一条是 user)
            iteration: 当前 1-indexed 迭代轮次,plan_mode reminder 节奏控制用

        Returns:
            新的 messages 列表(原列表不变,防止在迭代器消费时损坏)
        """
        agent_cfg = self._config.active_agent()
        if not agent_cfg.enable_system_reminders:
            return list(messages)

        # 1) env reminder 总是从 self._prompt.dynamic_messages 里复制出来
        reminders: list[Message] = list(self._prompt.dynamic_messages)

        # 2) plan_mode reminder 按节奏插入
        reminder_engine = PlanModeReminder(
            self._plan_mode, interval=agent_cfg.plan_reminder_interval
        )
        if reminder_engine.should_emit(iteration):
            reminders.append(
                Message(
                    role="user",
                    content=(
                        '<system-reminder type="plan_mode" ttl="static">\n'
                        f"{_PLAN_MODE_REMINDER_BODY}\n"
                        "</system-reminder>"
                    ),
                )
            )

        if not reminders:
            return list(messages)

        # 3) splice 到 messages[-2](user 消息保持最后位置)
        out = list(messages)
        if len(out) == 0:
            # 第一轮迭代前 conversation 还没塞 user,全部追加
            out.extend(reminders)
            return out
        insert_at = len(out) - 1
        for r in reminders:
            out.insert(insert_at, r)
            insert_at += 1
        return out

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        """主循环:迭代 LLM → 工具 → 续流,直到触发停止条件。"""
        self._cancel_event.clear()
        self._conversation.add_user(user_message)
        guard_state = GuardState()
        session_usage = UsageStats()
        iteration = 0
        terminate_reason: StopReason | None = None

        try:
            while iteration < self._max_iterations:
                iteration += 1
                yield AgentEvent.progress(iteration, self._max_iterations, "streaming")

                # ---- LLM stream ----
                # v0.4: messages 注入 reminders,system/tools 来自 self._prompt
                messages_for_llm = self._inject_reminders(
                    self._conversation.to_list(), iteration
                )
                collector = StreamCollector()
                this_turn_usage = UsageStats()
                try:
                    async for delta in self._llm.stream(
                        messages_for_llm,
                        system=self._prompt.stable_system,
                        tools=self._prompt.augmented_tools,
                        cache_breakpoints=self._prompt.cache_breakpoints,
                    ):
                        if delta.type == "text":
                            async for chunk in collector.absorb(delta):
                                if chunk:
                                    yield AgentEvent.text(chunk)
                        elif delta.type == "tool_use":
                            async for _ in collector.absorb(delta):
                                pass  # tool_use 不 emit text
                        elif delta.type == "usage":
                            this_turn_usage = delta.text  # ContentDelta.text = UsageStats
                except Exception as exc:  # noqa: BLE001
                    yield AgentEvent.error(f"LLM stream error: {exc}")
                    terminate_reason = StopReason.STREAM_ERROR
                    break

                session_usage = session_usage + this_turn_usage
                yield AgentEvent.usage(this_turn_usage, session_usage)

                # 取消点 1:流结束
                if self._cancel_event.is_set():
                    terminate_reason = StopReason.USER_CANCELLED
                    break

                # ---- 终止判断:COMPLETED ----
                turn = collector.snapshot()
                if not turn.tool_calls:
                    # 本轮没要工具,把 assistant 文本入库
                    self._conversation.add_turn(turn)
                    terminate_reason = StopReason.COMPLETED
                    break

                yield AgentEvent.progress(iteration, self._max_iterations, "checking")

                # ---- 三层 guards:unknown tool 检查(plan_mode 下 side_effect=True 工具按 unknown 处理)----
                available_names = {t.name for t in self.available_tools}
                valid_calls: list[ToolCall] = []
                for call in turn.tool_calls:
                    reason = check_unknown_tool(
                        call, guard_state, valid_names=available_names
                    )
                    if reason is not None:
                        terminate_reason = reason
                        break
                    valid_calls.append(call)
                if terminate_reason is not None:
                    # 即便触发 unknown 终止,也要把 assistant 消息入库(保留 LLM 决策)
                    self._conversation.add_turn(turn)
                    break

                # ---- 入库 assistant message(从 TurnSnapshot 重建) ----
                self._conversation.add_turn(turn)

                # ---- 工具调度 ----
                yield AgentEvent.progress(iteration, self._max_iterations, "tool_exec")

                # 在执行前 yield 每条 tool_call,TUI 可挂卡片/触发 Modal
                for call in valid_calls:
                    yield AgentEvent.tool_call(call)

                side_effect_map = {t.name: t.side_effect for t in self.available_tools}
                annotate_calls(valid_calls, side_effect_map)

                async def executor(call: ToolCall) -> ToolResult:
                    # 权限检查:deny → 立即拒绝;auto_allow → 直接执行;
                    # 其他 → 委托给 TUI 的 permission_callback(它会弹 Modal)。
                    if self._matches_deny(call):
                        record_denial(guard_state, call.name)
                        return ToolResult(
                            tool_call_id=call.id,
                            content=(
                                f"Tool call denied by permissions.deny "
                                f"(matched pattern for {call.name})."
                            ),
                            is_error=True,
                        )
                    if not self._is_auto_allowed(call):
                        if self._permission_callback is None:
                            # 没有回调时(v0.2 兼容/headless 模式)按拒绝处理
                            record_denial(guard_state, call.name)
                            return ToolResult(
                                tool_call_id=call.id,
                                content=(
                                    f"Tool {call.name} requires user "
                                    f"confirmation (no permission_callback)."
                                ),
                                is_error=True,
                            )
                        allowed = await self._permission_callback(call)
                        if not allowed:
                            record_denial(guard_state, call.name)
                            return ToolResult(
                                tool_call_id=call.id,
                                content=(
                                    f"Tool {call.name} denied by user."
                                ),
                                is_error=True,
                            )
                    return await execute_tool_call(call)

                # 调度并 yield 每个 tool_result 事件
                async for result in self._run_scheduler(valid_calls, executor, guard_state):
                    yield AgentEvent.tool_result(result)
                    self._conversation.add_tool_result(result)

                    if result.is_error:
                        # 失败工具循环检查
                        reason = check_failed_loop(
                            ToolCall(id=result.tool_call_id, name="", arguments={}),
                            guard_state,
                            result.content,
                        )
                        if reason is not None:
                            terminate_reason = reason
                            break

                    # 取消点 2:每个 tool_result 之后
                    if self._cancel_event.is_set():
                        terminate_reason = StopReason.USER_CANCELLED
                        break

                if terminate_reason is not None:
                    break

                # ---- 拒绝阈值检查(本轮结束累计) ----
                for call in valid_calls:
                    reason = check_deny_threshold(call, guard_state)
                    if reason is not None:
                        terminate_reason = reason
                        break
                if terminate_reason is not None:
                    break
        finally:
            if terminate_reason is None:
                # 跑完循环但没有显式 reason → 兜底 MAX_ITERATIONS_REACHED
                terminate_reason = StopReason.MAX_ITERATIONS_REACHED
            yield AgentEvent.done(terminate_reason)

    # ---- helpers ----

    async def _run_scheduler(
        self,
        calls: list[ToolCall],
        executor,
        guard_state: GuardState,
    ) -> AsyncIterator[ToolResult]:
        """薄包装 — 让 Agent 主循环能用 `for ... in self._run_scheduler(...)`。

        实际委托给 scheduler.schedule()。AsyncIterator 通过 yield from 等价实现。
        """
        async for result in schedule(calls, executor):
            yield result

    def _matches_deny(self, call: ToolCall) -> bool:
        for pattern in self._permissions.deny:
            if fnmatch.fnmatch(call.name, pattern):
                return True
            for v in call.arguments.values():
                if isinstance(v, str) and fnmatch.fnmatch(v, pattern):
                    return True
        return False

    def _is_auto_allowed(self, call: ToolCall) -> bool:
        return call.name in self._permissions.auto_allow


__all__ = ["Agent"]
