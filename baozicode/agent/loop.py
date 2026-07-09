"""Agent 主循环 — v0.3 抽出,v0.4 与 PromptBuilder 集成。

D1 决策:async generator 推 AgentEvent
D2 决策:5 种停止条件共用 _terminate(reason) 出口
D3 决策:StreamCollector 双路,TurnSnapshot 是唯一可信源
D7 决策:Plan Mode 用 side_effect 过滤工具
D8 决策:用户取消用 asyncio.Event
v0.4 决策:Agent 通过 AppConfig 拿配置,PromptBuilder.build() 在 __init__ 一次性构造,
        每轮 _inject_reminders() 在 messages[-2] 处插入 <system-reminder> 块
v0.8 决策:Agent 通过 setter 注入 SessionArchiver(CoversationManager 透传 append)
        和 MemoryUpdater(COMPLETED/MAX_ITERATIONS_REACHED 时异步触发);
        _inject_reminders 新增 time_gap / memory_refreshed 两类 reminder。
"""

from __future__ import annotations

import asyncio
import fnmatch
from pathlib import Path
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

log = logging.getLogger(__name__)

from collections.abc import Awaitable, Callable

from baozicode.agent.collector import StreamCollector
from baozicode.agent.events import AgentEvent, ProgressPhase, StopReason, UsageStats
from baozicode.agent.guards import (
    GuardState,
    check_failed_loop,
    check_unknown_tool,
    record_denial_warn,
    should_inject_denial_reminder,
)
from baozicode.agent.scheduler import annotate_calls, schedule
from baozicode.config.schema import AppConfig
from baozicode.context import CompactionError, MaybeCompactContext, maybe_compact
from baozicode.llm.base import LLMClient, Message
from baozicode.permissions.engine import RuleEngine
from baozicode.permissions.types import MergedPermissions, PermissionMode
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

_DENIAL_REMINDER_BODY = (
    "提示:你刚才的工具调用被权限层拒绝了。"
    "如果它确实是必要的,请检查调用参数或换用其他工具;"
    "如果不确定,请向用户询问。"
)


def _read_memory_indices(
    config: AppConfig,
    *,
    project_root: Path | None = None,
) -> tuple[str | None, str | None]:
    """从两层 MemoryStore 读出 index 文本,喂给 PromptBuilder。

    - `project_root` 为 None 时退到 cwd(供测试用)
    - 失败 / disabled → (None, None),PromptBuilder 跳过 memory section
    """
    try:
        from baozicode.memory import bootstrap as memory_bootstrap
        from baozicode.memory.store import MemoryStore  # noqa: F401
    except ImportError:
        return None, None
    root = project_root if project_root is not None else Path.cwd()
    try:
        user_store, project_store = memory_bootstrap(root, config)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("baozicode.agent").warning(
            "memory bootstrap 失败: %s: %s", type(exc).__name__, exc,
        )
        return None, None
    try:
        user_idx = user_store.read_index().format_for_prompt() or None
        project_idx = project_store.read_index().format_for_prompt() or None
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("baozicode.agent").warning(
            "memory index 读取失败: %s: %s", type(exc).__name__, exc,
        )
        return None, None
    return user_idx, project_idx


