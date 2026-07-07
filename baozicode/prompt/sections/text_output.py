"""文本输出约定 — Markdown / 代码块。"""

from baozicode.prompt.types import BuildContext


def render(ctx: BuildContext) -> str:
    return """## 文本输出
- 代码用 ```language 围栏块
- 文件路径用反引号 `path/to/file`
- 列表用 `-` 或 `1.` Markdown
- 不要在文本里输出 ANSI 控制符或 emoji
- 长输出用 Markdown 标题分节""".rstrip()


__all__ = ["render"]
