"""v1.0 Skills — Skill 执行两种模式(shared / independent)。

公开 API 见 `__init__.py`。

`SkillExecutor.execute(name, args)` 是 Skill 执行的统一入口(loading + 执行):

- **shared 模式**:把 Skill body 钉到 system-reminder,Slash 触发时把 body 作为
  下一条 user 消息发给主 Agent,SOP 在主对话里顺序执行 → 结果留在主历史。
- **independent 模式**:Skill body 钉到 system-reminder 后,snapshot 主对话最后
  N 个 bubble(由 SkillFrontmatter.history_bubbles 控制,默认 3),开一个 sub-Agent
  跑完 SOP,捕获最后一条 assistant text 作为摘要,作为下一条 user 消息流回主 Agent。

`independent_runner` 是可注入的 sub-Agent 执行器:生产环境由 chat_screen bootstrap
时构造(实际调 Agent.run),测试时用 stub。SkillExecutor 不直接构造 Agent ——
保持 execution 模块可独立单测,sub-Agent 编排由 chat_screen 层负责。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from .activation import SkillActivation
from .loader import LoadSkillResult, SkillLoader
from .schema import ExecutionMode, SkillDef

_log = logging.getLogger(__name__)

__all__ = [
    "IndependentRunner",
    "SkillExecutionResult",
    "SkillExecutor",
]


# 独立模式运行器签名:接收 (SkillDef, args dict) 返回最后一轮 assistant 摘要文本
IndependentRunner = Callable[[SkillDef, dict[str, str] | None], Awaitable[str]]


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
        independent_runner: 可选,独立模式运行器 ——
            `Callable[[SkillDef, dict], Awaitable[str]]`,返回 sub-Agent 最后一轮
            assistant 文本摘要。`None` 时独立模式返回 ok=False。
    """

    def __init__(
        self,
        loader: SkillLoader,
        activation: SkillActivation,
        *,
        independent_runner: IndependentRunner | None = None,
    ) -> None:
        self._loader = loader
        self._activation = activation
        self._independent_runner = independent_runner

    @property
    def has_independent_runner(self) -> bool:
        return self._independent_runner is not None

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
           - independent → 没有 independent_runner → 失败;
             有则 runner(sd, args) → catch 异常,包成 SkillExecutionResult
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

        # independent
        if self._independent_runner is None:
            return SkillExecutionResult(
                ok=False,
                name=name,
                mode="independent",
                summary=(
                    f"Skill {name!r} 需要独立模式执行,但 independent_runner 未注入;"
                    f"在 chat_screen bootstrap 时调 SkillExecutor(loader, activation, "
                    f"independent_runner=...) 装配"
                ),
            )

        try:
            output = await self._independent_runner(sd, args)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Skill %r independent run failed: %s: %s",
                name,
                type(exc).__name__,
                exc,
            )
            return SkillExecutionResult(
                ok=False,
                name=name,
                mode="independent",
                summary=(
                    f"Skill {name!r} 独立模式运行失败:{type(exc).__name__}: {exc}"
                ),
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