# v0.8:新的 reminder 类型 — Agent 收到的 reminder 列表,按顺序注入到 messages[-2]
# key 是 reminder 类型;value 是 (body, ttl)。Phase 4 由外部(ResumeResult / MemoryUpdater)
# 通过 `enqueue_reminder` 注入。
# - "time_gap":ttl="once",resume 时根据首条 user 时间间隔决定插不插
# - "memory_refreshed":ttl="sticky",MemoryUpdater 成功更新后注入
# v1.1 新增 "hook_prompt" — Hook prompt action 通过 enqueue_reminder 注入
ReminderKind = Literal["time_gap", "memory_refreshed", "hook_prompt"]


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

    v1.1 改造点:
    - 新增 hook_dispatcher 参数(可为 None,v1.0 行为)
    - lifecycle event(session.start/end, turn.start/end, message.received/sent,
      tool.pre/post)在 run() / 每轮迭代 / add_* 触发点 fire
    - _v5_executor 改为 v1.1 pipeline:L1 → hook.pre → L2-L5 → execute → hook.post
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
        session_mode: PermissionMode | None = None,
        merged_permissions: MergedPermissions | None = None,
        permissions_engine: RuleEngine | None = None,
        compact_ctx: MaybeCompactContext | None = None,
        instructions_text: str = "",
        project_root: Path | None = None,
        skill_filter: "Any | None" = None,
        skill_activation: "Any | None" = None,
        skill_registry: "Any | None" = None,
        hook_dispatcher: "Any | None" = None,
    ) -> None:
        if config is None:
            raise ValueError("Agent requires AppConfig in v0.4+")
        self._llm = llm_client
        self._all_tools = tools
        self._conversation = conversation
        self._permissions = permissions  # v0.2 兼容字段
        self._config = config
        self._plan_mode = plan_mode
        self._max_iterations = max_iterations
        self._permission_callback = permission_callback
        # v0.5: session 级 mode override;None 时由 L4 在跑时从 merged.mode 兜底
        # 设计上:mode 切换不热更当前 Agent,只对下一个新建的 Agent 生效
        self._session_mode = session_mode
        # v0.5: 5 层防御合并状态;None 时退回 v0.2 旧路径(_matches_deny 等)
        self._merged = merged_permissions
        # v0.5:稳定的 RuleEngine 实例 — SESSION 按钮累积的 allow 规则
        # 存在 merged.session_rules,engine 通过 merged 间接看到,
        # 这里保存它以保证 add_session_rule 走的是同一个 merged。
        self._engine = permissions_engine
        # v0.7: 上下文压缩编排器 — None 时跳过整层压缩
        self._compact_ctx = compact_ctx
        self._compact_requested = False  # /compact 触发
        self._cancel_event = asyncio.Event()
        # v0.8: 三层 BaoZiCode.md 拼接结果(空 → 跳过注入)
        self._instructions_text = instructions_text
        # v1.0: SkillRegistry(供 PromptBuilder 渲染「可用 Skill 列表」section)
        # None 时走 v0.4 旧路径(扫 config.skills_dir)
        self._skill_registry = skill_registry
        # v0.8: 外部(ResumeResult / MemoryUpdater)通过 enqueue_reminder 推入
        # 的 time_gap / memory_refreshed 提醒,每轮 _inject_reminders 消费一次后清空。
        self._pending_reminders: list[Message] = []
        # v0.8: 异步记忆更新器 — None 时跳过;COMPLETED / MAX_ITERATIONS_REACHED
        # 时 Agent 拿消息快照 fire-and-forget 触发 updater.update(snapshot)
        self._memory_updater: Any = None
        # v1.0: Skill 白名单守卫(动态 L2)— None 时不限制
        self._skill_filter: Any = skill_filter
        # v1.0: Skill 激活状态(用于 _inject_reminders 注入 active_skills section)
        self._skill_activation: Any = skill_activation
        # v1.1: Hook 事件分发器(lazy import 写在内部方法,避免 module-load 污染)
        # —— None 时完全走 v1.0 行为(无 lifecycle event / 无 hook.pre / 无 hook.post)
        self._hook_dispatcher: Any = hook_dispatcher
        # v1.1: stable_system slot 的 hook prompt 覆盖(append 进 stable_system 末尾)
        self._hook_stable_overrides: list[str] = []  # loaded on session.start
        # v1.1.1: temp slot 的 hook prompt — 一次性,下轮 _inject_reminders 消费即清
        self._temp_reminders: list[str] = []
        # v0.8: 两层 memory index — bootstrap 后立即读出 index 文本,灌进
        # PromptBuilder 让 LLM 在每轮请求前就知道已有笔记,避免重复 add。
        memory_index_user, memory_index_project = _read_memory_indices(
            self._config, project_root=project_root,
        )
        # PromptBuilder 构造一次,后续 run 都复用
        self._prompt: BuiltPrompt = PromptBuilder().build(
            self._config,
            plan_mode=self._plan_mode,
            tools=self._all_tools,
            instructions_text=instructions_text,
            memory_index_user=memory_index_user,
            memory_index_project=memory_index_project,
            skill_registry=skill_registry,
        )

    # ---- public API ----

    def cancel(self) -> None:
        """请求取消当前 run。在安全点生效,不打断正在执行的 tool。"""
        self._cancel_event.set()

    def set_archiver(self, archiver: Any) -> None:
        """v0.8:late-binding 注入 SessionArchiver(Agent 构造完后再接也允许)。

        转发到 ConversationManager,archiver.append 内部已 swallow 异常。
        None 时退回纯内存行为(v0.7)。
        """
        self._conversation.set_archiver(archiver)

    def set_memory_updater(self, updater: Any) -> None:
        """v0.8:注入异步记忆更新器。

        updater.update(snapshot) 是 async callable,snapshot 是 list[Message] 副本。
        触发条件见 run() 的 finally 块 — 仅在自然停止(COMPLETED)或
        预算耗尽(MAX_ITERATIONS_REACHED)时触发;被取消 / 异常时跳过。
        """
        self._memory_updater = updater

    def enqueue_reminder(
        self,
        kind: ReminderKind,
        body: str,
        *,
        ttl: Literal["once", "sticky"] = "once",
    ) -> None:
        """v0.8:外部向下一轮 LLM 注入一条 reminder。

        Args:
            kind: reminder 类型 — "time_gap" 或 "memory_refreshed"
            body: 注入到 `<system-reminder>` 块内的文本
            ttl: "once" 消费一次后丢弃;"sticky" 在生命周期内每次迭代都重发
                 (memory_refreshed 默认 sticky;time_gap 默认 once)
        """
        msg = Message(
            role="user",
            content=(
                f'<system-reminder type="{kind}" ttl="{ttl}">\n'
                f"{body}\n"
                "</system-reminder>"
            ),
        )
        self._pending_reminders.append(msg)

    def request_compact(self) -> None:
        """v0.7:请求在下次迭代顶部执行手动压缩。TUI 在 /compact 触发。

        异步安全:可在 `agent.run()` 内部任意点调,Agent 主循环会在下个
        安全点(每轮迭代开始时)捕获这个 flag,跑 maybe_compact(trigger="manual")。
        """
        self._compact_requested = True

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

    def _inject_reminders(
        self,
        messages: list[Message],
        iteration: int,
        guard_state: GuardState | None = None,
    ) -> list[Message]:
        """在 messages[-2] 处插入所有 active reminders(<system-reminder> user-role 消息)。

        Args:
            messages: 当前对话历史(最后一条是 user)
            iteration: 当前 1-indexed 迭代轮次,plan_mode reminder 节奏控制用
            guard_state: v0.5 新增 — 用于判断 denial_rate_limit reminder 是否注入

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

        # 3) v0.5: denial_rate_limit reminder(任一工具达到 denial_warn_threshold)
        if guard_state is not None:
            threshold = agent_cfg.denial_warn_threshold
            for name, count in guard_state.deny_counts.items():
                if count >= threshold:
                    reminders.append(
                        Message(
                            role="user",
                            content=(
                                '<system-reminder type="denial_rate_limit" '
                                'ttl="sticky">\n'
                                f"工具 `{name}` 已被连续拒绝 {count} 次(阈值 {threshold})。"
                                f"{_DENIAL_REMINDER_BODY}\n"
                                "</system-reminder>"
                            ),
                        )
                    )
                    # 只提醒一次最高频的工具,避免 reminder 爆炸
                    break

        # 5) v1.0: active_skills section(SkillActivation 动态渲染)
        # —— 始终每轮插入(只要有激活 Skill),优先级高(先于 sticky reminder)
        if self._skill_activation is not None:
            section = self._skill_activation.render_active_section()
            if section:
                reminders.append(
                    Message(
                        role="user",
                        content=section,
                    )
                )

        # 4) v0.8: 外部入队的 reminder(time_gap / memory_refreshed)— ttl=once 的
        # 注入后从队列里丢弃,sticky 的保留(下一轮还会重发)。
        if self._pending_reminders:
            sticky: list[Message] = []
            for r in self._pending_reminders:
                reminders.append(r)
                # 解析 ttl:消息 content 第一行 `<system-reminder ... ttl="...">`
                if 'ttl="sticky"' not in r.content:
                    # once → 消费后丢弃(不加入 sticky 列表)
                    pass
                else:
                    sticky.append(r)
            self._pending_reminders = sticky

        # 5) v1.1.1: hook prompt slot=temp — 一次性消费,append 后立即清空
        if self._temp_reminders:
            for body in self._temp_reminders:
                reminders.append(
                    Message(
                        role="user",
                        content=(
                            '<system-reminder type="hook_prompt" ttl="once">\n'
                            f"{body}\n"
                            "</system-reminder>"
                        ),
                    )
                )
            self._temp_reminders = []

        if not reminders:
            return list(messages)

        # 4) splice 到 messages[-2](user 消息保持最后位置)
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
        """主循环:迭代 LLM → 工具 → 续流,直到触发停止条件。

        v0.7 增量:每轮迭代顶部先检查手动压缩请求,再跑自动压缩;
        Layer-2 失败用 `CompactionError` → `StopReason.COMPACTION_FAILED`。

        v1.1 增量:lifecycle 事件触发
        - session.start 在 run 入口 fire(try 之前)
        - session.end 在 finally fire(cancel / exception 也覆盖)
        - turn.start 在每次迭代顶部 fire
        - turn.end 在每次迭代尾部 fire(在 yield 完 done 之前)
        - message.received 在 conversation.add_user 之前 fire
        - message.sent 在 conversation.add_turn 之后 fire
        """
        self._cancel_event.clear()
        # v1.1:message.received(在 add_user 之前)
        self._fire_lifecycle_safe("message.received", user_message)
        self._conversation.add_user(user_message)
        guard_state = GuardState()
        session_usage = UsageStats()
        iteration = 0
        terminate_reason: StopReason | None = None

        # v1.1:session.start
        self._fire_lifecycle_safe("session.start", None)

        try:
            while iteration < self._max_iterations:
                iteration += 1
                # v1.1:turn.start
                self._fire_lifecycle_safe("turn.start", iteration)
                yield AgentEvent.progress(iteration, self._max_iterations, "streaming")

                # ---- v0.7:手动压缩优先(/compact 触发)----
                if self._compact_requested and self._compact_ctx is not None:
                    # v1.1:system.compaction 事件
                    self._fire_lifecycle_safe("system.compaction", "manual")
                    self._compact_requested = False
                    try:
                        new_msgs, result = await maybe_compact(
                            self._conversation.to_list(),
                            trigger="manual",
                            ctx=self._compact_ctx,
                        )
                    except CompactionError as exc:
                        yield AgentEvent.error(f"compaction failed: {exc}")
                        terminate_reason = StopReason.COMPACTION_FAILED
                        break
                    # 总是用新 messages(覆盖 Layer 1 offload 结果;幂等)
                    self._conversation.set_messages(new_msgs)
                    if result.triggered:
                        yield AgentEvent.text(
                            f"[已压缩:{result.tokens_before} → {result.tokens_after} tokens]\n"
                        )

                # ---- v0.7:自动压缩(预算逼近)----
                if self._compact_ctx is not None:
                    try:
                        new_msgs, result = await maybe_compact(
                            self._conversation.to_list(),
                            trigger="auto",
                            ctx=self._compact_ctx,
                        )
                    except CompactionError as exc:
                        yield AgentEvent.error(f"compaction failed: {exc}")
                        terminate_reason = StopReason.COMPACTION_FAILED
                        break
                    # 总是用新 messages(覆盖 Layer 1 offload 结果;幂等)
                    self._conversation.set_messages(new_msgs)
                    if result.triggered:
                        yield AgentEvent.text(
                            f"[已压缩:{result.tokens_before} → {result.tokens_after} tokens]\n"
                        )

                # ---- LLM stream ----
                # v0.4: messages 注入 reminders,system/tools 来自 self._prompt
                messages_for_llm = self._inject_reminders(
                    self._conversation.to_list(), iteration, guard_state
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
                    # v0.5:5 层防御 + L5 user 决策
                    if self._merged is not None:
                        return await self._v5_executor(call, guard_state)
                    # v0.2 兼容路径(无 merged_permissions)
                    return await self._v2_executor(call, guard_state)

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

                # v1.1:turn.end(在 yield done / 重新迭代前)
                self._fire_lifecycle_safe("turn.end", iteration)
                # v1.1:message.sent(Assistant turn 已 add_turn)
                if self._conversation is not None:
                    try:
                        self._fire_lifecycle_safe("message.sent", iteration)
                    except Exception:
                        pass

                # v0.5:deny 不再终止 Agent Loop,只通过 reminder 提示 LLM
                # 老的 check_deny_threshold() 已 no-op,这里不再 break
        except Exception as exc:  # noqa: BLE001
            # v1.1:system.error 兜底网底 —— 上述循环内多处 try/except 各自处理
            # 已知错误(LLM stream / compaction),但任何漏网的未处理异常都走这。
            # 失败策略与 hook 自身一致:仅 log + fire system.error,绝不阻断 finally。
            log.exception("Agent.run 未处理异常: %s", exc)
            self._fire_lifecycle_safe("system.error", exc)
            if terminate_reason is None:
                terminate_reason = StopReason.STREAM_ERROR
            yield AgentEvent.error(f"unhandled: {exc}")
        finally:
            # v1.1:system.cancel(若用户取消)
            if self._cancel_event.is_set():
                self._fire_lifecycle_safe("system.cancel", "user_interrupt")
            # v1.1:session.end(无论什么 stop 路径都 fire,system.error 之后也必跑)
            self._fire_lifecycle_safe("session.end", None)
            if terminate_reason is None:
                # 跑完循环但没有显式 reason → 兜底 MAX_ITERATIONS_REACHED
                terminate_reason = StopReason.MAX_ITERATIONS_REACHED
            # v0.8: 自然停止时异步触发记忆更新。
            # 复制消息快照(updater 在另一个 task 里跑,不能直接持引用)
            if self._memory_updater is not None and terminate_reason in (
                StopReason.COMPLETED,
                StopReason.MAX_ITERATIONS_REACHED,
            ):
                snapshot = list(self._conversation.to_list())
                asyncio.create_task(self._memory_updater.update(snapshot))
            yield AgentEvent.done(terminate_reason)

    # ---- helpers ----

    async def _v5_executor(
        self,
        call: ToolCall,
        guard_state: GuardState,
    ) -> ToolResult:
        """v1.1 executor:hook-aware pipeline。

        顺序:L1 → hook.pre → L2-L5 → execute → hook.post。
        - hook.pre 拒 → ToolResult(execution_status=block_hook_pre, denied_by=hook_pre)
        - L1 拒 → ToolResult(execution_status=block_l1, denied_by=l1_blacklist)
        - L2-L5 拒 → ToolResult(execution_status=block_permission, denied_by=l2_l5_permission)
        - execute 成功 → ToolResult(execution_status=executed_success, is_error=False)
        - execute 异常 → ToolResult(execution_status=executed_failed, is_error=True)
        hook.post 在 finally 块 fire,覆盖以上所有路径。

        v0.5 兼容:hook_dispatcher 为 None 时仍走原来的 L0 Skill 白名单 + L1-L5
        permissions.check()(不带 hook.pre / hook.post)。
        """
        # L0 (v1.0 Skill 动态白名单)— 早于 hook.pre / 五层防御
        if self._skill_filter is not None and not self._skill_filter.is_allowed(call):
            record_denial_warn(guard_state, call.name)
            return ToolResult(
                tool_call_id=call.id,
                content=(
                    f"工具 {call.name!r} 不在当前激活 Skill 的 allowed-tools 白名单"
                    f"(技能 'shared' / 'independent' 模式下收窄可见工具,internal 例外)"
                ),
                is_error=True,
            )

        if self._hook_dispatcher is None:
            # v1.0 路径(无 hooks)
            return await self._v5_legacy_executor(call, guard_state)

        # ============ v1.1 hook-aware pipeline ============
        from baozicode.permissions import blacklist_check, check_layers_2_through_5

        # Step 1: L1 hard blacklist(早于 hook.pre,不可被 hook.allow 绕过)
        try:
            l1 = blacklist_check(call)
        except Exception as exc:
            log.warning("hook pipeline: blacklist_check 异常,跳过 L1: %s", exc)
            l1 = None
        if l1 is not None and l1.decision == "deny":
            record_denial_warn(guard_state, call.name)
            result = ToolResult(
                tool_call_id=call.id,
                content=f"L1 硬黑名单拒绝: {l1.reason}",
                execution_status="block_l1",
                denied_by="l1_blacklist",
            )
            self._fire_hook_post_safe(call, result)
            return result

        # Step 2: hook.pre(同步,首个 deny 短路)
        hook_deny = self._fire_hook_pre_safe(call)
        if hook_deny.denied:
            record_denial_warn(guard_state, call.name)
            result = ToolResult(
                tool_call_id=call.id,
                content=f"hook.pre 拒绝 ({hook_deny.denied_hook_id}): {hook_deny.reason}",
                execution_status="block_hook_pre",
                denied_by="hook_pre",
                denied_hook_id=hook_deny.denied_hook_id,
            )
            self._fire_hook_post_safe(call, result)
            return result

        # Step 3: L2-L5(跳过 L1,L1 已独立跑过)
        try:
            decision = check_layers_2_through_5(call, self._merged)
        except Exception as exc:
            log.warning("hook pipeline: check_layers_2_through_5 异常: %s", exc)
            decision = None
        if decision is not None and decision.decision == "deny":
            record_denial_warn(guard_state, call.name)
            result = ToolResult(
                tool_call_id=call.id,
                content=f"权限拒绝 ({decision.layer}): {decision.reason}",
                execution_status="block_permission",
                denied_by="l2_l5_permission",
            )
            self._fire_hook_post_safe(call, result)
            return result

        # Step 4: execute(工具真正跑)+ try/except 把异常转 executed_failed
        try:
            raw_result = await execute_tool_call(call)
            result = ToolResult(
                tool_call_id=call.id,
                content=raw_result.content,
                execution_status="executed_success" if not raw_result.is_error else "executed_failed",
                offloaded_to=raw_result.offloaded_to,
                original_size=raw_result.original_size,
            )
        except Exception as exc:
            log.warning("tool execution failed: %s", exc)
            result = ToolResult(
                tool_call_id=call.id,
                content=f"tool execution error: {exc}",
                execution_status="executed_failed",
            )
        self._fire_hook_post_safe(call, result)
        return result

    async def _v5_legacy_executor(
        self,
        call: ToolCall,
        guard_state: GuardState,
    ) -> ToolResult:
        """v0.5/v1.0 兼容 executor:无 hooks 时走。"""
        from baozicode.permissions import check as perms_check

        decision = perms_check(call, self._merged)
        if decision.decision == "deny":
            record_denial_warn(guard_state, call.name)
            return ToolResult(
                tool_call_id=call.id,
                content=f"工具调用被 {decision.layer} 拒绝: {decision.reason}",
                is_error=True,
            )
        if decision.decision == "allow":
            return await execute_tool_call(call)
        return await self._handle_user_decision(call, guard_state)

    def _fire_hook_post_safe(self, call: ToolCall, result: ToolResult) -> None:
        """tool.post 在 finally 同步 fire;hook 异常容错(fail-open)。"""
        if self._hook_dispatcher is None:
            return
        try:
            self._hook_dispatcher.run("tool.post", result)
        except Exception as exc:
            log.warning("hook tool.post 异常: %s", exc)

    def _fire_hook_pre_safe(self, call: ToolCall) -> "Any":
        """tool.pre 同步 fire(其他生命周期事件也走这个口径)。"""
        if self._hook_dispatcher is None:
            from baozicode.hooks.dispatcher import HookResult
            return HookResult()
        try:
            return self._hook_dispatcher.run("tool.pre", call)
        except Exception as exc:
            log.warning("hook tool.pre 异常,继续: %s", exc)
            from baozicode.hooks.dispatcher import HookResult
            return HookResult()

    def _fire_lifecycle_safe(self, event: str, payload: Any) -> None:
        """统一 lifecycle 事件触发(fail-open:异常仅 log)。"""
        if self._hook_dispatcher is None:
            return
        try:
            self._hook_dispatcher.run(event, payload)
        except Exception as exc:
            log.warning("hook lifecycle event %s 异常: %s", event, exc)

    def set_dynamic_section(self, name: str, content: str) -> None:
        """v1.1 hook prompt slot=stable_system 调这个,append 进 stable_system 末尾。"""
        # 仅 hook_overrides 这一个 name 接受覆盖;其他忽略
        if name != "hook_overrides":
            return
        self._hook_stable_overrides.append(content)
        # 重建 stable_system 把 override 拼接进来
        if self._prompt:
            extras = "\n\n".join(self._hook_stable_overrides)
            new_stable = self._prompt.stable_system + (
                "\n\n## Hook Overrides\n" + extras if extras else ""
            )
            self._prompt = BuiltPrompt(
                stable_system=new_stable,
                dynamic_messages=self._prompt.dynamic_messages,
                augmented_tools=self._prompt.augmented_tools,
                cache_breakpoints=self._prompt.cache_breakpoints,
            )

    async def _handle_user_decision(
        self,
        call: ToolCall,
        guard_state: GuardState,
    ) -> ToolResult:
        """L5 user 决策:走 permission_callback,或 headless 模式按拒绝处理。"""
        if self._permission_callback is None:
            record_denial_warn(guard_state, call.name)
            return ToolResult(
                tool_call_id=call.id,
                content=(
                    f"Tool {call.name} requires user confirmation "
                    f"(no permission_callback, default deny)."
                ),
                is_error=True,
            )
        allowed = await self._permission_callback(call)
        if not allowed:
            record_denial_warn(guard_state, call.name)
            return ToolResult(
                tool_call_id=call.id,
                content=f"Tool {call.name} denied by user.",
                is_error=True,
            )
        return await execute_tool_call(call)

    async def _v2_executor(
        self,
        call: ToolCall,
        guard_state: GuardState,
    ) -> ToolResult:
        """v0.2 兼容 executor:无 merged_permissions 时走原 _matches_deny / _is_auto_allowed。"""
        if self._matches_deny(call):
            record_denial_warn(guard_state, call.name)
            return ToolResult(
                tool_call_id=call.id,
                content=(
                    f"Tool call denied by permissions.deny "
                    f"(matched pattern for {call.name})."
                ),
                is_error=True,
            )
        if not self._is_auto_allowed(call):
            return await self._handle_user_decision(call, guard_state)
        return await execute_tool_call(call)

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
