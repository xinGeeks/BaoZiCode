"""PromptBuilder — 把 11 个 sections 拼成一个 BuiltPrompt。"""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path

from baozicode.llm.base import Message
from baozicode.prompt.rules import RuleRegistry
from baozicode.prompt.sections import (
    action_exec,
    constraints,
    custom,
    env_info,
    identity,
    memory,
    skills,
    task_mode,
    text_output,
    tone_style,
    tool_usage,
)
from baozicode.prompt.types import BuiltPrompt, BuildContext, CacheBreakpoint
from baozicode.tools.base import ToolDefinition


_FIXED_SECTIONS = [
    identity,
    constraints,
    task_mode,
    action_exec,
    tool_usage,
    tone_style,
    text_output,
]

_OPTIONAL_SECTIONS = [
    custom,
    skills,
    memory,
]


def _detect_git_info(cwd: str) -> tuple[str, str]:
    """返回 (branch, commit)。cwd 不在 git repo 时返回 ("", "")。"""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return branch, commit
    except Exception:
        return "", ""


def _build_env_message(ctx: BuildContext) -> Message:
    """把 env_info 段包装成 <system-reminder type=env> user-role 消息。"""
    content = env_info.render(ctx)
    body = f'<system-reminder type="env" ttl="static">\n{content}\n</system-reminder>'
    return Message(role="user", content=body)


class PromptBuilder:
    """构造 BuiltPrompt。Agent 启动时调用一次。"""

    def __init__(self, rule_registry: RuleRegistry | None = None) -> None:
        self._rules = rule_registry or RuleRegistry()

    @property
    def rule_registry(self) -> RuleRegistry:
        return self._rules

    def _make_context(
        self,
        config: object,
        plan_mode: bool,
        cwd: str | None = None,
        instructions_text: str = "",
        memory_index_user: str | None = None,
        memory_index_project: str | None = None,
        skill_registry: object | None = None,
    ) -> BuildContext:
        cwd = cwd or os.getcwd()
        branch, commit = _detect_git_info(cwd)
        return BuildContext(
            config=config,  # type: ignore[arg-type]
            rule_registry=self._rules,
            plan_mode=plan_mode,
            cwd=cwd,
            os_name=platform.system(),
            python_version=platform.python_version(),
            git_branch=branch,
            git_commit=commit,
            project_name=Path(cwd).name,
            now_iso=datetime.now().strftime("%Y-%m-%d %H:%M:%S (%a)"),
            instructions_text=instructions_text,
            memory_index_user=memory_index_user,
            memory_index_project=memory_index_project,
            skill_registry=skill_registry,
        )

    def _filter_registry(self, config: object) -> RuleRegistry:
        """根据 config.active_agent().rules 返回只含 enabled rule 的 RuleRegistry。

        spec: rule 字段为 False 时,该 rule 既不出现在 system prompt section,
        也不注入任何 tool description 前缀。所以两层(filtered)统一从源头过滤。
        """
        try:
            rules_cfg = config.active_agent().rules  # type: ignore[attr-defined]
        except Exception:
            return self._rules
        filtered = tuple(r for r in self._rules.rules if getattr(rules_cfg, r.id, True))
        return RuleRegistry(filtered)

    def build(
        self,
        config: object,
        plan_mode: bool,
        tools: list[ToolDefinition],
        cwd: str | None = None,
        instructions_text: str = "",
        memory_index_user: str | None = None,
        memory_index_project: str | None = None,
        skill_registry: object | None = None,
    ) -> BuiltPrompt:
        # 按 config.active_agent().rules 过滤出本次使用的 rule 集
        effective_rules = self._filter_registry(config)
        ctx = self._make_context(
            config, plan_mode, cwd, instructions_text,
            memory_index_user, memory_index_project, skill_registry,
        )
        # BuildContext.frozen? 不是 — 用 replace 简单替换 rule_registry
        ctx = BuildContext(**{**ctx.__dict__, "rule_registry": effective_rules})

        # 0. v0.8:三层 BaoZiCode.md 拼接结果(空 → 跳过)
        instructions_block = (
            f"## 项目指令\n{ctx.instructions_text}" if ctx.instructions_text else ""
        )

        # 1. 拼 7 个固定 sections
        fixed_blocks = [fn.render(ctx) for fn in _FIXED_SECTIONS]
        # 2. 拼 3 个可选 sections(空内容跳过)
        optional_blocks = [fn.render(ctx) for fn in _OPTIONAL_SECTIONS if fn.render(ctx)]
        # 3. 用 \n\n 串联
        blocks: list[str] = []
        if instructions_block:
            blocks.append(instructions_block)
        blocks.extend(fixed_blocks)
        blocks.extend(optional_blocks)
        stable = "\n\n".join(blocks)

        # 4. 增强工具描述(plan_mode 时只保留 side_effect=False 的只读工具)
        source_tools = tools if not plan_mode else [t for t in tools if not t.side_effect]
        augmented = [effective_rules.augment_tool(t) for t in source_tools]

        # 5. env_info 走 user-role 消息
        env_msg = _build_env_message(ctx)

        # 6. cache break points(接口级,v0.4 不实际生效)
        breakpoints = [
            CacheBreakpoint("system_start", priority=100),
            CacheBreakpoint("after_tools", priority=80),
        ]

        return BuiltPrompt(
            stable_system=stable,
            dynamic_messages=[env_msg],
            augmented_tools=augmented,
            cache_breakpoints=breakpoints,
        )


__all__ = ["PromptBuilder"]
