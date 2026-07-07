"""用户自定义指令 — 从 config.custom_instructions 读。"""

from baozicode.prompt.types import BuildContext


def render(ctx: BuildContext) -> str:
    text = (ctx.config.custom_instructions or "").strip()
    if not text:
        return ""
    return f"""## 自定义指令
{text}""".rstrip()


__all__ = ["render"]
