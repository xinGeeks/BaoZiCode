"""prompt modules — 每个模块一个文件,导出 render(ctx) -> str。"""

from baozicode.prompt.sections import (  # noqa: F401
    action_exec,
    agents,
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

__all__ = [
    "action_exec",
    "agents",
    "constraints",
    "custom",
    "env_info",
    "identity",
    "memory",
    "skills",
    "task_mode",
    "text_output",
    "tone_style",
    "tool_usage",
]
