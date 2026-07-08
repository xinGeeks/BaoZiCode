"""`@include <path>` 解析器 — 三道安全关。

按 openspec/changes/v0-8-memory-and-sessions/specs/instructions-loader/spec.md
"## @include guards" 段:

- **深度限制:** `max_depth=5` 之后停止递归
- **环路检测:** `visited` 集合记录 current_file.resolve() 的祖宗链
- **路径白名单:** 解析后的 path 必须 `is_relative_to(project_root)`
  或 `is_relative_to(user_baozicode)`(~/.baozicode/),否则拒

失败时记 warning 但继续(不抛错),best-effort。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


_INCLUDE_PATTERN = re.compile(r"^@include\s+(.+?)\s*$")


def user_baozicode_root() -> Path:
    """用户全局 baozicode 根:`~/.baozicode/`。"""
    return Path.home() / ".baozicode"


def _is_under(path: Path, root: Path) -> bool:
    """path 是否在 root 之下(含 root 本身)。"""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_include_path(raw: str, current_file: Path) -> Path:
    """`./snippets/a.md` → 基于 current_file.parent;`/abs/path` → 直接用。

    其他情况(URL、scheme)作为相对路径处理,让下游 is_relative_to 拒掉。
    """
    raw = raw.strip()
    if not raw:
        return current_file  # 空 include → 视作 self,visited 会短路
    p = Path(raw)
    if p.is_absolute():
        return p
    return (current_file.parent / p).resolve()


def resolve_includes(
    text: str,
    current_file: Path,
    project_root: Path,
    *,
    max_depth: int = 5,
    visited: frozenset[Path] | None = None,
) -> tuple[str, list[str]]:
    """解析 text 中所有 `@include <path>` 行,递归展开(深度优先)。

    任何 guard 失败(深度/环路/路径)→ 记 warning,原行保留为注释,继续处理其它行。
    返回 (resolved_text, warnings)。
    """
    if visited is None:
        visited = frozenset()
    warnings: list[str] = []
    current_resolved = current_file.resolve()
    user_root = user_baozicode_root()

    out_lines: list[str] = []
    for line in text.splitlines():
        m = _INCLUDE_PATTERN.match(line)
        if not m:
            out_lines.append(line)
            continue

        raw_target = m.group(1).strip()
        # 深度 guard
        if max_depth <= 0:
            warnings.append(
                f"{current_file}: include depth limit reached, skipping {raw_target!r}"
            )
            out_lines.append(f"<!-- @include skipped (depth limit): {raw_target} -->")
            continue

        # 解析目标路径
        target = _resolve_include_path(raw_target, current_file)

        # 环路 guard
        if target in visited:
            warnings.append(
                f"{current_file}: include cycle detected for {target}, skipping"
            )
            out_lines.append(f"<!-- @include skipped (cycle): {raw_target} -->")
            continue

        # 路径白名单 guard
        if not (_is_under(target, project_root) or _is_under(target, user_root)):
            warnings.append(
                f"{current_file}: include path {target} escapes project_root and "
                f"user_baozicode, skipping"
            )
            out_lines.append(
                f"<!-- @include skipped (path escape): {raw_target} -->"
            )
            continue

        # 文件存在性
        if not target.is_file():
            warnings.append(
                f"{current_file}: include target {target} not found, skipping"
            )
            out_lines.append(
                f"<!-- @include skipped (missing): {raw_target} -->"
            )
            continue

        # 递归
        try:
            included_text = target.read_text(encoding="utf-8").strip()
        except OSError as exc:
            warnings.append(
                f"{current_file}: failed to read {target}: {exc}"
            )
            out_lines.append(
                f"<!-- @include skipped (read error): {raw_target} -->"
            )
            continue

        next_visited = visited | {current_resolved}
        resolved, sub_warnings = resolve_includes(
            included_text,
            target,
            project_root,
            max_depth=max_depth - 1,
            visited=next_visited,
        )
        warnings.extend(sub_warnings)
        out_lines.append(resolved)

    return ("\n".join(out_lines), warnings)


__all__ = ["resolve_includes", "user_baozicode_root"]
