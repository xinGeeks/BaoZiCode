"""长期记忆 — 渲染两层 index(user-level + project-level)。

v0.8 行为:
- `ctx.memory_index_user` / `ctx.memory_index_project` 各是 `MemoryIndex.format_for_prompt()` 的输出
- 两个都为空 / None → 返回 ""(不渲染该 section)
- 仅一个非空 → 渲染对应那层
- 两个都非空 → 渲染两层(用户级在前,项目级在后)
- 向后兼容:若 `config.memory_path` 存在且非默认 + 新两层都空,fallback 到旧单文件
"""

from __future__ import annotations

from pathlib import Path

from baozicode.prompt.types import BuildContext


def _render_layer(label: str, index_text: str) -> str:
    """把一层 index 文本包装成 `## 长期记忆 (<label>)\n<text>` 形式。"""
    return f"## 长期记忆 ({label})\n{index_text}"


def _deprecated_fallback(ctx: BuildContext) -> str | None:
    """读 `config.memory_path` 旧字段(只在两层都空时考虑)。"""
    path_obj = getattr(ctx.config, "memory_path", None)
    if path_obj is None:
        return None
    path = Path(path_obj)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return f"## 长期记忆\n{text}"


def render(ctx: BuildContext) -> str:
    user_text = (ctx.memory_index_user or "").strip()
    project_text = (ctx.memory_index_project or "").strip()

    blocks: list[str] = []
    if user_text:
        blocks.append(_render_layer("用户级", user_text))
    if project_text:
        blocks.append(_render_layer("项目级", project_text))

    if not blocks:
        # 向后兼容:旧 memory_path 仍可用
        return _deprecated_fallback(ctx) or ""
    return "\n\n".join(blocks)


__all__ = ["render"]
