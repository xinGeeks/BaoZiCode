"""L2 PathSandbox 测试(v0.5)。

覆盖:
- 项目内相对/绝对路径放行
- /etc/passwd 等沙箱外路径拒
- .. 跳出沙箱拒
- symlink 逃逸拒
- $VAR / ${VAR} / ~ / `cmd` 拒
- Bash 链式命令多 path,一个逃逸整条拒
- WebFetch(URL 不是 path)不受影响
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.permissions.sandbox import PathSandbox
from baozicode.permissions.types import PermissionDecision
from baozicode.tools.base import ToolCall


def _read(path: str) -> ToolCall:
    return ToolCall(id="t1", name="Read", arguments={"file_path": path})


def _write(path: str, content: str = "x") -> ToolCall:
    return ToolCall(id="t2", name="Write", arguments={"file_path": path, "content": content})


def _edit(path: str) -> ToolCall:
    return ToolCall(
        id="t3",
        name="Edit",
        arguments={"file_path": path, "old_string": "x", "new_string": "y"},
    )


def _grep(path: str = ".") -> ToolCall:
    return ToolCall(id="t4", name="Grep", arguments={"pattern": "foo", "path": path})


def _glob(path: str = ".") -> ToolCall:
    return ToolCall(id="t5", name="Glob", arguments={"pattern": "*.py", "path": path})


def _bash(command: str) -> ToolCall:
    return ToolCall(id="t6", name="Bash", arguments={"command": command})


def _webfetch(url: str) -> ToolCall:
    return ToolCall(id="t7", name="WebFetch", arguments={"url": url})


@pytest.fixture
def sandbox(tmp_path: Path) -> PathSandbox:
    """用 tmp_path 作为沙箱根(自动 resolve)。"""
    return PathSandbox(real_root=tmp_path)


# ---- 文件类工具:沙箱内允许 ----

class TestFileToolsInsideAllowed:
    def test_read_inside_absolute(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        f = tmp_path / "foo.txt"
        f.write_text("hi")
        decision = sandbox.check(_read(str(f)))
        assert decision.decision == "fallthrough"

    def test_write_inside_relative(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        decision = sandbox.check(_write("baozicode/app.py"))
        # 相对路径 → real_root 下,沙箱内
        assert decision.decision == "fallthrough"

    def test_edit_inside_absolute(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        f = tmp_path / "deep" / "file.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
        decision = sandbox.check(_edit(str(f)))
        assert decision.decision == "fallthrough"

    def test_grep_default_cwd(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_grep("."))
        assert decision.decision == "fallthrough"

    def test_glob_default_cwd(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_glob("."))
        assert decision.decision == "fallthrough"

    def test_grep_with_subdir_path(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        subdir = tmp_path / "sub"
        subdir.mkdir()
        decision = sandbox.check(_grep(str(subdir)))
        assert decision.decision == "fallthrough"


# ---- 文件类工具:沙箱外拒绝 ----

class TestFileToolsOutsideDenied:
    def test_read_etc_passwd(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_read("/etc/passwd"))
        assert decision.decision == "deny"
        assert decision.layer == "L2_sandbox"

    def test_write_root_ssh(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_write("/root/.ssh/authorized_keys"))
        assert decision.decision == "deny"

    def test_edit_home_bashrc(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_edit("/home/user/.bashrc"))
        assert decision.decision == "deny"

    def test_grep_to_etc(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_grep("/etc"))
        assert decision.decision == "deny"

    def test_glob_to_tmp(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_glob("/tmp"))
        assert decision.decision == "deny"

    def test_denied_decision_has_matched_pattern(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_read("/etc/passwd"))
        assert decision.matched_pattern is not None
        assert "/etc/passwd" in decision.matched_pattern or decision.reason


# ---- .. 跳出沙箱 ----

class TestParentTraversalDenied:
    def test_double_dot_relative(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        # ../foo 在 tmp_path 同级的父目录,应该被拒
        decision = sandbox.check(_read("../foo.txt"))
        assert decision.decision == "deny"

    def test_triple_dot_relative(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        # ../../etc/passwd 也应该被拒
        decision = sandbox.check(_read("../../etc/passwd"))
        assert decision.decision == "deny"

    def test_dot_only_allowed(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        # ./ 在沙箱内,放行
        decision = sandbox.check(_read("./baozicode/app.py"))
        assert decision.decision == "fallthrough"


# ---- symlink 逃逸 ----

class TestSymlinkEscapeDenied:
    def test_symlink_escape(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        """在沙箱内创建 symlink 指向 /etc,Read 该 symlink 应该被拒。"""
        # 准备:在沙箱内建一个 symlink → /etc
        link = tmp_path / "escape"
        try:
            link.symlink_to("/etc")
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported on this platform")

        decision = sandbox.check(_read(str(link)))
        # 解析后的真实路径是 /etc,不在沙箱内,应该被拒
        assert decision.decision == "deny"
        assert decision.layer == "L2_sandbox"


# ---- shell expansion 拒绝 ----

class TestShellExpansionDenied:
    def test_dollar_var(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_read("$HOME/secret.txt"))
        assert decision.decision == "deny"

    def test_dollar_brace_var(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_read("${HOME}/secret.txt"))
        assert decision.decision == "deny"

    def test_tilde(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_read("~/secret.txt"))
        assert decision.decision == "deny"

    def test_backtick(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_read("`pwd`/foo.txt"))
        assert decision.decision == "deny"

    def test_dollar_paren(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_read("$(pwd)/foo.txt"))
        assert decision.decision == "deny"

    def test_tilde_alone(self, sandbox: PathSandbox) -> None:
        # 单独的 ~ 也算 shell expansion
        decision = sandbox.check(_read("~"))
        assert decision.decision == "deny"


# ---- Bash:regex 抽 path + 沙箱检查 ----

class TestBashPathExtraction:
    def test_bash_cat_inside(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        decision = sandbox.check(_bash("cat ./baozicode/app.py"))
        assert decision.decision == "fallthrough"

    def test_bash_cat_outside(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_bash("cat /etc/passwd"))
        assert decision.decision == "deny"
        assert decision.layer == "L2_sandbox"

    def test_bash_cat_relative_escape(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_bash("cat ../escape.txt"))
        assert decision.decision == "deny"

    def test_bash_no_path_fallthrough(self, sandbox: PathSandbox) -> None:
        # echo / ls / pwd 等无 path literal,放行
        decision = sandbox.check(_bash("echo hello"))
        assert decision.decision == "fallthrough"

    def test_bash_pwd_fallthrough(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_bash("pwd"))
        assert decision.decision == "fallthrough"

    def test_bash_chained_one_escapes_denied(self, sandbox: PathSandbox) -> None:
        # 链式命令,第一个 path 在沙箱内,第二个 path 逃逸 → 整条拒
        decision = sandbox.check(_bash("cat ./safe.txt && cat /etc/passwd"))
        assert decision.decision == "deny"

    def test_bash_chained_all_inside_allowed(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_bash("cat ./a.txt && cat ./b.txt"))
        assert decision.decision == "fallthrough"

    def test_bash_dollar_var_denied(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_bash("cat $HOME/secret.txt"))
        assert decision.decision == "deny"

    def test_bash_tilde_denied(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_bash("cat ~/secret.txt"))
        assert decision.decision == "deny"

    def test_bash_redirection_to_outside(self, sandbox: PathSandbox) -> None:
        # echo x > /etc/forbidden — 前缀是 >,regex 应该能抓到 /etc/forbidden
        decision = sandbox.check(_bash("echo secret > /etc/forbidden"))
        assert decision.decision == "deny"

    def test_bash_absolute_path_inside(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        subdir = tmp_path / "sub"
        subdir.mkdir()
        decision = sandbox.check(_bash(f"cat {subdir}/file.txt"))
        assert decision.decision == "fallthrough"


# ---- WebFetch 不受影响(URL 不是 path) ----

class TestWebFetchNotAffected:
    def test_webfetch_url_not_checked(self, sandbox: PathSandbox) -> None:
        decision = sandbox.check(_webfetch("https://example.com/foo"))
        assert decision.decision == "fallthrough"

    def test_webfetch_with_etc_in_url(self, sandbox: PathSandbox) -> None:
        # URL 里含 /etc 也不该被 L2 拦截(WebFetch 不走 path_args 也不走 bash regex)
        decision = sandbox.check(_webfetch("https://example.com/etc/passwd"))
        assert decision.decision == "fallthrough"


# ---- is_inside 直接 API ----

class TestIsInsideAPI:
    def test_is_inside_direct_child(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        assert sandbox.is_inside(tmp_path / "foo.txt") is True

    def test_is_inside_nested(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        assert sandbox.is_inside(tmp_path / "a" / "b" / "c.txt") is True

    def test_is_inside_parent_denied(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        parent = tmp_path.parent
        assert sandbox.is_inside(parent) is False

    def test_is_inside_sibling_denied(self, sandbox: PathSandbox, tmp_path: Path) -> None:
        sibling = tmp_path.parent / "sibling_dir"
        assert sandbox.is_inside(sibling) is False


# ---- 边界:空 / 缺省 ----

class TestEdgeCases:
    def test_read_no_path(self, sandbox: PathSandbox) -> None:
        # arguments 缺 file_path → 工具自己的错误处理,L2 视为 fallthrough
        call = ToolCall(id="t", name="Read", arguments={})
        decision = sandbox.check(call)
        assert decision.decision == "fallthrough"

    def test_bash_empty(self, sandbox: PathSandbox) -> None:
        call = ToolCall(id="t", name="Bash", arguments={"command": ""})
        decision = sandbox.check(call)
        assert decision.decision == "fallthrough"

    def test_unknown_tool_fallthrough(self, sandbox: PathSandbox) -> None:
        # 未声明 path_args 的工具 → fallthrough
        call = ToolCall(id="t", name="Mystery", arguments={"file_path": "/etc/passwd"})
        decision = sandbox.check(call)
        assert decision.decision == "fallthrough"
