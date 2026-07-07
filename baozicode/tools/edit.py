"""Edit 工具 — old_string/new_string 精确替换。

old_string 在目标文件中必须恰好出现一次；0 次或 >1 次都拒绝执行。
"""

from __future__ import annotations

from pathlib import Path

from baozicode.tools.base import ToolDefinition, ToolResult

TOOL = ToolDefinition(
    name="Edit",
    description=(
        "Replace an exact substring in a file with a new string. The old_string "
        "must appear EXACTLY ONCE in the file; if it appears 0 or more than 1 "
        "times, the edit is rejected and the file is not modified. Always "
        "Read the file first to confirm the current contents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": "The exact substring to replace.",
            },
            "new_string": {
                "type": "string",
                "description": "The replacement string.",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    risk="high",
    side_effect=True,
    path_args=["file_path"],
)


async def execute(arguments: dict) -> ToolResult:
    file_path = arguments.get("file_path")
    old_string = arguments.get("old_string")
    new_string = arguments.get("new_string")

    if not file_path:
        return ToolResult.error_result("", "Edit: missing required argument 'file_path'")
    if old_string is None:
        return ToolResult.error_result("", "Edit: missing required argument 'old_string'")
    if new_string is None:
        return ToolResult.error_result("", "Edit: missing required argument 'new_string'")

    path = Path(file_path)
    if not path.exists():
        return ToolResult.error_result("", f"Edit: file not found: {file_path}")
    if not path.is_file():
        return ToolResult.error_result("", f"Edit: not a regular file: {file_path}")

    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult.error_result("", f"Edit: read error: {exc}")

    count = original.count(old_string)
    if count == 0:
        return ToolResult.error_result(
            "", f"Edit: old_string not found in {file_path} (0 matches)"
        )
    if count > 1:
        return ToolResult.error_result(
            "",
            f"Edit: old_string appears {count} times in {file_path}; "
            "must be unique. Provide more context.",
        )

    updated = original.replace(old_string, new_string, 1)
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return ToolResult.error_result("", f"Edit: write error: {exc}")

    return ToolResult.success(
        "", f"Edited {file_path}: replaced 1 occurrence ({len(old_string)} -> {len(new_string)} chars)"
    )