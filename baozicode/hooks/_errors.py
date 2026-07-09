"""Hook 系统共用错误类型。

放独立文件避免 schema / registry 之间的循环 import。
"""
from __future__ import annotations

from typing import Any


class HookValidationError(Exception):
    """Hook 配置 / 解析期错误。承载多条子错误,格式化为多行消息。

    用法:
    - 构造时直接传 errors=[{"hook_id": ..., "field": ..., "reason": ...}, ...]
    - raise 之前调 .format() 拼多行可读字符串
    """

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(self.format())

    def format(self) -> str:
        if not self.errors:
            return "hook validation failed (unknown reason)"
        lines = [f"hook validation failed ({len(self.errors)} error(s)):"]
        for err in self.errors:
            hook_id = err.get("hook_id", "<unknown>")
            field = err.get("field", "<unknown>")
            reason = err.get("reason", "<unknown>")
            lines.append(f"  - hooks[{hook_id}]: {field}: {reason}")
        return "\n".join(lines)

    @classmethod
    def from_pydantic(cls, exc: Any) -> "HookValidationError":
        """从 Pydantic ValidationError 转 HookValidationError。

        每条 pydantic error 带 loc 路径,转成 (hook_id, field, reason) 三元。
        """
        errors: list[dict[str, Any]] = []
        try:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err.get("loc", ()))
                errors.append({
                    "hook_id": err.get("input", {}).get("id", "<unknown>") if isinstance(err.get("input"), dict) else "<unknown>",
                    "field": loc or "<root>",
                    "reason": err.get("msg", "<unknown>"),
                })
        except Exception:
            errors.append({"hook_id": "<unknown>", "field": "<root>", "reason": str(exc)})
        return cls(errors)


class HookParseError(Exception):
    """解析期运行错误(parse_expr / shell command / etc.)。

    不阻断 Agent,只 log;但 hook 配置错导致的应属于 HookValidationError(启动期拦截)。
    """


class HookConditionError(Exception):
    """Condition 求值异常(如 all/any 同时存在)。"""


class HookSlotError(Exception):
    """Slot 注入失败(如 stable_system 注入到 tool.pre/tool.post 事件)。"""


class HookActionError(Exception):
    """Action 执行器异常(非用户预期路径,如创建子进程失败)。"""
