"""Write 工具 — 整文件覆写，自动创建父目录。"""

from __future__ import annotations

from pathlib import Path

from baozicode.tools.base import ToolDefinition, ToolResult

TOOL = ToolDefinition(
    name="Write",
    description=(
        "Write content to a file on disk, creating parent directories as needed. "
        "Overwrites the file if it already exists. Use Edit for surgical changes; "
        "use Write only for new files or full rewrites."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to write (absolute or relative).",
            },
            "content": {
                "type": "string",
                "description": "Full file content to write.",
            },
        },
        "required": ["file_path", "content"],
    },
    risk="high",
    side_effect=True,
)


async def execute(arguments: dict) -> ToolResult:
    file_path = arguments.get("file_path")
    content = arguments.get("content")
    if not file_path:
        return ToolResult.error_result("", "Write: missing required argument 'file_path'")
    if content is None:
        return ToolResult.error_result("", "Write: missing required argument 'content'")

    path = Path(file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        return ToolResult.success("", f"Wrote {byte_count} bytes to {file_path}")
    except OSError as exc:
        return ToolResult.error_result("", f"Write: I/O error: {exc}")