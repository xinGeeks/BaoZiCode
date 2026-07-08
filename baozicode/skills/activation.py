"""v1.0 Skills — 已激活 Skill 的运行时容器。

公开 API 见 `__init__.py`。

SkillActivation 维护当前 Agent 那一轮可见的「激活中」Skill 集合,
并负责:

1. `activate()` — 把 Skill 挂到 active 集合 + 在 v0.9 `CommandRegistry` 动态注册
   `/<name>` 斜杠(PROMPT 类型)
2. `deactivate()` / `clear()` — 从 active 集合移除 + 反注册斜杠
3. `render_active_section()` — 输出
   `<system-reminder type="active_skills" sticky="true">` block,
   供 v0.4 PromptBuilder 注入到 dynamic_messages 最显眼位置

设计要点:
- 同一 Skill 重复 `activate()` 同 body/mode 等参数 → 幂等返回
- 一旦 body / mode 等参数变化 → 先 deactivate 再重新 activate
- active 集合的名字 ⇄ `_index` 名字保持严格一致(deactivate 双向清理)
- 斜杠 handler 只返回 `PromptResult(text=body)` —— 占位符替换由 P5 loader 处理
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace as dataclasses_replace
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from baozicode.commands.registry import (
    CommandDef,
    CommandRegistry,
    CommandType,
    PromptResult,
)

from .schema import MAX_HISTORY_BUBBLES, ExecutionMode

if TYPE_CHECKING:
    from .schema import SkillDef

_log = logging.getLogger(__name__)

__all__ = ["ActiveSkill", "SkillActivation"]


@dataclass(frozen=True)
class ActiveSkill:
    """单条已激活 Skill 的运行期状态。

    Attributes:
        name: Skill 名(同命令名,匹配 `^[a-z][a-z0-9-]*$`)
        body: Skill 正文(Markdown SOP,可能含 `{var}` 占位符,运行时按需替换)
        mode: `shared` / `independent`
        allowed_tools: 工具白名单元组(internal 工具不受影响)
        history_bubbles: independent 模式下带多少历史进子对话
        model: 可选,指定模型覆盖
        hidden: 斜杠是否对用户隐藏
        description: 一句话说明(用于 `/help` 类查询)
        slash_registered: 是否成功在 v0.9 registry 注册了 `/<name>` 斜杠;
            False 时通常是 name 已被 v0.9 builtin 命令占用(如 `/review` /
            `/commit` / `/test`),Skill 仍激活,只是少了斜杠入口 ——
            用户仍可通过 `load_skill` tool 或 `/skill <name>` 加载它
    """

    name: str
    body: str
    mode: ExecutionMode
    allowed_tools: tuple[str, ...]
    history_bubbles: int
    model: str | None
    hidden: bool
    description: str
    slash_registered: bool = True
    # v1.0 SkillDef 引用 — 给独立模式斜杠触发器调用 (SkillDef, args) -> summary
    _sd: "SkillDef | None" = None


# 独立模式触发器签名:(SkillDef, args) -> 摘要文本
IndependentInvoke = Callable[["SkillDef", dict[str, str] | None], Awaitable[str]]


class SkillActivation:
    """已激活 Skill 集合 + 斜杠命令挂载点。

    注入路径:由 v0.5+ App 在 bootstrap 时构造,持有 v0.9 CommandRegistry 引用
    以动态注册 `/<name>` 斜杠(PROMPT 类型,handler 返回 Skill body 或独立模式摘要)。

    Args:
        command_registry: v0.9 CommandRegistry 实例
        independent_invoke: 可选,(SkillDef, args) -> summary;为 None 时独立
            模式斜杠退回 shared 行为(把 body 作为 user 消息发出去)
    """

    def __init__(
        self,
        command_registry: CommandRegistry,
        *,
        independent_invoke: IndependentInvoke | None = None,
    ) -> None:
        self._registry = command_registry
        self._active: dict[str, ActiveSkill] = {}
        # 独立模式触发器(由 v1.0 chat_screen bootstrap 时注入,接 SkillExecutor)
        self._independent_invoke = independent_invoke

    def activate(
        self,
        name: str,
        body: str,
        *,
        mode: ExecutionMode = "shared",
        allowed_tools: list[str] | None = None,
        history_bubbles: int = 0,
        model: str | None = None,
        hidden: bool = False,
        description: str = "",
        skill_def: "SkillDef | None" = None,
    ) -> None:
        """激活 Skill。

        行为:
        - 同 body / mode / allowed_tools / history_bubbles / model / hidden /
          description → 幂等返回
        - 任一参数变化 → 先 `deactivate(name)` 再以新参数注册

        Args:
            skill_def: 可选 SkillDef 引用 ——
                SkillLoader 传进来用于独立模式斜杠触发时调
                `independent_invoke(skill_def, args)`,其它场景可省。

        Raises:
            ValueError: 构造的 CommandDef 撞名时由 `register_dynamic` 抛
        """
        allowed = tuple(dict.fromkeys(allowed_tools or ()))
        clamped_history = max(0, min(history_bubbles, MAX_HISTORY_BUBBLES))

        if name in self._active:
            cur = self._active[name]
            if (
                cur.body == body
                and cur.mode == mode
                and cur.allowed_tools == allowed
                and cur.history_bubbles == clamped_history
                and cur.model == model
                and cur.hidden == hidden
                and cur.description == description
            ):
                return
            self.deactivate(name)

        entry = ActiveSkill(
            name=name,
            body=body,
            mode=mode,
            allowed_tools=allowed,
            history_bubbles=clamped_history,
            model=model,
            hidden=hidden,
            description=description,
            _sd=skill_def,
        )

        slash_ok = True
        try:
            self._registry.register_dynamic(self._build_command(entry))
        except ValueError:
            # 斜杠名已被 v0.9 builtin 命令占用(如 `/review`) → 跳过注册,
            # Skill 仍激活,用户可走 load_skill tool 或 /skill <name> 加载。
            slash_ok = False
            _log.info(
                "Skill %r activated, but /%s slash is occupied by a built-in "
                "command; use load_skill tool or /skill %s to invoke it.",
                name,
                name,
                name,
            )

        self._active[name] = dataclasses_replace(entry, slash_registered=slash_ok)

    def deactivate(self, name: str) -> bool:
        """移除 Skill。

        Returns:
            True = 移除成功;False = 该 name 当前未激活
        """
        if name not in self._active:
            return False
        self._registry.unregister(name)
        del self._active[name]
        return True

    def clear(self) -> None:
        """清空全部 active Skill + 反注册全部对应斜杠。"""
        for name in list(self._active.keys()):
            self._registry.unregister(name)
        self._active.clear()

    def is_active(self, name: str) -> bool:
        return name in self._active

    def active_names(self) -> list[str]:
        """返回已激活名列表(按 activate 顺序)。"""
        return list(self._active.keys())

    def get(self, name: str) -> ActiveSkill | None:
        return self._active.get(name)

    def render_active_section(self) -> str:
        """生成 `<system-reminder type="active_skills" sticky="true">` block。

        空集合 → 返回 `""`(调用方直接跳过 section 注入)。

        多个 Skill 按 activate 顺序堆叠,每个 Skill 渲染为:

            ### <name>
            <body>
        """
        if not self._active:
            return ""
        lines: list[str] = [
            '<system-reminder type="active_skills" sticky="true">',
            "",
            "## Active Skills(钉在此处,每轮重建,优先级高于普通 system prompt)",
            "",
        ]
        for entry in self._active.values():
            lines.append(f"### {entry.name}")
            lines.append(entry.body)
            lines.append("")
        lines.append("</system-reminder>")
        return "\n".join(lines)

    def _build_command(self, entry: ActiveSkill) -> CommandDef:
        """构造 CommandDef:

        - **shared** Skill:handler 返回 `PromptResult(text=entry.body)`
        - **independent** Skill:handler 调 `self._independent_invoke(entry._sd, args)` 拿
          摘要;若独立触发器未注入 → 退回 shared 行为(body 作为 user 消息)
        """
        body = entry.body

        async def _handler(*args, **kwargs) -> PromptResult:
            if self._independent_invoke is not None and entry.mode == "independent":
                # 简化:把 args 解析成 {var: str} 字典;真实 args 解析留 P8
                parsed_args: dict[str, str] = {}
                if args and isinstance(args[0], str):
                    parsed_args["0"] = args[0]
                try:
                    summary = await self._independent_invoke(entry._sd, parsed_args)
                except Exception as exc:  # noqa: BLE001
                    return PromptResult(
                        text=f"[Skill {entry.name} 独立模式失败] {exc}"
                    )
                return PromptResult(
                    text=f"[{entry.name} 摘要]\n{summary}"
                    if summary
                    else f"[{entry.name} 无输出]"
                )
            return PromptResult(text=body)

        return CommandDef(
            name=entry.name,
            description=entry.description or f"执行 Skill:{entry.name}",
            usage=f"/{entry.name}",
            type=CommandType.PROMPT,
            hidden=entry.hidden,
            handler=_handler,
        )
