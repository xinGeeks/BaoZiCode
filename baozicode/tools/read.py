"""Read 工具 — 读 UTF-8 文本文件，带行/字节 cap。"""

from __future__ import annotations

from pathlib import Path

from baozicode.tools.base import ToolDefinition, ToolResult

# 硬 cap，防止大文件撑爆上下文
MAX_BYTES = 50_000
MAX_LINES = 2_000

TOOL = ToolDefinition(
    name="Read",
    description=(
        "Read a UTF-8 text file from disk. Returns the file content up to "
        f"{MAX_LINES} lines or {MAX_BYTES} bytes (whichever hits first). "
        "Use this before editing to confirm current contents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute or relative path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Optional 0-based line offset to start reading from.",
            },
        },
        "required": ["file_path"],
    },
    risk="low",
)


def _truncate(content: str, file_size: int) -> str:
    """按行/字节截断，并附说明。"""
    lines = content.splitlines(keepends=True)
    truncated_by_lines = len(lines) > MAX_LINES
    truncated_by_bytes = len(content.encode("utf-8")) > MAX_BYTES

    if not truncated_by_lines and not truncated_by_bytes:
        return content

    out_lines: list[str] = []
    running_bytes = 0
    for line in lines[:MAX_LINES]:
        b = len(line.encode("utf-8"))
        if running_bytes + b > MAX_BYTES:
            break
        out_lines.append(line)
        running_bytes += b

    truncated_content = "".join(out_lines)
    return (
        f"{truncated_content}"
        f"\n\n... [truncated: file is {file_size} bytes / "
        f"{len(lines)} lines; showing first {len(out_lines)} lines / "
        f"{running_bytes} bytes. Use offset= to read further.]"
    )


async def execute(arguments: dict) -> ToolResult:
    file_path = arguments.get("file_path")
    if not file_path:
        return ToolResult.error_result("", "Read: missing required argument 'file_path'")
    offset = arguments.get("offset", 0)

    path = Path(file_path)
    if not path.exists():
        return ToolResult.error_result("", f"Read: file not found: {file_path}")
    if not path.is_file():
        return ToolResult.error_result("", f"Read: not a regular file: {file_path}")

    try:
        file_size = path.stat().st_size
        if offset > 0:
            # 跳过前 offset 行
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for _ in range(offset):
                    f.readline()
                content = f.read()
        else:
            content = path.read_text(encoding="utf-8", errors="replace")

        truncated = _truncate(content, file_size)
        return ToolResult.success("", truncated)
    except UnicodeDecodeError as exc:
        return ToolResult.error_result("", f"Read: not a UTF-8 text file ({exc})")
    except OSError as exc:
        return ToolResult.error_result("", f"Read: I/O error: {exc}")