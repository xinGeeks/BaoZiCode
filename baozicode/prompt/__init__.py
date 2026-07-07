"""Modular system prompt builder (v0.4+).

Public API re-exports. To enable incremental development and per-module unit
testing, non-leaf imports (rules / reminder / builder) are attempted lazily.
This way `from baozicode.prompt.types import ...` works even before sibling
modules exist. Once all modules are present, the eager imports here succeed.

After Phase 1 completes, this file is functionally equivalent to a flat
re-export. The try/except is purely a development convenience.
"""

from baozicode.prompt.types import (
    BuiltPrompt,
    BuildContext,
    CacheBreakpoint,
    SystemReminder,
)

# 延迟导入叶子依赖(sections / rules / reminder / builder)
# 这样 types 测试可以单独跑通,即使其它文件还没写。
try:
    from baozicode.prompt.rules import (  # noqa: F401
        DEFAULT_RULES,
        Rule,
        RuleRegistry,
    )
except ImportError:  # pragma: no cover - 增量开发期
    DEFAULT_RULES = ()  # type: ignore[assignment]
    Rule = None  # type: ignore[assignment]
    RuleRegistry = None  # type: ignore[assignment]

try:
    from baozicode.prompt.reminder import PlanModeReminder  # noqa: F401
except ImportError:  # pragma: no cover - 增量开发期
    PlanModeReminder = None  # type: ignore[assignment]

try:
    from baozicode.prompt.builder import PromptBuilder  # noqa: F401
except ImportError:  # pragma: no cover - 增量开发期
    PromptBuilder = None  # type: ignore[assignment]


__all__ = [
    "BuiltPrompt",
    "BuildContext",
    "CacheBreakpoint",
    "DEFAULT_RULES",
    "PlanModeReminder",
    "PromptBuilder",
    "Rule",
    "RuleRegistry",
    "SystemReminder",
]
