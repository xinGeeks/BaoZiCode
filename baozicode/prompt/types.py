"""prompt 包的核心数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from baozicode.llm.base import Message
from baozicode.tools.base import ToolDefinition

if TYPE_CHECKING:
    from baozicode.config.schema import AppConfig
    from baozicode.prompt.rules import RuleRegistry


CacheLocation = Literal["system_start", "system_end", "after_tools", "before_user"]
ReminderKind = Literal["env", "plan_mode", "task_complete", "cancel"]
ReminderTTL = Literal["once", "static", "session"]


@dataclass(frozen=True)
class CacheBreakpoint:
    """v0.4 接口级:声明这里应该有一个 cache 断点。v0.5 各后端落地。"""

    location: CacheLocation
    priority: int  # 0-100, 数字越大越优先保留


@dataclass
class SystemReminder:
    """运行时补充指令,被序列化为 <system-reminder> 标签的 user-role 消息。"""

    kind: ReminderKind
    content: str
    ttl: ReminderTTL = "static"


@dataclass
class BuildContext:
    """sections/*.py 的 render() 拿到的输入。"""

    config: "AppConfig"
    rule_registry: "RuleRegistry"
    plan_mode: bool = False
    cwd: str = "."
    os_name: str = "linux"
    python_version: str = "3.11.0"
    git_branch: str = ""
    git_commit: str = ""
    project_name: str = ""
    now_iso: str = ""


@dataclass
class BuiltPrompt:
    """PromptBuilder.build() 的产物,Agent 一次构建多次复用。"""

    stable_system: str = ""
    dynamic_messages: list[Message] = field(default_factory=list)
    augmented_tools: list[ToolDefinition] = field(default_factory=list)
    cache_breakpoints: list[CacheBreakpoint] = field(default_factory=list)


__all__ = [
    "BuiltPrompt",
    "BuildContext",
    "CacheBreakpoint",
    "SystemReminder",
]
