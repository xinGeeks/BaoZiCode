"""WebFetch 工具 — 拉取 URL 内容,HTML 去 tags,返回 UTF-8 文本。"""

from __future__ import annotations

import re

import httpx

from baozicode.tools.base import ToolDefinition, ToolResult

DEFAULT_TIMEOUT_SECONDS = 30
MAX_BYTES = 50_000
MAX_HTML_RAW_BYTES = 2_000_000  # 去 tags 前先 cap 原始大小

# 简单 HTML 去 tag — 足以让 LLM 读到正文,不追求完美
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

TOOL = ToolDefinition(
    name="WebFetch",
    description=(
        "Fetch a URL and return its content as UTF-8 text. HTML responses "
        "are stripped of tags. Use for documentation lookups or API references."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP or HTTPS URL to fetch.",
            },
        },
        "required": ["url"],
    },
    risk="low",
    side_effect=False,
)


def _strip_html(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub("", html)
    text = _TAG_RE.sub(" ", text)
    # 解码常见 HTML entity 的最常见几个;不追求全
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    text = _WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


async def execute(arguments: dict) -> ToolResult:
    url = arguments.get("url")
    if not url:
        return ToolResult.error_result("", "WebFetch: missing required argument 'url'")

    if not (url.startswith("http://") or url.startswith("https://")):
        return ToolResult.error_result(
            "", f"WebFetch: url must start with http:// or https:// (got {url!r})"
        )

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "BaoZiCode/0.2"})
    except httpx.TimeoutException:
        return ToolResult.error_result("", f"WebFetch: timed out after {DEFAULT_TIMEOUT_SECONDS}s")
    except httpx.HTTPError as exc:
        return ToolResult.error_result("", f"WebFetch: HTTP error: {exc}")

    if resp.status_code >= 400:
        return ToolResult.error_result(
            "", f"WebFetch: HTTP {resp.status_code} for {url}"
        )

    raw = resp.content
    if len(raw) > MAX_HTML_RAW_BYTES:
        return ToolResult.error_result(
            "",
            f"WebFetch: response too large ({len(raw)} bytes > {MAX_HTML_RAW_BYTES})",
        )

    content_type = resp.headers.get("content-type", "")
    try:
        text = raw.decode(resp.encoding or "utf-8", errors="replace")
    except (LookupError, TypeError):
        text = raw.decode("utf-8", errors="replace")

    if "html" in content_type.lower():
        text = _strip_html(text)

    if len(text.encode("utf-8")) > MAX_BYTES:
        truncated = text.encode("utf-8")[:MAX_BYTES].decode("utf-8", errors="replace")
        text = truncated + f"\n... [truncated: response exceeded {MAX_BYTES} bytes]"

    return ToolResult.success("", text)


__all__ = ["TOOL", "execute"]