"""身份模块 — BaoZiCode 是谁。"""

from baozicode.prompt.types import BuildContext

_DEFAULT_IDENTITY = "你是 BaoZiCode,v0.3 版本的 AI 编程助手。"


def render(ctx: BuildContext) -> str:
    user_provided = ctx.config.system_prompt
    extra = ""
    if user_provided and user_provided != "You are BaoZiCode, a helpful AI coding assistant.":
        extra = f"\n{user_provided}"
    return f"""## 身份
{_DEFAULT_IDENTITY}{extra}""".rstrip()


__all__ = ["render"]
