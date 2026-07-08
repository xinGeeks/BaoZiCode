"""v1.0 Skills — `load_skill` 双入口(tool + `/skill` slash)+ 占位符替换。

公开 API 见 `__init__.py`。

`SkillLoader.load_skill(name, args)` 是 Skill 加载的**唯一**主入口:
- LLM 调 `load_skill` tool 走这里
- 用户输 `/skill <name> [args]` slash 也走这里

流程:
1. `registry.lookup(name)` — 找不到 → 返回失败结果(让上层决定 show_error)
2. `substitute_placeholders(body, args)` — 替换 `{var}` / `{var:default}`
3. `activation.activate(name, body, mode=..., allowed_tools=..., ...)` —
   注册斜杠(`/skill <name>` 撞名时软失败)+ 钉到 active set
4. 返回 `LoadSkillResult`

`LOAD_SKILL_TOOL` 是给 LLM 看的工具描述;`execute()` 是给 ToolRegistry 的
executor。ToolDefinition 上有 `tool_type="internal"` 标记,P6 的白名单防御
会豁免它(load_skill 在 Skill 未激活时也必须可用)。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from baozicode.tools.base import ToolDefinition, ToolResult
from baozicode.tools.registry import ToolRegistry as ToolsReg

from .activation import SkillActivation
from .registry import SkillRegistry
from .schema import SkillDef
from .whitelist import validate_declared_tools

if TYPE_CHECKING:
    from .execution import SkillExecutor

_log = logging.getLogger(__name__)

# 占位符语法:`{var}` 或 `{var:default}` —— var 名 `[a-z_][a-z0-9_]*`
_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)(?::([^}]*))?\}")


@dataclass(frozen=True)
class LoadSkillResult:
    """`load_skill` 的统一返回(共享模式 + 配置无 executor 时)。

    Attributes:
        ok: True = 加载并激活成功;False = 找不到 Skill 或参数错
        name: 请求的 Skill 名(无论成败都填)
        summary: 简短提示(成功:已激活;失败:错误描述)
    """

    ok: bool
    name: str
    summary: str


def substitute_placeholders(
    body: str, args: dict[str, str] | None
) -> str:
    """替换 body 里的 `{var}` / `{var:default}` 占位符。

    规则:
    - `{var}`:
      - `args[var]` 存在 → 替换为 `str(args[var])`
      - `args` 为 None / 缺该 var → 保留字面 `{var}`(让 LLM 看到缺值,自主决定)
    - `{var:default}`:
      - `args[var]` 存在 → 替换为 `str(args[var])`
      - 缺该 var → 替换为 `default`
    - `args=None` / `args={}` 当空 dict 处理
    - 占位符出现在 code span(`` `...` ``)内时仍替换(便于 Skill 用 `{var}` 提示)
    """
    values = args or {}

    def repl(m: re.Match[str]) -> str:
        var = m.group(1)
        default = m.group(2)
        if var in values:
            return str(values[var])
        if default is not None:
            return default
        return m.group(0)

    return _PLACEHOLDER_RE.sub(repl, body)


class SkillLoader:
    """Skill 加载主入口,持有 registry + activation 引用。"""

    def __init__(
        self,
        registry: SkillRegistry,
        activation: SkillActivation,
        *,
        tool_registry: ToolsReg | None = None,
        executor: "SkillExecutor | None" = None,
    ) -> None:
        self._registry = registry
        self._activation = activation
        # L1 静态防御需要 tool_registry(校验声明的工具都存在);
        # 为 None 时降级到不校验(测试 / 部分 boot 流程)。
        self._tool_registry = tool_registry
        # v1.0 Skill 执行器(独立模式必需);为 None 时 dispatch 退到 load_skill
        self._executor = executor

    def load_skill(
        self,
        name: str,
        args: dict[str, str] | None = None,
    ) -> LoadSkillResult:
        """按 name 加载 Skill,完成 placeholder 替换后 activate。

        Args:
            name: Skill 主名(已在 registry 内)
            args: 占位符替换字典(`{var: value}`)

        Returns:
            `LoadSkillResult(ok=True)` 成功;失败时 `ok=False`,summary 含原因。
        """
        if not name:
            return LoadSkillResult(
                ok=False,
                name=name,
                summary="load_skill: 缺少 Skill 名称",
            )

        sd = self._registry.lookup(name)
        if sd is None:
            return LoadSkillResult(
                ok=False,
                name=name,
                summary=f"未找到 Skill:{name}(可用 /skill list 或 "
                f"扫描 builtin/user/project 三级目录)",
            )

        # L1 静态防御:声明的 allowed-tools 必须存在于 ToolRegistry。
        if self._tool_registry is not None and sd.frontmatter.allowed_tools:
            try:
                validate_declared_tools(
                    list(sd.frontmatter.allowed_tools),
                    self._tool_registry,
                    skill_name=sd.name,
                )
            except ValueError as exc:
                return LoadSkillResult(ok=False, name=name, summary=str(exc))

        body = substitute_placeholders(sd.body, args)
        tools = sd.frontmatter.allowed_tools or []
        self._activation.activate(
            name=name,
            body=body,
            mode=sd.frontmatter.mode,
            allowed_tools=list(tools),
            history_bubbles=sd.frontmatter.history_bubbles,
            model=sd.frontmatter.model,
            hidden=sd.frontmatter.hidden,
            description=sd.frontmatter.description,
            skill_def=sd,
        )
        info = self._activation.get(name)
        slash_note = (
            ""
            if info is None or info.slash_registered
            else " (斜杠 /{} 被 v0.9 builtin 占用,改走 load_skill tool 或 "
            "/skill {})".format(name, name)
        )
        return LoadSkillResult(
            ok=True,
            name=name,
            summary=f"已激活 Skill:{name}{slash_note}",
        )

    def get_tool_definition(self) -> ToolDefinition:
        """返回 load_skill 的 ToolDefinition。"""
        return LOAD_SKILL_TOOL

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """ToolRegistry 调用的 executor(供 tool 路由)。

        流程:
        1. 解析 name / args
        2. 有 self._executor → 调 SkillExecutor.execute(支持独立模式)
        3. 否则退到 self.load_skill()(只激活,共享模式有效,独立模式仍 ok 但
           摘要需要后续 Skill slash / 手动触发)

        `arguments` 期望字段:
        - `name: str` — Skill 主名(必填)
        - `args: dict[str, str]` — 占位符替换(可选)
        """
        name = arguments.get("name")
        if not isinstance(name, str) or not name:
            return ToolResult.error_result(
                "",
                "load_skill: missing or invalid 'name' argument (str required)",
            )
        raw_args = arguments.get("args") or {}
        if not isinstance(raw_args, dict):
            return ToolResult.error_result(
                "",
                f"load_skill: 'args' must be a dict (got {type(raw_args).__name__})",
            )
        str_args: dict[str, str] = {}
        for k, v in raw_args.items():
            if not isinstance(k, str) or not k:
                continue
            if v is None:
                continue
            str_args[k] = str(v)

        if self._executor is not None:
            exec_result = await self._executor.execute(name, str_args)
            if exec_result.ok:
                return ToolResult.success("", exec_result.summary)
            return ToolResult.error_result("", exec_result.summary)

        # 没有 executor → 退化为只激活(共享模式 OK;独立模式仅激活)
        result = self.load_skill(name, args=str_args)
        if result.ok:
            return ToolResult.success("", result.summary)
        return ToolResult.error_result("", result.summary)


# ---- load_skill tool 描述 ----


LOAD_SKILL_TOOL = ToolDefinition(
    name="load_skill",
    description=(
        "按名加载一个 Skill,把完整 SOP 钉到 system reminder,同时收窄工具白名单。"
        "两步走:(1) 调 `Bash` 跑 `ls <builtin_dir> && ls ~/.config/baozicode/skills/ "
        "&& ls .baozicode/skills/` 看可用 Skill 名字 + 一句话说明(proj 优先)。"
        "(2) 调 `load_skill(name=<skill>, args={<var>: <value>})` 激活。"
        "args 把 body 里的 `{var}` 占位符替换为字符串值。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill 主名(^[a-z][a-z0-9-]*$)",
            },
            "args": {
                "type": "object",
                "description": "占位符 {var} → 值替换,值为字符串",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["name"],
    },
    risk="low",
    side_effect=False,
    path_args=[],
    tool_type="internal",  # 系统级 — 不受 Skill 白名单约束
)


__all__ = [
    "LOAD_SKILL_TOOL",
    "LoadSkillResult",
    "SkillLoader",
    "substitute_placeholders",
]
