"""v1.2 SubAgent Delegation — SubAgentManager(中央编排器)。

公开 API:
- `TaskInfo` — 单个 sub-Agent task 的状态容器
- `SubAgentManager.dispatch(...)` — 派发新任务(sync / async)
- `SubAgentManager.cancel_all()` — 主 Agent 取消时级联
- `SubAgentManager.list_tasks()` / `get_task(task_id)` — TUI 状态栏用
- `SubAgentManager.drain_pending_notifications()` — 主 Agent 下一轮消费通知
- `TASK_TOOL` — 暴露给主 Agent LLM 的 `task` ToolDefinition
- `task_executor(arguments) -> ToolResult` — 工具调用 wrapper

状态机(每个 TaskInfo):
    pending → running → {done | failed | canceled | timeout}
    ↑                              ↓
    └──── retention 过期后被清理 ────┘(lazy,下次 dispatch/list_tasks 时扫)

派发路径:
- async=True:asyncio.create_task 跑后台,返回 task_id
- async=False:await 阻塞到完成(或 timeout demote)
- fork + async=False:warning + 强制 async(子任务必须后台跑才能 cache 命中)
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal, TYPE_CHECKING

from baozicode.agents.filter import ToolFilterEmptyError
from baozicode.agents.runtime import SubAgentRuntime
from baozicode.agents.schema import AgentDef
from baozicode.llm.base import Message
from baozicode.tools.base import ToolDefinition, ToolResult

if TYPE_CHECKING:
    from baozicode.agent import Agent
    from baozicode.conversation.manager import ConversationManager

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TaskInfo
# ---------------------------------------------------------------------------


# 状态字面量(写 spec 用,跟 dataclass 字段一起用)
TaskState = Literal[
    "pending", "running", "done", "failed", "canceled", "timeout"
]


@dataclass
class TaskInfo:
    """单个 sub-Agent task 的状态容器。

    字段可变性:
    - `state` / `started_at` / `finished_at` / `result` / `error` /
      `usage` / `agent` / `notification_pending` 由 manager 在生命周期内修改
    - `task_id` / `type` / `role` / `prompt` 创建后不变
    """

    task_id: str
    type: Literal["definition", "fork"]
    role: str | None
    prompt: str
    state: TaskState = "pending"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None
    agent: "Agent | None" = None
    result: str | None = None
    error: str | None = None
    usage: Any = None  # UsageStats 实例(避免循环 import)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    notification_pending: bool = False
    # 增量累积 sub-Agent 的文本输出,TUI 卡片用来轮询刷新
    last_text: str = ""

    @property
    def role_label(self) -> str:
        """用于 TUI 卡片显示 / 通知文本的简短标识。"""
        if self.type == "fork":
            return "fork"
        return self.role or "definition"


# ---------------------------------------------------------------------------
# SubAgentManager
# ---------------------------------------------------------------------------


class MaxConcurrentReachedError(Exception):
    """dispatch 时已超出 max_concurrent 限制 — sync 路径抛,async 路径返回 error。"""


class SubAgentManager:
    """sub-Agent 任务中央编排器 — 由 `BaoZiCodeApp` 持有 1 个实例。

    Args:
        runtime: SubAgentRuntime 实例(由 App 注入,共享 llm/hooks/tool_registry)
        main_conversation: 主 Agent 的 ConversationManager(用于 idle 时塞 user msg)
        max_concurrent: 同时 running 上限(默认 5)
        task_retention_minutes: terminal 状态保留时间(默认 5 分钟)
        main_agent_ref: 主 Agent 实例的 late-binding 句柄(可选)
    """

    def __init__(
        self,
        *,
        runtime: SubAgentRuntime,
        main_conversation: "ConversationManager",
        max_concurrent: int = 5,
        task_retention_minutes: int = 5,
        main_agent_ref: Callable[[], "Agent | None"] | None = None,
    ) -> None:
        self._runtime = runtime
        self._main_conv = main_conversation
        self._max_concurrent = max_concurrent
        self._retention_seconds = task_retention_minutes * 60
        self._main_agent_ref = main_agent_ref
        self._tasks: dict[str, TaskInfo] = {}
        # 异步任务句柄(async=True 的 task_id → asyncio.Task),用于 cancel
        self._asyncio_tasks: dict[str, asyncio.Task[None]] = {}

    # ---- 查询 API ----

    def get_task(self, task_id: str) -> TaskInfo | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[TaskInfo]:
        self._cleanup_expired()
        return list(self._tasks.values())

    def count_by_state(self) -> dict[str, int]:
        """返回 `{state: count}`,供 status bar 用。"""
        self._cleanup_expired()
        result: dict[str, int] = {}
        for t in self._tasks.values():
            result[t.state] = result.get(t.state, 0) + 1
        return result

    def drain_pending_notifications(self) -> list[TaskInfo]:
        """主 Agent 下一轮顶部调 — 把 notification_pending=True 的 task 抽出,
        调 enqueue_reminder + 复位 flag。

        Returns:刚被排空的 task 列表(供测试断言)。
        """
        pending: list[TaskInfo] = []
        for t in self._tasks.values():
            if t.notification_pending and t.state in (
                "done", "failed", "canceled", "timeout",
            ):
                pending.append(t)
                t.notification_pending = False
        return pending

    # ---- 主 Agent 状态查询 ----

    def is_main_agent_running(self) -> bool:
        """主 Agent 是否在跑(用来决定结果走 user message 还是 reminder)。"""
        if self._main_agent_ref is None:
            return False
        agent = self._main_agent_ref()
        if agent is None:
            return False
        # 主 Agent 的 _is_running flag 由 Agent.run 在入口设、finally 清
        return bool(getattr(agent, "_is_running", False))

    # ---- 派发 API ----

    def dispatch(
        self,
        *,
        type: Literal["definition", "fork"],  # noqa: A002
        role: str | None,
        prompt: str,
        async_: bool = True,
        timeout_seconds: int | None = None,
        parent_conversation: "ConversationManager | None" = None,
        parent_denied_counts: dict[str, int] | None = None,
        parent_agent: "Agent | None" = None,
    ) -> str | ToolResult:
        """派发一个 sub-Agent task。

        Args:
            type: "definition" / "fork"
            role: definition 模式的 role 名;fork 模式 None
            prompt: 任务 prompt(已替换占位符)
            async_: True(默认)= 后台跑,返回 task_id;
                    False = 阻塞到完成(或 timeout demote)
            timeout_seconds: sync 模式专属,超时后 demote 到后台
            parent_conversation: fork 模式必填,snapshot 来源
            parent_denied_counts: fork 模式必填,初始 session_rules
            parent_agent: fork 模式必填,共享 _prompt

        Returns:
            - async=True → str(task_id)
            - async=False → str(完成摘要) 或 str("[sub-Agent 超时自动切后台...]")
            - 错误(未知 role / ToolFilter 空集) → ToolResult(is_error=True)
              注意:错误时**不**抛,直接返回 ToolResult,这样 task tool 走 LLM 路径
              时也能拿到错误反馈
        """
        self._cleanup_expired()

        # ---- fork 强制 async(D8)----
        if type == "fork" and not async_:
            log.warning(
                "fork 模式强制 async=true(sub-Agent 必须后台跑才能 cache 命中)"
            )
            async_ = True

        # ---- 参数校验 + 解析 role ----
        if type == "definition":
            role_def = self._runtime._registry.lookup(role or "")
            if role_def is None:
                return ToolResult.error_result(
                    tool_call_id="",
                    message=f"未知 Agent role: {role!r}",
                )
        else:
            role_def = None

        # ---- concurrent limit check ----
        running = sum(1 for t in self._tasks.values() if t.state == "running")
        if running >= self._max_concurrent:
            return ToolResult.error_result(
                tool_call_id="",
                message=(
                    f"已达并发上限 {self._max_concurrent};请等待现有 sub-Agent 完成"
                ),
            )

        # ---- 构造 TaskInfo ----
        task_id = self._new_task_id()
        task = TaskInfo(
            task_id=task_id,
            type=type,
            role=role,
            prompt=prompt,
            usage=self._empty_usage(),
        )

        # ---- spawn(状态隔离 + tool filter)----
        try:
            parent_messages = (
                list(parent_conversation.to_list())
                if parent_conversation is not None
                else None
            )
            sub_agent = self._runtime.spawn(
                task_id=task_id,
                type=type,
                role_def=role_def,
                prompt=prompt,
                parent_messages=parent_messages,
                parent_denied_counts=parent_denied_counts,
                parent_agent=parent_agent,
                is_background=async_,
            )
        except ToolFilterEmptyError as exc:
            return ToolResult.error_result(
                tool_call_id="",
                message=(
                    f"sub-Agent 工具过滤后空集,无法派发:"
                    f" {exc.layer_states}"
                ),
            )
        except ValueError as exc:
            return ToolResult.error_result(
                tool_call_id="",
                message=f"sub-Agent spawn 失败: {exc}",
            )

        task.agent = sub_agent
        self._tasks[task_id] = task

        if async_:
            # 后台跑
            asyncio_task = asyncio.create_task(self._run_subagent(task))
            self._asyncio_tasks[task_id] = asyncio_task
            return task_id

        # sync 阻塞路径
        return self._dispatch_sync_blocking(task, timeout_seconds)

    def _dispatch_sync_blocking(
        self,
        task: TaskInfo,
        timeout_seconds: int | None,
    ) -> str:
        """同步阻塞路径 — 跑 sub-Agent,等结果;超时则 demote 到后台。"""
        async def _runner() -> str:
            # 派一个真正跑 task 的协程,等结果
            runner_task = asyncio.create_task(self._run_subagent(task))
            self._asyncio_tasks[task.task_id] = runner_task
            if timeout_seconds is None:
                # 无限等
                await runner_task
                return task.result or "(无输出)"
            try:
                await asyncio.wait_for(runner_task, timeout=timeout_seconds)
                return task.result or "(无输出)"
            except asyncio.TimeoutError:
                # demote 到后台
                log.warning(
                    "sub-Agent %s 超过 timeout=%ds,demote 到后台",
                    task.task_id, timeout_seconds,
                )
                task.state = "timeout"
                task.notification_pending = True
                self._on_subagent_done(task)
                return (
                    f"[sub-Agent 超时自动切后台,task_id={task.task_id}]"
                )

        # 在新的 event loop 里跑(确保 sync 路径不阻塞主 loop)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 主 loop 在跑(常见) — 起一个临时 task 等
                return loop.create_task(_runner())  # type: ignore[return-value]
            return loop.run_until_complete(_runner())
        except RuntimeError:
            # 无 loop,自己开一个
            return asyncio.run(_runner())

    # ---- 取消 API ----

    def cancel_all(self) -> None:
        """主 Agent cancel 时调 — set 所有 running task 的 cancel_event。"""
        for t in self._tasks.values():
            if t.state == "running":
                t.cancel_event.set()

    def demote_to_background(self, task_id: str) -> bool:
        """手动 demote sync task 到后台(D8 第 3 种方式)。

        Sets cancel_event 让正在跑的 sub-Agent 提早结束当前 iter,
        再让 _on_subagent_done 走 timeout 分支发通知。

        Returns:True 如果成功 demote,False(task 不存在 / 已 terminal)。
        """
        t = self._tasks.get(task_id)
        if t is None or t.state not in ("running", "pending"):
            return False
        t.state = "timeout"  # 用 timeout 状态表示"被 demote"
        t.cancel_event.set()  # 让 sub-Agent 提早退出当前 iter
        return True

    def clear_tasks(self) -> None:
        """start_new_session 时调 — 关掉 running task 并清空所有状态。"""
        # 先 cancel 所有 running
        self.cancel_all()
        self._tasks.clear()
        self._asyncio_tasks.clear()

    # ---- 内部 ----

    def _run_subagent(self, task: TaskInfo) -> None:
        """跑一个 sub-Agent 的 async coroutine(后台 task 的入口)。"""
        task.state = "running"
        task.started_at = datetime.now(tz=timezone.utc)
        agent = task.agent
        if agent is None:
            task.state = "failed"
            task.error = "spawn 失败,agent is None"
            self._on_subagent_done(task)
            return

        # 接管 cancel_event(主 Agent cancel + 本 task 自己 cancel 都生效)
        original_cancel = agent._cancel_event  # type: ignore[attr-defined]
        # 当 task.cancel_event 被 set,转发到 agent._cancel_event
        forwarder = asyncio.create_task(
            self._forward_cancel(task.cancel_event, original_cancel)
        )

        last_text: str | None = None
        try:
            # 把 last assistant text 抽出(用于 result 摘要)
            # 用一个简易 collector 走 Agent.run 的事件流
            coro = agent.run(task.prompt)
            loop = asyncio.get_event_loop()
            agen = coro.__aiter__()
            while True:
                try:
                    event = loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
                if event.type == "text":
                    chunk = event.payload or ""
                    last_text = (last_text or "") + chunk
                    # 增量写回 last_text 给 TUI 卡片读
                    task.last_text = last_text
                elif event.type == "done":
                    reason = event.payload
                    # 合并 usage(从 session_usage 派生)
                    task.usage = getattr(
                        agent, "_last_session_usage", task.usage
                    )
                    if reason.value in ("user_cancelled",):
                        task.state = "canceled"
                        task.error = "子对话已被取消"
                    elif reason.value in (
                        "stream_error", "compaction_failed",
                    ):
                        task.state = "failed"
                        task.error = f"sub-Agent {reason.value}"
                    else:
                        task.state = "done"
                    break
                elif event.type == "error":
                    task.state = "failed"
                    task.error = str(event.payload)
                    break
            task.result = last_text or "(无文本输出)"
        except Exception as exc:  # noqa: BLE001
            log.exception("sub-Agent %s 异常: %s", task.task_id, exc)
            task.state = "failed"
            task.error = f"{type(exc).__name__}: {exc}"
        finally:
            forwarder.cancel()
            task.finished_at = datetime.now(tz=timezone.utc)
            self._on_subagent_done(task)

    async def _forward_cancel(
        self,
        src: asyncio.Event,
        dst: asyncio.Event,
    ) -> None:
        """把 src event 转发到 dst(主 Agent cancel + 本 task cancel 都生效)。"""
        while not src.is_set():
            try:
                # 短轮询,避免整段 await 卡住
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                return
        if not dst.is_set():
            dst.set()

    def _on_subagent_done(self, task: TaskInfo) -> None:
        """sub-Agent 跑完收尾 — 通知主 Agent(idle=user msg / busy=reminder)。"""
        task.notification_pending = True

        if self.is_main_agent_running():
            # busy 路径:塞 reminder 给主 Agent 下一轮顶部消费
            self._enqueue_subagent_reminder(task)
        else:
            # idle 路径:直接 add_user 到主 ConversationManager
            text = self._format_user_message(task)
            self._main_conv.add_user(text)

    def _enqueue_subagent_reminder(self, task: TaskInfo) -> None:
        """busy 路径 — enqueue_reminder("subagent_result", ...)。"""
        if self._main_agent_ref is None:
            return
        agent = self._main_agent_ref()
        if agent is None:
            return
        try:
            body = self._format_reminder(task)
            agent.enqueue_reminder(
                kind="subagent_result",  # type: ignore[arg-type]
                body=body,
                ttl="once",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "sub-Agent 结果 enqueue_reminder 失败: %s: %s",
                type(exc).__name__, exc,
            )

    def _format_user_message(self, task: TaskInfo) -> str:
        """idle 路径塞进主对话的 user 消息文本。"""
        if task.state == "failed":
            return (
                f"[{task.role_label} 子对话失败]\n{task.error}\n"
                f"(usage: in={task.usage.input_tokens},"
                f" out={task.usage.output_tokens})"
            )
        if task.state == "canceled":
            return f"[{task.role_label} 子对话已取消]"
        if task.state == "timeout":
            return (
                f"[{task.role_label} 子对话超时切后台]\n"
                f"task_id={task.task_id},完成时主 Agent 顶部会有通知"
            )
        # done
        summary = task.result or "(无输出)"
        return (
            f"[{task.role_label} 子对话结果]\n{summary}\n"
            f"(usage: in={task.usage.input_tokens},"
            f" out={task.usage.output_tokens})"
        )

    def _format_reminder(self, task: TaskInfo) -> str:
        """busy 路径 reminder body。"""
        prefix_map = {
            "failed": f"[{task.role_label} 子对话失败]",
            "canceled": f"[{task.role_label} 子对话已取消]",
            "timeout": f"[{task.role_label} 子对话超时切后台]",
            "done": f"[{task.role_label} 子对话结果]",
        }
        prefix = prefix_map.get(task.state, f"[{task.role_label} 子对话]")
        body = task.result or task.error or "(无输出)"
        return (
            f"{prefix}\n{body}\n"
            f"(usage: in={task.usage.input_tokens},"
            f" out={task.usage.output_tokens})"
        )

    def _cleanup_expired(self) -> None:
        """lazy 清理 — terminal 状态超过 retention 的 task 删掉。"""
        if not self._tasks:
            return
        now = datetime.now(tz=timezone.utc)
        expired: list[str] = []
        for tid, t in self._tasks.items():
            if (
                t.state in ("done", "failed", "canceled", "timeout")
                and t.finished_at is not None
                and (now - t.finished_at).total_seconds() > self._retention_seconds
            ):
                expired.append(tid)
        for tid in expired:
            self._tasks.pop(tid, None)
            self._asyncio_tasks.pop(tid, None)

    def _new_task_id(self) -> str:
        """生成 task_id — `task-YYYYMMDD-HHMMSS-xxxx`(跟 session_id 同格式)。"""
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        rand = secrets.token_hex(2)
        return f"task-{ts}-{rand}"

    def _empty_usage(self) -> Any:
        """构造一个零值 UsageStats(避免循环 import)。"""
        from baozicode.agent.events import UsageStats
        return UsageStats()


# ---------------------------------------------------------------------------
# task 工具定义 + executor
# ---------------------------------------------------------------------------


TASK_TOOL = ToolDefinition(
    name="task",
    description=(
        "派发一个独立子 Agent 执行子任务。"
        "两种模式:\n"
        "1. **definition** — 干净上下文 + 角色身份。Role 名来自预定义"
        "(`explorer` / `summarizer` 等)。子 Agent 工具集受 4 层过滤,"
        "无法调本工具自身(防嵌套)。\n"
        "2. **fork** — 继承主对话历史 + 共享系统 prompt,首次 LLM 请求"
        "命中 prompt cache,省 token。强制后台运行(async=true)。\n\n"
        "参数:\n"
        "- `type`: 'definition' 或 'fork'\n"
        "- `role`: definition 模式的角色名\n"
        "- `prompt`: 任务描述\n"
        "- `async`(默认 true):true=后台跑(返回 task_id),"
        "false=阻塞到完成(可设 timeout_seconds)\n"
        "- `timeout_seconds`:sync 模式专属超时,超时自动切后台\n\n"
        "返回:async 时返回 task_id,完成时主 Agent 顶部会有通知;"
        "sync 时返回 sub-Agent 最后输出文本。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["definition", "fork"],
                "description": "definition=干净上下文+角色, fork=继承主对话历史",
            },
            "role": {
                "type": "string",
                "description": "definition 模式的角色名(fork 模式不需要)",
            },
            "prompt": {
                "type": "string",
                "description": "任务描述",
            },
            "async": {
                "type": "boolean",
                "default": True,
                "description": "true=后台跑, false=阻塞到完成",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "description": "sync 模式专属超时(秒)",
            },
        },
        "required": ["type", "prompt"],
    },
    tool_type="internal",  # bypass Skill whitelist(跟 load_skill 一致)
)


async def task_executor(arguments: dict, *, manager_getter: Callable[[], "SubAgentManager | None"]) -> ToolResult:
    """task 工具的执行器 — 桥接 SubAgentManager.dispatch。

    `manager_getter` 是 late-binding — App.on_mount 时注册 tool 时还没构造
    SubAgentManager,用 getter 在调用时动态拿。
    """
    manager = manager_getter()
    if manager is None:
        return ToolResult.error_result(
            tool_call_id="",
            message="SubAgentManager 未初始化(task 工具不可用)",
        )
    type_str = arguments.get("type")
    if type_str not in ("definition", "fork"):
        return ToolResult.error_result(
            tool_call_id="",
            message=f"task.type 必须是 'definition' 或 'fork',收到 {type_str!r}",
        )
    prompt = arguments.get("prompt") or ""
    if not prompt:
        return ToolResult.error_result(
            tool_call_id="",
            message="task.prompt 必填",
        )

    # 主 Agent 句柄 late-binding — 用 manager 自己的 _main_agent_ref
    parent_agent = (
        manager._main_agent_ref() if manager._main_agent_ref else None
    )

    return await _do_dispatch(
        manager=manager,
        type=type_str,  # type: ignore[arg-type]
        role=arguments.get("role"),
        prompt=prompt,
        async_=arguments.get("async", True),
        timeout_seconds=arguments.get("timeout_seconds"),
        parent_agent=parent_agent,
    )


async def _do_dispatch(
    *,
    manager: SubAgentManager,
    type: Literal["definition", "fork"],  # noqa: A002
    role: str | None,
    prompt: str,
    async_: bool,
    timeout_seconds: int | None,
    parent_agent: "Agent | None",
) -> ToolResult:
    """实际调 manager.dispatch,把 ToolResult 错误路径包成 ToolResult。

    dispatch 返回 3 类值:
    - task_id (str):async 成功
    - summary str:sync 成功
    - ToolResult:错误 / 超时 demote(已经是 ToolResult 直接返回)
    """
    raw = manager.dispatch(
        type=type,
        role=role,
        prompt=prompt,
        async_=async_,
        timeout_seconds=timeout_seconds,
        parent_conversation=manager._main_conv if type == "fork" else None,
        parent_denied_counts=None,
        parent_agent=parent_agent if type == "fork" else None,
    )
    if isinstance(raw, ToolResult):
        return raw  # 错误已包装
    # success path: raw is task_id (str)
    if async_:
        return ToolResult.success(
            tool_call_id="",
            content=(
                f"[sub-Agent 已派发,task_id={raw},"
                f" 完成时主 Agent 顶部会有通知]"
            ),
        )
    # sync path: raw is summary str
    return ToolResult.success(tool_call_id="", content=str(raw))


__all__ = [
    "MaxConcurrentReachedError",
    "SubAgentManager",
    "TASK_TOOL",
    "TaskInfo",
    "TaskState",
    "task_executor",
]