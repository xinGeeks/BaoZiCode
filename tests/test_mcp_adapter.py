"""adapter 模块单元测试。"""

from __future__ import annotations

import pytest

from baozicode.mcp.adapter import (
    adapt_call_result,
    adapt_tool,
    make_namespaced_name,
    parse_namespaced_name,
)
from baozicode.mcp.types import McpCallResult, McpTool


class TestNamespacedName:
    def test_simple(self) -> None:
        assert make_namespaced_name("filesystem", "read_file") == "mcp__filesystem__read_file"

    def test_double_underscore_in_server_replaced(self) -> None:
        # server name "a__b" 应清洗为 "a_b" — 避免反向解析歧义
        assert make_namespaced_name("a__b", "tool") == "mcp__a_b__tool"

    def test_double_underscore_in_tool_replaced(self) -> None:
        assert make_namespaced_name("srv", "foo__bar") == "mcp__srv__foo_bar"

    def test_round_trip(self) -> None:
        full = make_namespaced_name("github", "search_repos")
        parsed = parse_namespaced_name(full)
        assert parsed == ("github", "search_repos")

    def test_parse_non_mcp_returns_none(self) -> None:
        assert parse_namespaced_name("Read") is None
        assert parse_namespaced_name("mcp__") is None
        assert parse_namespaced_name("mcp__only_server") is None  # 缺 tool
        assert parse_namespaced_name("mcp____tool") is None  # server 为空


class TestAdaptTool:
    def test_default_conservative_values(self) -> None:
        mcp = McpTool(name="write", description="write a file",
                      input_schema={"type": "object", "properties": {}})
        td = adapt_tool("fs", mcp)
        assert td.name == "mcp__fs__write"
        assert td.description == "write a file"
        assert td.side_effect is True
        assert td.risk == "high"
        assert td.path_args == []

    def test_readonly_annotation_lowers_risk(self) -> None:
        mcp = McpTool(
            name="list",
            description="list files",
            input_schema={"type": "object"},
            annotations={"readOnlyHint": True},
        )
        td = adapt_tool("fs", mcp)
        assert td.side_effect is False
        assert td.risk == "low"

    def test_readonly_false_stays_conservative(self) -> None:
        """annotations 存在但 readOnlyHint=False → 仍按保守。"""
        mcp = McpTool(
            name="write",
            description="x",
            input_schema={"type": "object"},
            annotations={"readOnlyHint": False},
        )
        td = adapt_tool("fs", mcp)
        assert td.side_effect is True
        assert td.risk == "high"

    def test_path_args_heuristic(self) -> None:
        mcp = McpTool(
            name="move",
            description="move file",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "directory": {"type": "string"},
                    "query": {"type": "string"},  # 不应被选中
                    "limit": {"type": "integer"},  # 不是 string
                    "pattern": {"type": "string"},  # 不含 path/file/dir/root
                    "root_dir": {"type": "string"},  # 应被选中
                },
            },
        )
        td = adapt_tool("fs", mcp)
        assert set(td.path_args) == {"file_path", "directory", "root_dir"}

    def test_input_schema_with_no_properties(self) -> None:
        mcp = McpTool(name="ping", description="ping", input_schema={"type": "object"})
        td = adapt_tool("srv", mcp)
        assert td.parameters == {"type": "object"}
        assert td.path_args == []

    def test_input_schema_with_array_of_types(self) -> None:
        mcp = McpTool(
            name="x",
            description="x",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": ["string", "null"]},
                },
            },
        )
        td = adapt_tool("srv", mcp)
        assert "file_path" in td.path_args


class TestAdaptCallResult:
    def test_text_only(self) -> None:
        r = McpCallResult(content=[{"type": "text", "text": "hello"}], is_error=False)
        out = adapt_call_result(r)
        assert out.content == "hello"
        assert out.is_error is False

    def test_multi_text_blocks_joined(self) -> None:
        r = McpCallResult(
            content=[
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
            is_error=False,
        )
        out = adapt_call_result(r)
        assert out.content == "first\nsecond"

    def test_mixed_text_and_image(self) -> None:
        r = McpCallResult(
            content=[
                {"type": "text", "text": "look:"},
                {"type": "image", "data": "base64data", "mimeType": "image/png"},
            ],
            is_error=False,
        )
        out = adapt_call_result(r)
        assert "look:" in out.content
        assert "[image:" in out.content
        assert "image/png" in out.content

    def test_error_flag_propagates(self) -> None:
        r = McpCallResult(content=[{"type": "text", "text": "oops"}], is_error=True)
        out = adapt_call_result(r)
        assert out.is_error is True
        assert out.content == "oops"

    def test_empty_content(self) -> None:
        r = McpCallResult(content=[], is_error=False)
        out = adapt_call_result(r)
        assert out.content == ""
        assert out.is_error is False
