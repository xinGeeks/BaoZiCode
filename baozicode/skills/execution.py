"""v1.0 Skills — Skill 执行两种模式(shared / independent)。

公开 API 见 `__init__.py`。

`SkillExecutor.execute(name, args)` 是 Skill 执行的统一入口(loading + 执行):

- **shared 模式**:把 Skill body 钉到 system-reminder,Slash 触发时把 body 作为
  下一条 user 消息发给主 Agent,SOP 在主对话里顺序执行 → 结果留在主历史。
- **independent 模式**(v1.2):走 SubAgentManager 通道 —
  `dispatch(type="definition", role="<skill-name-as-agent>", prompt=..., async_=True)`
  然后 await task 完成。Skill body 替换进 sub-Agent 的 identity section,
  sub-Agent 跑完 → 摘要回流主对话。

v1.2 改动:`independent_runner` 参数被 `subagent_manager` 取代 —
不再需要外部注入独立运行器,SkillExecutor 直接走 SubAgentManager。
SkillDef 当作 AgentDef 用(role 名 = skill 名,body = skill body,
tools = SkillFrontmatter.allowed_tools 推导)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .activation import SkillActivation
from .loader import LoadSkillResult, SkillLoader
from .schema import ExecutionMode, SkillDef

_log = logging.getLogger(__name__)

__all__ = [
    "SkillExecutionResult",
    "SkillExecutor",
]


@dataclass(frozen=True)
class SkillExecutionResult:
    """Skill 执行统一返回。

    Attributes:
        ok: True = 加载 + 执行成功;False = 加载失败或独立运行抛错
        name: Skill 名(无论成败都填)
        mode: shared / independent
        summary: 给主 Agent 看的简短结果 ——
            shared 通常是「已激活,用 /<name> 执行」,independent 是 sub-Agent 摘要
        raw_output: 完整 sub-Agent 输出(独立模式才有值;shared 留 None)
    """

    ok: bool
    name: str
    mode: ExecutionMode
    summary: str
    raw_output: str | None = None


class SkillExecutor:
    """Skill 执行编排 — 统一加载 + 分派 shared/independent。

    Args:
        loader: SkillLoader 实例(用于加载 + activate)
        activation: SkillActivation 实例(由 SkillLoader 持有,这里再持一份便于诊断)
        subagent_manager: v1.2 — SubAgentManager 实例。独立模式走它派发 sub-Agent。
            `None` 时独立模式返回 ok=False。
    """

    def __init__(
        self,
        loader: SkillLoader,
        activation: SkillActivation,
        *,
        subagent_manager: Any | None = None,
    ) -> None:
        self._loader = loader
        self._activation = activation
        self._subagent_manager = subagent_manager

    @property
    def has_subagent_manager(self) -> bool:
        return self._subagent_manager is not None

    async def execute(
        self,
        name: str,
        args: dict[str, str] | None = None,
    ) -> SkillExecutionResult:
        """按 Skill 模式执行。

        流程:
        1. 查 registry;没找到 → 返回失败
        2. loader.load_skill(name, args) 激活 + 占位符替换;
           失败 → 把 LoadSkillResult.summary 当作 summary 透传
        3. 模式分发:
           - shared → 直接返回 ok=True(summary=激活消息)
           - independent → 没有 subagent_manager → 失败;
             有则 `dispatch(type="definition", role=name, async_=True)`
             → 等 task 完成 → 包成 SkillExecutionResult
        """
        sd = self._loader._registry.lookup(name)  # type: ignore[attr-defined]
        if sd is None:
            return SkillExecutionResult(
                ok=False,
                name=name,
                mode="shared",
                summary=f"未找到 Skill:{name}",
            )

        load_result: LoadSkillResult = self._loader.load_skill(name, args)
        if not load_result.ok:
            return SkillExecutionResult(
                ok=False,
                name=name,
                mode=sd.frontmatter.mode,
                summary=load_result.summary,
            )

        mode = sd.frontmatter.mode
        if mode == "shared":
            return SkillExecutionResult(
                ok=True,
                name=name,
                mode="shared",
                summary=load_result.summary,
                raw_output=None,
            )

        # independent — 走 SubAgentManager 派发
        if self._subagent_manager is None:
            return SkillExecutionResult(
                ok=False,
                name=name,
                mode="independent",
                summary=(
                    f"Skill {name!r} 需要独立模式执行,但 SubAgentManager 未注入;"
                    f"在 App bootstrap 时构造 SkillExecutor(loader, activation, "
                    f"subagent_manager=...) 装配"
                ),
            )

        # 把 Skill body 当成 sub-Agent 的 prompt(SOP 是 prompt 内容)
        # SkillFrontmatter.allowed_tools 推导到 AgentFrontmatter.tools
        # 注:这里直接用 Skill 的 name 作为 sub-Agent role name —
        # SubAgentManager 会去 registry 查;但 Skill 没注册到 AgentRegistry,
        # 所以走另一条路径:用 SkillLoader 直接构造一个 in-memory AgentDef。
        try:
            from baozicode.agents.schema import (
                AgentDef, AgentFrontmatter,
            )
            from baozicode.llm.base import Message

            # use the body from loader's already-substituted result via activation
            entry = self._activation.get(name)
            skill_body = entry.body if entry else ""
            # 构造一个 in-memory AgentDef(Skill → Agent 适配)
            skill_fm = sd.frontmatter
            agent_def = AgentDef(
                frontmatter=AgentFrontmatter(
                    name=name,
                    description=skill_fm.description,
                    tools=skill_fm.allowed_tools,
                    tools_deny=None,
                    model=skill_fm.model,
                    max_iterations=10,
                    permission_mode="permissive",
                    nesting_depth=0,
                    hidden=False,
                ),
                body=skill_body,
                source="builtin",
                path=None,  # type: ignore[arg-type]
            )
            # 把 in-memory agent 注册到 SubAgentManager 的 registry(覆盖同名)
            self._subagent_manager._runtime._registry._defs[name] = agent_def

            # 派发 async + 等完成(用 sync timeout 短一点,默认 120s)
            raw = self._subagent_manager.dispatch(
                type="definition",
                role=name,
                prompt=skill_body or f"按 Skill {name!r} 执行任务",
                async_=True,
            )
            # raw 是 task_id — 拿 task 对象并 await
            if not isinstance(raw, str):
                # 错误路径(已被 dispatch 包成 ToolResult)
                return SkillExecutionResult(
                    ok=False,
                    name=name,
                    mode="independent",
                    summary=f"Skill {name!r} 派发失败:{raw.content}",
                )
            task = self._subagent_manager.get_task(raw)
            if task is None:
                return SkillExecutionResult(
                    ok=False,
                    name=name,
                    mode="independent",
                    summary=f"Skill {name!r} 派发后 task 缺失",
                )
            # 等 task 跑到 terminal(主 Agent idle 时 task 完成会自动塞 user msg,
            # 这里 await 的是 task.state 而非主对话变化)
            await self._wait_task_done(task)
            # 收尾
            output = task.result or ""
            if task.state == "failed":
                return SkillExecutionResult(
                    ok=False,
                    name=name,
                    mode="independent",
                    summary=(
                        f"Skill {name!r} 独立模式运行失败:{task.error}"
                    ),
                )
            if task.state == "canceled":
                return SkillExecutionResult(
                    ok=False,
                    name=name,
                    mode="independent",
                    summary=f"Skill {name!r} 独立模式被取消",
                )
            return SkillExecutionResult(
                ok=True,
                name=name,
                mode="independent",
                summary=(
                    f"[{name} 子对话摘要]\n{output}"
                    if output
                    else f"[{name} 子对话无输出]"
                ),
                raw_output=output,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Skill %r independent run failed: %s: %s",
                name, type(exc).__name__, exc,
            )
            return SkillExecutionResult(
                ok=False,
                name=name,
                mode="independent",
                summary=(
                    f"Skill {name!r} 独立模式运行失败:"
                    f"{type(exc).__name__}: {exc}"
                ),
            )

    async def _wait_task_done(self, task: Any) -> None:
        """等 task 跑到 terminal 状态。"""
        import asyncio
        # 简单轮询(避免整段 await 卡住 cancel)
        for _ in range(60 * 60):  # 上限 60 分钟,1s 一次
            if task.state in ("done", "failed", "canceled", "timeout"):
                return
            await asyncio.sleep(1)
        # 超时兜底 — 设 cancel
        task.cancel_event.set()
