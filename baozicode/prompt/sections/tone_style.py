"""语气风格 — 简洁、中文为主。"""

from baozicode.prompt.types import BuildContext


def render(ctx: BuildContext) -> str:
    return """## 语气风格
- 简洁:能用一句话说清的不用两句
- 中文为主,技术术语保留英文
- 不加客套(不要"好的,我会帮您...")
- 不重复用户的问题
- 不输出"作为 AI..."之类的元描述""".rstrip()


__all__ = ["render"]
