"""已激活 Skill — 扫描 skills_dir/*.md。

v0.4 还没 Skill 系统,扫到空目录返回空字符串,不报错。
"""

from __future__ import annotations

from pathlib import Path

from baozicode.prompt.types import BuildContext


def render(ctx: BuildContext) -> str:
    skills_dir = getattr(ctx.config, "skills_dir", None)
    if skills_dir is None:
        return ""
    path = Path(skills_dir)
    if not path.exists() or not path.is_dir():
        return ""
    files = sorted(path.glob("*.md"))
    if not files:
        return ""
    blocks: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8").strip()
        if text:
            blocks.append(f"### {f.stem}\n{text}")
    if not blocks:
        return ""
    return "## 已激活 Skill\n\n" + "\n\n".join(blocks)


__all__ = ["render"]
