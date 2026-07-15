"""v1.4 Team Tools — 6 个 Lead 协作工具实现。

工具清单(role_visibility 全部 ['lead']):

- `team_dispatch` — 派活给 member,关联 task_id(可选),触发 wake
- `team_send_message` — 给 member 发任意文本(纯文本 / APPROVED:/REJECTED:)
- `team_cancel` — 取消当前任务或 terminate 后端(terminate=False 只取消当前任务,True 强杀)
- `team_merge` — 顺序合 team 内 member 分支到 target
- `team_task_create` — 创建 task,8 字符 hex id,自动 detect_cycles
- `team_task_query` — 按 status / assignee 查任务清单

设计要点:

- 所有 6 个工具 `role_visibility=['lead']`;非 Lead Agent 看不到
- executor 委托到 `Mailbox` + `Tasks` + `run_team_merge` —— 不自己写
  mailbox/tasks 逻辑,保证单一可信源
- executor 返回 `ToolResult(content=...)`,错误用 `ToolResult.error_result`
- 注册入口:`register_team_tools(tool_registry, teams_registry,
  project_root) -> list[ToolDefinition]` — 由 `BaoZiCodeApp.on_mount` 调用
- executor 内部不持有 state,纯函数 + 入参上下文
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any

from baozicode.tools.base import ToolDefinition, ToolResult
from baozicode.tools.registry import ToolRegistry

from .backend_manager import BackendManager
from .mailbox import Mailbox
from .merge import run_team_merge
from .registry import TeamsRegistry
from .schema import (
    Member,
    MemberNotFound,
    Message,
    Team,
    TeamNotFound,
)
from .tasks import Task, TaskCycleError, Tasks

log = logging.getLogger(__name__)

# Type alias for backend_manager kwarg(可注入,默认 None 跳过 spawn)
_BackendManagerT = BackendManager | None


# ---------------------------------------------------------------------------
# Team existence + member lookup helpers(shared by all executors)
# ---------------------------------------------------------------------------


def _resolve_member(
    teams_registry: TeamsRegistry,
    team_name: str,
    member_name: str,
) -> tuple[Team, Member]:
    """校验 team + member 都存在,返 (team, member);否则抛 ValueError。

    错误返回经 ToolResult.error_result 包装;这里抛 ValueError 让调用方
    一致处理。
    """
    store = teams_registry.get(team_name)
    if store is None:
        raise ValueError(f"team {team_name!r} 不存在")
    team = store.show()
    try:
        member = store.get_member(member_name)
    except MemberNotFound as e:
        raise ValueError(str(e)) from e
    return team, member


# ---------------------------------------------------------------------------
# team_dispatch
# ---------------------------------------------------------------------------


async def execute_team_dispatch(
    args: dict[str, Any],
    *,
    teams_registry: TeamsRegistry,
    project_root: Path,
    backend_manager: _BackendManagerT = None,
) -> ToolResult:
    """派一个 task 给 member:写 inbox + touch wake + 标 task in_progress +
    (可选)触发 spawn。

    Args:
        args: `{team, member, task_id?, body?}` —— team/member 必填;
            task_id 可选(若空则不发任务关联,只发 body 文本);
            body 可选(若 task_id 有则默认 "task=<task_id>"
        backend_manager: 可选 — 注入则末尾调 `spawn_if_offline`,
            派生 member pane/coroutine;为 None 时跳过(foundation 阶段)。
    """
    team_name = args.get("team")
    member_name = args.get("member")
    task_id = args.get("task_id")
    body = args.get("body", "")

    if not team_name or not isinstance(team_name, str):
        return ToolResult.error_result("", "team_dispatch: 缺 team 参数")
    if not member_name or not isinstance(member_name, str):
        return ToolResult.error_result("", "team_dispatch: 缺 member 参数")

    try:
        team, member = _resolve_member(teams_registry, team_name, member_name)
    except ValueError as e:
        return ToolResult.error_result("", str(e))

    member_dir = teams_registry.teams_dir / team.name / member.name

    # 构造消息
    if task_id:
        msg_body = f"task={task_id}: {body or '(no body)'}"
    else:
        msg_body = body or "(empty)"

    msg = Message(sender="lead", body=msg_body)
    try:
        Mailbox.append_message(member_dir, "inbox", msg)
        Mailbox.touch_wake(member_dir)
    except Exception as e:  # noqa: BLE001
        return ToolResult.error_result(
            "", f"team_dispatch: 写 inbox 失败: {type(e).__name__}: {e}"
        )

    # 标 task in_progress(若有 task_id)
    if task_id:
        try:
            Tasks.update_status(
                team_dir=teams_registry.teams_dir / team.name,
                task_id=task_id,
                new_status="in_progress",
                assignee=member.name,
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "team_dispatch: 更新 task %s 状态失败: %s",
                task_id,
                e,
            )

    # 触发 spawn(若 backend_manager 注入)
    backend_type_str = ""
    if backend_manager is not None:
        try:
            handle = await backend_manager.spawn_if_offline(team.name, member)
            backend_type_str = handle.backend_type
        except Exception as e:  # noqa: BLE001
            log.warning(
                "team_dispatch: spawn_if_offline %s/%s 失败: %s",
                team.name, member.name, e,
            )

    suffix = f" backend={backend_type_str}" if backend_type_str else ""
    return ToolResult.success(
        "",
        f"dispatched {member.name} task={task_id or '(no task)'}{suffix}",
    )


# ---------------------------------------------------------------------------
# team_send_message
# ---------------------------------------------------------------------------


async def execute_team_send_message(
    args: dict[str, Any],
    *,
    teams_registry: TeamsRegistry,
    project_root: Path,
) -> ToolResult:
    """给 member 发任意文本(不关联 task)。

    Args:
        args: `{team, member, body}` — 三者必填;body 可包含 APPROVED:
            /REJECTED: 前缀触发审批语义(由 ApprovalProtocol 解析)
    """
    team_name = args.get("team")
    member_name = args.get("member")
    body = args.get("body", "")

    if not team_name or not isinstance(team_name, str):
        return ToolResult.error_result("", "team_send_message: 缺 team 参数")
    if not member_name or not isinstance(member_name, str):
        return ToolResult.error_result("", "team_send_message: 缺 member 参数")
    if not body or not isinstance(body, str):
        return ToolResult.error_result("", "team_send_message: 缺 body 参数")

    try:
        team, member = _resolve_member(teams_registry, team_name, member_name)
    except ValueError as e:
        return ToolResult.error_result("", str(e))

    member_dir = teams_registry.teams_dir / team.name / member.name
    msg = Message(sender="lead", body=body)

    try:
        Mailbox.append_message(member_dir, "inbox", msg)
        Mailbox.touch_wake(member_dir)
    except Exception as e:  # noqa: BLE001
        return ToolResult.error_result(
            "", f"team_send_message: 写 inbox 失败: {type(e).__name__}: {e}"
        )

    return ToolResult.success("", f"sent message to {member.name}")


# ---------------------------------------------------------------------------
# team_cancel
# ---------------------------------------------------------------------------


async def execute_team_cancel(
    args: dict[str, Any],
    *,
    teams_registry: TeamsRegistry,
    project_root: Path,
    backend_manager: _BackendManagerT = None,
) -> ToolResult:
    """取消 member 的当前任务(terminate=False)或强杀后端(terminate=True)。

    Args:
        args: `{team, member, reason?, terminate=false}` — team/member
            必填;reason 可选;terminate 默认 False
        backend_manager: 可选 — 注入则 terminate=True 时走
            `backend_manager.kill(..., grace_seconds=5.0)` 优雅杀后端
            (优先 pane 句柄);为 None 时 fallback 走裸 `os.kill(pid, SIGTERM)`
            (v1-4-team-tools 阶段行为)。
    """
    team_name = args.get("team")
    member_name = args.get("member")
    reason = args.get("reason", "no reason given")
    terminate = bool(args.get("terminate", False))

    if not team_name or not isinstance(team_name, str):
        return ToolResult.error_result("", "team_cancel: 缺 team 参数")
    if not member_name or not isinstance(member_name, str):
        return ToolResult.error_result("", "team_cancel: 缺 member 参数")

    try:
        team, member = _resolve_member(teams_registry, team_name, member_name)
    except ValueError as e:
        return ToolResult.error_result("", str(e))

    member_dir = teams_registry.teams_dir / team.name / member.name

    # 1) 写 cancel 消息到 inbox
    cancel_msg = Message(sender="lead", body=f"CANCEL: {reason}")
    try:
        Mailbox.append_message(member_dir, "inbox", cancel_msg)
    except Exception as e:  # noqa: BLE001
        return ToolResult.error_result(
            "", f"team_cancel: 写 inbox 失败: {type(e).__name__}: {e}"
        )

    # 2) 若 member 当前有 task,标 canceled
    state = Mailbox.read_state(member_dir)
    if state.current_task:
        try:
            Tasks.update_status(
                team_dir=teams_registry.teams_dir / team.name,
                task_id=state.current_task,
                new_status="canceled",
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "team_cancel: 更新 task %s 状态失败: %s",
                state.current_task,
                e,
            )

    # 3) terminate=True 时改 state = offline + 后端 kill
    if terminate:
        new_state = state.__class__(
            status="offline",
            last_active_ts=state.last_active_ts,
            current_task=None,
            backend_pid=state.backend_pid,
        )
        Mailbox.write_state(member_dir, new_state)
        # 优先走 backend_manager(优雅 kill + state cleanup + pane_info 清理);
        # 没注入则 fallback 走裸 os.kill(state.backend_pid, SIGTERM)
        # —— v1-4-team-tools 阶段行为,foundation 兼容保留。
        if backend_manager is not None:
            try:
                await backend_manager.kill(
                    team.name, member.name,
                    reason=reason, grace_seconds=5.0,
                )
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "team_cancel: backend_manager.kill %s/%s 失败:%s",
                    team.name, member.name, e,
                )
        elif state.backend_pid is not None:
            import os
            import signal

            try:
                os.kill(state.backend_pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                log.info(
                    "team_cancel: backend pid %s 已退出",
                    state.backend_pid,
                )
    else:
        Mailbox.touch_wake(member_dir)

    action = "terminated" if terminate else "canceled"
    return ToolResult.success(
        "", f"{action} {member.name} reason={reason}"
    )


# ---------------------------------------------------------------------------
# team_merge
# ---------------------------------------------------------------------------


async def execute_team_merge(
    args: dict[str, Any],
    *,
    teams_registry: TeamsRegistry,
    project_root: Path,
) -> ToolResult:
    """合并 team 内 member 分支到 target。委托 `run_team_merge`。"""
    team_name = args.get("team")
    if not team_name or not isinstance(team_name, str):
        return ToolResult.error_result("", "team_merge: 缺 team 参数")

    target = args.get("target", "main")
    dry_run = bool(args.get("dry_run", False))

    store = teams_registry.get(team_name)
    if store is None:
        return ToolResult.error_result("", f"team_merge: team {team_name!r} 不存在")
    team = store.show()

    result = run_team_merge(project_root, team, target=target, dry_run=dry_run)
    return ToolResult.success(
        "", json.dumps(result, ensure_ascii=False, indent=2)
    )


# ---------------------------------------------------------------------------
# team_task_create
# ---------------------------------------------------------------------------


async def execute_team_task_create(
    args: dict[str, Any],
    *,
    teams_registry: TeamsRegistry,
    project_root: Path,
) -> ToolResult:
    """在共享 tasks.jsonl 创建新 task,自动 generate id + cycle detect。"""
    team_name = args.get("team")
    body = args.get("body")
    if not team_name or not isinstance(team_name, str):
        return ToolResult.error_result("", "team_task_create: 缺 team 参数")
    if not body or not isinstance(body, str):
        return ToolResult.error_result("", "team_task_create: 缺 body 参数")

    deps_raw = args.get("depends_on", [])
    if not isinstance(deps_raw, list):
        return ToolResult.error_result(
            "", "team_task_create: depends_on 必须是 list"
        )
    auto_ready = bool(args.get("auto_ready", True))

    store = teams_registry.get(team_name)
    if store is None:
        return ToolResult.error_result("", f"team_task_create: team {team_name!r} 不存在")
    team_dir = store.team_dir

    task_id = secrets.token_hex(4)
    task = Task(id=task_id, body=body, depends_on=tuple(deps_raw))

    try:
        Tasks.append(team_dir, task)
    except Exception as e:  # noqa: BLE001
        return ToolResult.error_result(
            "", f"team_task_create: append 失败: {type(e).__name__}: {e}"
        )

    # auto_ready 时若 deps 全 done,标 ready
    if auto_ready and tuple(deps_raw):
        ready = Tasks.find_ready(team_dir)
        if any(t.id == task_id for t in ready):
            Tasks.update_status(team_dir, task_id, "ready")

    final = Tasks.read_all(team_dir)
    final_status = next(
        (t.status for t in final if t.id == task_id),
        "pending",
    )
    return ToolResult.success(
        "", f"created task={task_id} status={final_status}"
    )


# ---------------------------------------------------------------------------
# team_task_query
# ---------------------------------------------------------------------------


async def execute_team_task_query(
    args: dict[str, Any],
    *,
    teams_registry: TeamsRegistry,
    project_root: Path,
) -> ToolResult:
    """查 team 共享任务清单,按 status / assignee / ready_graph 过滤。"""
    team_name = args.get("team")
    if not team_name or not isinstance(team_name, str):
        return ToolResult.error_result("", "team_task_query: 缺 team 参数")

    status_filter = args.get("status_filter") or []
    if not isinstance(status_filter, list):
        return ToolResult.error_result(
            "", "team_task_query: status_filter 必须是 list"
        )
    assignee_filter = args.get("assignee")  # str | None
    include_ready_graph = bool(args.get("include_ready_graph", False))

    store = teams_registry.get(team_name)
    if store is None:
        return ToolResult.error_result("", f"team_task_query: team {team_name!r} 不存在")
    team_dir = store.team_dir

    all_tasks = Tasks.read_all(team_dir)
    ready_set = {t.id for t in Tasks.find_ready(team_dir)}

    results: list[dict[str, Any]] = []
    for t in all_tasks:
        if status_filter and t.status not in status_filter:
            continue
        if assignee_filter and t.assignee != assignee_filter:
            continue
        item: dict[str, Any] = {
            "id": t.id,
            "body": t.body,
            "status": t.status,
            "assignee": t.assignee,
            "depends_on": list(t.depends_on),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "completed_at": (
                t.completed_at.isoformat() if t.completed_at else None
            ),
            "error": t.error,
        }
        if include_ready_graph:
            item["ready_for_dispatch"] = t.id in ready_set
        results.append(item)

    return ToolResult.success(
        "", json.dumps(results, ensure_ascii=False, indent=2)
    )


# ---------------------------------------------------------------------------
# ToolDefinition 注册
# ---------------------------------------------------------------------------


_TEAM_ROLE_VISIBILITY = ["lead"]


def _td(name: str, description: str, parameters: dict[str, Any]) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        parameters=parameters,
        side_effect=True,
        risk="low",
        role_visibility=list(_TEAM_ROLE_VISIBILITY),
    )


def _build_team_tool_defs() -> list[ToolDefinition]:
    common_props = {
        "team": {"type": "string", "description": "team 名"},
    }
    return [
        _td(
            "team_dispatch",
            "把 task 派给一个 team member。member 必须已 add_member(...);"
            "若 task_id 非空,自动在 tasks.jsonl 标 in_progress 并填 started_at。"
            "返回 ToolResult 含 member 接受的 task_id。",
            {
                "type": "object",
                "properties": {
                    **common_props,
                    "member": {"type": "string", "description": "member 名"},
                    "task_id": {
                        "type": ["string", "null"],
                        "description": "可选关联到 tasks.jsonl 里的某个 task id",
                    },
                    "body": {
                        "type": "string",
                        "description": "任务描述(plain text)",
                    },
                },
                "required": ["team", "member"],
            },
        ),
        _td(
            "team_send_message",
            "给已存在的 team member 发纯文本消息(非任务派活)。"
            "用法广泛:approve/reject plan、问进度、广播、注入上下文。"
            "body 含 APPROVED:/REJECTED: 前缀触发审批语义。",
            {
                "type": "object",
                "properties": {
                    **common_props,
                    "member": {"type": "string"},
                    "body": {"type": "string", "description": "消息正文"},
                },
                "required": ["team", "member", "body"],
            },
        ),
        _td(
            "team_cancel",
            "终止一个 member 的当前任务。"
            "terminate=False(默认):写 cancel 消息到 inbox + 当前 task → canceled。"
            "terminate=True:写 cancel 消息 + Mailbox.write_state(status='offline') + 后端 SIGTERM。",
            {
                "type": "object",
                "properties": {
                    **common_props,
                    "member": {"type": "string"},
                    "reason": {"type": "string", "description": "取消原因"},
                    "terminate": {
                        "type": "boolean",
                        "default": False,
                        "description": "true=强杀后端;false=只取消当前任务",
                    },
                },
                "required": ["team", "member"],
            },
        ),
        _td(
            "team_merge",
            "把 team 内所有 member 的 worktree 分支(默认 wt/<name>)顺序合并到 "
            "target 分支(默认 main)。冲突走 git merge --abort + 报错;"
            "已合并的保留。Lead 必走此工具,不用 Bash git merge。",
            {
                "type": "object",
                "properties": {
                    **common_props,
                    "target": {
                        "type": "string",
                        "default": "main",
                        "description": "目标分支名",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "true=只输出计划,不实际跑 git",
                    },
                },
                "required": ["team"],
            },
        ),
        _td(
            "team_task_create",
            "在共享 tasks.jsonl 创建新任务,自动 generate 8 字符 hex id,"
            "可指定 depends_on 表达 DAG 依赖。返回 task id + status。",
            {
                "type": "object",
                "properties": {
                    **common_props,
                    "body": {"type": "string", "description": "任务描述"},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                        "description": "本任务依赖的 task id 列表",
                    },
                    "auto_ready": {
                        "type": "boolean",
                        "default": True,
                        "description": "若 deps 全 satisfied,自动 mark ready",
                    },
                },
                "required": ["team", "body"],
            },
        ),
        _td(
            "team_task_query",
            "查 team 共享任务清单。支持按 status / assignee 过滤,"
            "可选 include_ready_graph 返回 ready_for_dispatch 布尔 + depends_on 展开。",
            {
                "type": "object",
                "properties": {
                    **common_props,
                    "status_filter": {
                        "type": "array",
                        "items": {
                            "enum": [
                                "pending",
                                "ready",
                                "in_progress",
                                "done",
                                "failed",
                                "canceled",
                            ]
                        },
                        "default": [],
                        "description": "空数组 = 所有状态",
                    },
                    "assignee": {
                        "type": ["string", "null"],
                        "default": None,
                        "description": "null=查所有",
                    },
                    "include_ready_graph": {
                        "type": "boolean",
                        "default": False,
                        "description": "true=额外返回 ready_for_dispatch + depends_on",
                    },
                },
                "required": ["team"],
            },
        ),
    ]


async def register_team_tools(
    tool_registry: ToolRegistry,
    teams_registry: TeamsRegistry,
    project_root: Path,
    *,
    backend_manager: _BackendManagerT = None,
) -> list[ToolDefinition]:
    """注册 6 个 team_* 工具到 tool_registry。

    Args:
        tool_registry: 全局 ToolRegistry
        teams_registry: 用来在 executor 内查 team / member
        project_root: project 根目录(传 run_team_merge 用)
        backend_manager: 可选 — 注入后:
          - `team_dispatch` 末尾调 `spawn_if_offline` 派生 member
          - `team_cancel(terminate=True)` 走 `backend_manager.kill()`
          None 时 executor 跳过 spawn/kill 步骤(foundation 兼容)。

    Returns:
        注册的 6 个 ToolDefinition 列表

    Raises:
        ValueError: 工具名与已有工具冲突时(已被注册过视为已注册)
    """
    tool_defs = _build_team_tool_defs()
    executors = {
        "team_dispatch": execute_team_dispatch,
        "team_send_message": execute_team_send_message,
        "team_cancel": execute_team_cancel,
        "team_merge": execute_team_merge,
        "team_task_create": execute_team_task_create,
        "team_task_query": execute_team_task_query,
    }

    for tool_def in tool_defs:
        async def _execute(
            args: dict[str, Any],
            *,
            _td=tool_def,
            _bm=backend_manager,
        ) -> ToolResult:
            return await executors[_td.name](
                args,
                teams_registry=teams_registry,
                project_root=project_root,
                backend_manager=_bm,
            )

        try:
            await tool_registry.register_tool(
                tool_def,
                _execute,
                source_label="team-tools",
            )
        except ValueError as e:
            # 已注册过视为 idempotent
            if "already registered" in str(e):
                log.debug("team tool %s already registered; skip", tool_def.name)
                continue
            raise

    return tool_defs


async def unregister_team_tools(tool_registry: ToolRegistry) -> None:
    """注销 6 个 team_* 工具(BaoZiCodeApp.on_unmount 用)。"""
    await tool_registry.unregister_mcp_tools(
        [
            "team_dispatch",
            "team_send_message",
            "team_cancel",
            "team_merge",
            "team_task_create",
            "team_task_query",
        ]
    )


__all__ = [
    "register_team_tools",
    "unregister_team_tools",
    "execute_team_dispatch",
    "execute_team_send_message",
    "execute_team_cancel",
    "execute_team_merge",
    "execute_team_task_create",
    "execute_team_task_query",
]