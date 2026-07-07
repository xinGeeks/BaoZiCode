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
        self, config: object, plan_mode: bool, cwd: str | None = None
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
        )

    def build(
        self,
        config: object,
        plan_mode: bool,
        tools: list[ToolDefinition],
        cwd: str | None = None,
    ) -> BuiltPrompt:
        ctx = self._make_context(config, plan_mode, cwd)

        # 1. 拼 7 个固定 sections
        fixed_blocks = [fn.render(ctx) for fn in _FIXED_SECTIONS]
        # 2. 拼 3 个可选 sections(空内容跳过)
        optional_blocks = [fn.render(ctx) for fn in _OPTIONAL_SECTIONS if fn.render(ctx)]
        # 3. 用 \n\n 串联
        stable = "\n\n".join(fixed_blocks + optional_blocks)

        # 4. 增强工具描述
        augmented = [self._rules.augment_tool(t) for t in tools]

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
