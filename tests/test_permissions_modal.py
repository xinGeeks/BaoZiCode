"""L5 PermissionModal + derive_glob_pattern 测试(v0.5)。

不启动 Textual(那是 UI 测试,本测试只覆盖纯函数)。
"""

from __future__ import annotations

import json

import pytest

from baozicode.tools.base import ToolCall
from baozicode.tui.chat_screen import _call_deny_key
from baozicode.tui.permission_modal import (
    PermissionChoice,
    derive_glob_pattern,
)


# ---- PermissionChoice enum ----

class TestPermissionChoice:
    def test_four_choices(self) -> None:
        assert PermissionChoice.ONCE.value == "once"
        assert PermissionChoice.SESSION.value == "session"
        assert PermissionChoice.PERSISTENT.value == "persistent"
        assert PermissionChoice.DENY.value == "deny"

    def test_string_enum(self) -> None:
        # 应当可与 str 比较(Textual dismiss 返回的兼容性)
        assert PermissionChoice.ONCE == "once"


# ---- derive_glob_pattern ----

class TestDeriveGlobPatternBash:
    def test_single_token(self) -> None:
        call = ToolCall(id="t", name="Bash", arguments={"command": "ls"})
        assert derive_glob_pattern(call) == "ls *"

    def test_subcommand(self) -> None:
        call = ToolCall(id="t", name="Bash", arguments={"command": "git status"})
        assert derive_glob_pattern(call) == "git status *"

    def test_subcommand_with_flag(self) -> None:
        call = ToolCall(
            id="t", name="Bash", arguments={"command": "git status -sb"},
        )
        assert derive_glob_pattern(call) == "git status *"

    def test_npm_test_coverage(self) -> None:
        # 任务规范的参考示例
        call = ToolCall(
            id="t", name="Bash", arguments={"command": "npm test --coverage"},
        )
        assert derive_glob_pattern(call) == "npm test *"

    def test_first_arg_is_flag(self) -> None:
        call = ToolCall(id="t", name="Bash", arguments={"command": "pytest -v"})
        # "v" 是 flag → 只取第一个 token
        assert derive_glob_pattern(call) == "pytest *"

    def test_first_arg_path(self) -> None:
        call = ToolCall(
            id="t", name="Bash", arguments={"command": "pytest tests/"},
        )
        # "tests/" 不是 flag → 取前两个
        assert derive_glob_pattern(call) == "pytest tests/ *"

    def test_empty_command(self) -> None:
        call = ToolCall(id="t", name="Bash", arguments={"command": ""})
        assert derive_glob_pattern(call) == "*"

    def test_no_command_arg(self) -> None:
        call = ToolCall(id="t", name="Bash", arguments={})
        assert derive_glob_pattern(call) == "*"

    def test_cat_file(self) -> None:
        call = ToolCall(id="t", name="Bash", arguments={"command": "cat foo.txt"})
        assert derive_glob_pattern(call) == "cat foo.txt *"


class TestDeriveGlobPatternNonBash:
    def test_read_uses_filename(self) -> None:
        call = ToolCall(
            id="t", name="Read", arguments={"file_path": "/some/dir/foo.py"},
        )
        assert derive_glob_pattern(call) == "foo.py"

    def test_write_uses_filename(self) -> None:
        call = ToolCall(
            id="t", name="Write",
            arguments={"file_path": "baozicode/app.py", "content": "x"},
        )
        assert derive_glob_pattern(call) == "app.py"

    def test_edit_uses_filename(self) -> None:
        call = ToolCall(
            id="t", name="Edit",
            arguments={"file_path": "deep/nested/file.py", "old_string": "a", "new_string": "b"},
        )
        assert derive_glob_pattern(call) == "file.py"

    def test_no_string_arg(self) -> None:
        call = ToolCall(id="t", name="Bash", arguments={"timeout": 30})
        # 没有 str 值 → "*"
        assert derive_glob_pattern(call) == "*"

    def test_grep_pattern(self) -> None:
        call = ToolCall(
            id="t", name="Grep", arguments={"pattern": "foo", "path": "."},
        )
        # 第一个 str 是 pattern
        assert derive_glob_pattern(call) == "foo"


# ---- _call_deny_key ----

class TestCallDenyKey:
    def test_different_args_different_key(self) -> None:
        c1 = ToolCall(id="t1", name="Bash", arguments={"command": "git status"})
        c2 = ToolCall(id="t2", name="Bash", arguments={"command": "git diff"})
        assert _call_deny_key(c1) != _call_deny_key(c2)

    def test_same_args_same_key(self) -> None:
        c1 = ToolCall(id="t1", name="Bash", arguments={"command": "git status"})
        c2 = ToolCall(id="t2", name="Bash", arguments={"command": "git status"})
        assert _call_deny_key(c1) == _call_deny_key(c2)

    def test_args_order_independent(self) -> None:
        # sort_keys=True 让 dict 顺序不影响 key
        c1 = ToolCall(
            id="t", name="Write",
            arguments={"file_path": "a.py", "content": "x"},
        )
        c2 = ToolCall(
            id="t", name="Write",
            arguments={"content": "x", "file_path": "a.py"},
        )
        assert _call_deny_key(c1) == _call_deny_key(c2)

    def test_different_tools_different_key(self) -> None:
        c1 = ToolCall(id="t", name="Bash", arguments={"command": "foo"})
        c2 = ToolCall(id="t", name="Read", arguments={"file_path": "foo"})
        assert _call_deny_key(c1) != _call_deny_key(c2)

    def test_key_is_tuple(self) -> None:
        call = ToolCall(id="t", name="Bash", arguments={"command": "ls"})
        key = _call_deny_key(call)
        assert isinstance(key, tuple)
        assert len(key) == 2
        assert key[0] == "Bash"
        # 第二项是合法 JSON
        json.loads(key[1])
