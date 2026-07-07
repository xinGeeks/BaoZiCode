"""工具使用关键规则 — 7 条规则的集中地。"""

from baozicode.prompt.types import BuildContext


def render(ctx: BuildContext) -> str:
    rules_text = ctx.rule_registry.render_for_prompt()
    return f"""## 工具使用关键规则
{rules_text}""".rstrip()


__all__ = ["render"]
