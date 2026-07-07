"""长期记忆 — 读 memory_path 文件。"""

from __future__ import annotations

from pathlib import Path

from baozicode.prompt.types import BuildContext


def render(ctx: BuildContext) -> str:
    path_obj = getattr(ctx.config, "memory_path", None)
    if path_obj is None:
        return ""
    path = Path(path_obj)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    return f"## 长期记忆\n{text}"


__all__ = ["render"]
