"""Glob 工具 — pathlib.Path.glob 包装,返回相对路径列表。"""

from __future__ import annotations

import os
from pathlib import Path

from baozicode.tools.base import ToolDefinition, ToolResult

MAX_RESULTS = 1000

TOOL = ToolDefinition(
    name="Glob",
    description=(
        "List files matching a glob pattern. Returns paths relative to the "
        "search directory, one per line, sorted. `**` matches recursively."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. `**/*.py` or `src/*.md`.",
            },
            "path": {
                "type": "string",
                "description": "Base directory to search (default: current working directory).",
            },
        },
        "required": ["pattern"],
    },
    risk="low",
    side_effect=False,
    path_args=["path"],
)


async def execute(arguments: dict) -> ToolResult:
    pattern = arguments.get("pattern")
    if not pattern:
        return ToolResult.error_result("", "Glob: missing required argument 'pattern'")

    path_str = arguments.get("path") or "."
    base = Path(path_str)
    if not base.exists():
        return ToolResult.error_result("", f"Glob: path not found: {path_str}")
    if not base.is_dir():
        return ToolResult.error_result("", f"Glob: not a directory: {path_str}")

    matches: list[Path] = []
    for p in base.glob(pattern):
        if not p.exists():
            continue
        try:
            rel = p.resolve().relative_to(base.resolve())
        except ValueError:
            rel = Path(p.name)
        matches.append(rel)

    matches.sort()
    if len(matches) > MAX_RESULTS:
        matches = matches[:MAX_RESULTS]
        truncated_note = f"\n... [truncated: showing first {MAX_RESULTS} matches]"
    else:
        truncated_note = ""

    if not matches:
        return ToolResult.success("", "(no matches)")

    output = "\n".join(os.fspath(m) for m in matches) + truncated_note
    return ToolResult.success("", output)


__all__ = ["TOOL", "execute"]