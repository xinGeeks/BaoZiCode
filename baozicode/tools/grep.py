"""Grep 工具 — 用 ripgrep 搜文本,失败 fallback 到 Python re。

输出格式: `relative/path:line:content`(每行一个命中)。
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from baozicode.tools.base import ToolDefinition, ToolResult, decode_subprocess_output

MAX_RESULTS = 500
MAX_BYTES = 30_000

TOOL = ToolDefinition(
    name="Grep",
    description=(
        "Search for a regex pattern in files under a directory. "
        "Uses ripgrep (`rg`) when available; falls back to Python's `re` "
        "module. Output is `path:line:content` per match, sorted by path."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search (default: current working directory).",
            },
            "glob": {
                "type": "string",
                "description": "Optional glob filter, e.g. `*.py`.",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive search (default false).",
            },
            "max_results": {
                "type": "integer",
                "description": f"Cap on total matches (default {MAX_RESULTS}).",
            },
        },
        "required": ["pattern"],
    },
    risk="low",
    side_effect=False,
    path_args=["path"],
)


def _format_hits(hits: list[tuple[str, int, str]], base: Path) -> list[str]:
    """把 (abs_path, line, content) 渲染为 `rel:line:content` 行。"""
    out: list[str] = []
    for abs_path, line, content in hits:
        try:
            rel = str(Path(abs_path).resolve().relative_to(base.resolve()))
        except ValueError:
            rel = abs_path
        out.append(f"{rel}:{line}:{content}")
    return out


async def _rg_search(
    pattern: str,
    path: Path,
    glob: str | None,
    ignore_case: bool,
    max_results: int,
) -> list[tuple[str, int, str]] | None:
    """调 ripgrep;不可用时返回 None 触发 fallback。"""
    args: list[str] = [
        "rg",
        "--line-number",
        "--no-heading",
        "--color=never",
    ]
    if ignore_case:
        args.append("-i")
    if glob:
        args.extend(["--glob", glob])
    args.extend(["--", pattern, str(path)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None
    except OSError:
        return None

    try:
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except OSError:
            pass
        return None

    # rg exit 1 = no matches, 0 = matches, 2 = error
    if proc.returncode not in (0, 1):
        return None

    hits: list[tuple[str, int, str]] = []
    text = decode_subprocess_output(stdout_b)
    for raw in text.splitlines():
        # 格式:`abs/path:line:content`(rg 输出绝对路径)
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        try:
            line_num = int(parts[1])
        except ValueError:
            continue
        hits.append((parts[0], line_num, parts[2]))
        if len(hits) >= max_results:
            break
    return hits


def _py_search(
    pattern: str,
    path: Path,
    glob: str | None,
    ignore_case: bool,
    max_results: int,
) -> list[tuple[str, int, str]]:
    """Python re fallback — 遍历 Path.rglob 命中。"""
    flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"invalid regex: {exc}") from exc

    hits: list[tuple[str, int, str]] = []
    base = path if path.is_dir() else path.parent
    files: list[Path]
    if path.is_file():
        files = [path]
    else:
        files = [p for p in path.rglob("*") if p.is_file()]
    if glob:
        # 简单 fnmatch;rglob('*.py') 已经够用,所以 glob 走 fnmatch 过滤
        import fnmatch
        files = [p for p in files if fnmatch.fnmatch(p.name, glob)]

    for f in files:
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fp:
                for line_no, line in enumerate(fp, start=1):
                    if compiled.search(line):
                        hits.append((str(f), line_no, line.rstrip("\n")))
                        if len(hits) >= max_results:
                            return hits
        except OSError:
            continue
    return hits


async def execute(arguments: dict) -> ToolResult:
    pattern = arguments.get("pattern")
    if not pattern:
        return ToolResult.error_result("", "Grep: missing required argument 'pattern'")

    path_str = arguments.get("path") or "."
    path = Path(path_str)
    if not path.exists():
        return ToolResult.error_result("", f"Grep: path not found: {path_str}")

    glob_filter = arguments.get("glob")
    ignore_case = bool(arguments.get("ignore_case", False))
    max_results = int(arguments.get("max_results", MAX_RESULTS))
    max_results = max(1, min(max_results, MAX_RESULTS))

    base = path if path.is_dir() else path.parent
    try:
        hits = await _rg_search(pattern, path, glob_filter, ignore_case, max_results)
        if hits is None:
            hits = _py_search(pattern, path, glob_filter, ignore_case, max_results)
    except ValueError as exc:
        return ToolResult.error_result("", f"Grep: {exc}")

    if not hits:
        return ToolResult.success("", "(no matches)")

    rendered = _format_hits(hits, base)
    output = "\n".join(rendered)
    if len(output.encode("utf-8")) > MAX_BYTES:
        truncated = output.encode("utf-8")[:MAX_BYTES].decode("utf-8", errors="replace")
        output = truncated + f"\n... [truncated: results exceeded {MAX_BYTES} bytes]"
    return ToolResult.success("", output)


__all__ = ["TOOL", "execute"]