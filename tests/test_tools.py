"""7 个工具的 happy path + 错误 case 测试。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.tools.registry import execute_tool


def test_read_happy_path() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "hello.txt"
        p.write_text("hello world", encoding="utf-8")
        r = asyncio.run(execute_tool("Read", {"file_path": str(p)}))
        assert not r.is_error
        assert "hello world" in r.content
    print("[OK] Read: happy path")


def test_read_missing_file() -> None:
    r = asyncio.run(execute_tool("Read", {"file_path": "/nonexistent/__nope__.txt"}))
    assert r.is_error
    assert "not found" in r.content
    print("[OK] Read: missing file rejected")


def test_read_truncation() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "big.txt"
        # > MAX_LINES (2000) → 必须截断
        p.write_text("\n".join(f"line {i}" for i in range(3000)), encoding="utf-8")
        r = asyncio.run(execute_tool("Read", {"file_path": str(p)}))
        assert not r.is_error
        assert "truncated" in r.content
    print("[OK] Read: truncation marker present")


def test_write_creates_parents() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sub" / "deep" / "a.txt"
        r = asyncio.run(execute_tool("Write", {"file_path": str(p), "content": "x"}))
        assert not r.is_error
        assert p.exists()
        assert p.read_text(encoding="utf-8") == "x"
    print("[OK] Write: auto-mkdir parents")


def test_edit_unique_replacement() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.txt"
        p.write_text("foo bar baz", encoding="utf-8")
        r = asyncio.run(execute_tool(
            "Edit",
            {"file_path": str(p), "old_string": "bar", "new_string": "BAR"},
        ))
        assert not r.is_error
        assert p.read_text(encoding="utf-8") == "foo BAR baz"
    print("[OK] Edit: unique replacement")


def test_edit_zero_matches() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.txt"
        p.write_text("foo", encoding="utf-8")
        r = asyncio.run(execute_tool(
            "Edit",
            {"file_path": str(p), "old_string": "NOPE", "new_string": "x"},
        ))
        assert r.is_error
        assert "0 matches" in r.content
    print("[OK] Edit: zero matches rejected")


def test_edit_multiple_matches() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.txt"
        p.write_text("aaa", encoding="utf-8")
        r = asyncio.run(execute_tool(
            "Edit",
            {"file_path": str(p), "old_string": "a", "new_string": "b"},
        ))
        assert r.is_error
        assert "3 times" in r.content
    print("[OK] Edit: non-unique rejected")


def test_bash_simple_command() -> None:
    import platform
    if platform.system() == "Windows":
        cmd = "echo hi"
    else:
        cmd = "echo hi"
    r = asyncio.run(execute_tool("Bash", {"command": cmd}))
    assert not r.is_error, f"unexpected error: {r.content}"
    assert "hi" in r.content
    print("[OK] Bash: echo works")


def test_bash_capture_exit_code() -> None:
    import platform
    cmd = "exit 7" if platform.system() != "Windows" else "cmd /c exit 7"
    r = asyncio.run(execute_tool("Bash", {"command": cmd}))
    assert r.is_error
    assert "7" in r.content
    print("[OK] Bash: non-zero exit captured")


def test_grep_finds_match() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.txt"
        p.write_text("hello world\nfoo bar\nbaz qux", encoding="utf-8")
        r = asyncio.run(execute_tool(
            "Grep", {"pattern": "foo", "path": str(p)}
        ))
        assert not r.is_error
        assert "foo" in r.content
        assert ":2:" in r.content  # line number
    print("[OK] Grep: matches found")


def test_grep_no_matches() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.txt"
        p.write_text("hello", encoding="utf-8")
        r = asyncio.run(execute_tool(
            "Grep", {"pattern": "NOMATCH_XYZ", "path": str(p)}
        ))
        assert not r.is_error
        assert "no matches" in r.content
    print("[OK] Grep: no matches")


def test_glob_finds_files() -> None:
    with tempfile.TemporaryDirectory() as td:
        Path(td, "a.py").write_text("x")
        Path(td, "b.txt").write_text("y")
        r = asyncio.run(execute_tool(
            "Glob", {"pattern": "*.py", "path": td}
        ))
        assert not r.is_error
        assert "a.py" in r.content
        assert "b.txt" not in r.content
    print("[OK] Glob: pattern filtered")


def test_glob_no_matches() -> None:
    with tempfile.TemporaryDirectory() as td:
        r = asyncio.run(execute_tool(
            "Glob", {"pattern": "*.zzz", "path": td}
        ))
        assert not r.is_error
        assert "no matches" in r.content
    print("[OK] Glob: empty result")


def test_webfetch_invalid_url() -> None:
    r = asyncio.run(execute_tool("WebFetch", {"url": "not-a-url"}))
    assert r.is_error
    assert "http://" in r.content
    print("[OK] WebFetch: invalid scheme rejected")


def test_webfetch_http_error_status() -> None:
    """HTTP 4xx/5xx 响应必须被当作错误返回,且错误消息包含 status code。

    用 httpbin.org/status/404;httpbin 偶尔会 503(限流),但只要 status >= 400
    且错误消息包含 *某个* HTTP status code 就算契约正确。
    """
    import re

    r = asyncio.run(execute_tool(
        "WebFetch", {"url": "https://httpbin.org/status/404"}
    ))
    assert r.is_error
    assert re.search(r"HTTP \d{3}", r.content), f"expected HTTP status code in error, got: {r.content!r}"
    print("[OK] WebFetch: HTTP error status captured")


if __name__ == "__main__":
    test_read_happy_path()
    test_read_missing_file()
    test_read_truncation()
    test_write_creates_parents()
    test_edit_unique_replacement()
    test_edit_zero_matches()
    test_edit_multiple_matches()
    test_bash_simple_command()
    test_bash_capture_exit_code()
    test_grep_finds_match()
    test_grep_no_matches()
    test_glob_finds_files()
    test_glob_no_matches()
    test_webfetch_invalid_url()
    test_webfetch_http_error_status()
    print("\nAll tool tests passed.")