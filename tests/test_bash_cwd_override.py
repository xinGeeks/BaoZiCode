"""v1.3 Bash `cwd` 参数 + D11 cwd_validator closure 注入测试。

覆盖 3 类路径:
- happy:`cwd=有效绝对路径` → 在该目录跑,不更 session.cwd
- 不传 `cwd` → 走 v1.2 老路径(plan_cd + commit session.cwd)
- cwd 越界 → 拒(cwd_validator 返回 False + 主 project_root 外)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baozicode.tools import bash as bash_mod
from baozicode.tools.bash import (
    BashSession,
    configure,
    execute,
    get_session,
    set_cwd_validator,
)


@pytest.fixture(autouse=True)
def _reset_bash_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试 fresh:clean sessions dict + cwd_validator,固定 project_root。"""
    bash_mod._sessions.clear()
    bash_mod._cwd_validator = None
    monkeypatch.setenv("BAZ_PROJECT_ROOT", str(tmp_path))
    configure(tmp_path)


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cwd_override_runs_in_specified_directory(
    tmp_path: Path,
) -> None:
    """`cwd=<subdir>` → 命令在该目录跑;session.cwd 不更。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    marker = sub / "marker.txt"
    marker.write_text("in-sub", encoding="utf-8")

    session = get_session(tmp_path)
    assert session is not None
    original_cwd = session.cwd

    result = await execute({
        "command": "cat marker.txt",
        "cwd": str(sub),
    })

    assert not result.is_error, result.content
    assert "in-sub" in result.content
    # session.cwd 没变
    assert session.cwd == original_cwd


@pytest.mark.asyncio
async def test_cwd_override_skips_plan_cd(
    tmp_path: Path,
) -> None:
    """`cwd` 模式下不调 plan_cd —— 即便命令里有 cd,也不更新 session.cwd。"""
    sub = tmp_path / "sub"
    sub.mkdir()

    session = get_session(tmp_path)
    original_cwd = session.cwd

    # 命令里有 cd + && 链 — 在 v1.2 老路径下会被 plan_cd 识别并更 session.cwd
    # 但在 cwd_override 模式下完全跳过
    result = await execute({
        "command": f"cd {sub} && pwd",
        "cwd": str(sub),
    })

    assert not result.is_error, result.content
    # session.cwd 没变(关键测试点)
    assert session.cwd == original_cwd


# ---------------------------------------------------------------------------
# 不传 cwd → 走 v1.2 老路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_cwd_uses_v12_plan_cd_path(tmp_path: Path) -> None:
    """不传 cwd → 走 plan_cd + commit session.cwd(行为零变化)。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "f.txt").write_text("hello", encoding="utf-8")

    session = get_session(tmp_path)
    # 起始 cwd == project_root
    assert session.cwd == tmp_path.resolve()

    result = await execute({"command": f"cd {sub} && cat f.txt"})

    assert not result.is_error
    assert "hello" in result.content
    # session.cwd 跟着 cd → sub
    assert session.cwd == sub.resolve()


# ---------------------------------------------------------------------------
# cwd 越界 / 非法 → 拒
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cwd_must_be_absolute(tmp_path: Path) -> None:
    """相对路径 cwd → 拒。"""
    result = await execute({"command": "ls", "cwd": "relative/path"})
    assert result.is_error
    assert "绝对路径" in result.content


@pytest.mark.asyncio
async def test_cwd_must_exist(tmp_path: Path) -> None:
    """不存在的 cwd → 拒。"""
    result = await execute({
        "command": "ls", "cwd": str(tmp_path / "nonexistent"),
    })
    assert result.is_error
    assert "不存在" in result.content


@pytest.mark.asyncio
async def test_cwd_must_be_directory(tmp_path: Path) -> None:
    """cwd 指向文件而非目录 → 拒。"""
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    result = await execute({"command": "ls", "cwd": str(f)})
    assert result.is_error
    assert "不是目录" in result.content


@pytest.mark.asyncio
async def test_cwd_outside_project_root_rejected(tmp_path: Path) -> None:
    """cwd 在 project_root 外 + 没注入 validator → 拒。"""
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    try:
        result = await execute({"command": "ls", "cwd": str(outside)})
        assert result.is_error
        assert "不在任何有效 root 内" in result.content
    finally:
        if outside.exists() and not list(outside.iterdir()):
            outside.rmdir()


@pytest.mark.asyncio
async def test_cwd_validator_accepts_outside_root(tmp_path: Path) -> None:
    """D11:cwd_validator 注入 closure → 接受 main project_root 外的 cwd。"""
    outside = tmp_path.parent / "outside_v"
    outside.mkdir(exist_ok=True)
    try:
        # validator 只接受 outside 这个特定路径
        def validator(p: Path) -> bool:
            return p.resolve() == outside.resolve()

        set_cwd_validator(validator)
        try:
            result = await execute({
                "command": "pwd", "cwd": str(outside),
            })
            assert not result.is_error, result.content
            # 命令在 outside 跑(用 Path 比较,Windows 上 pwd 输出
            # 用 forward-slash / Path.resolve() 用 backslash,只比 basename)
            assert outside.name in result.content
        finally:
            set_cwd_validator(None)
    finally:
        if outside.exists() and not list(outside.iterdir()):
            outside.rmdir()


@pytest.mark.asyncio
async def test_cwd_validator_can_be_cleared(tmp_path: Path) -> None:
    """set_cwd_validator(None) → 清除(回到默认:只在主 root 内)。"""
    outside = tmp_path.parent / "outside_clear"
    outside.mkdir(exist_ok=True)
    try:
        # 先 set,再 clear
        set_cwd_validator(lambda p: True)
        set_cwd_validator(None)
        result = await execute({
            "command": "pwd", "cwd": str(outside),
        })
        assert result.is_error
        assert "不在任何有效 root 内" in result.content
    finally:
        if outside.exists() and not list(outside.iterdir()):
            outside.rmdir()


# ---------------------------------------------------------------------------
# Bash TOOL schema 加 cwd 字段
# ---------------------------------------------------------------------------


def test_tool_schema_has_cwd_parameter() -> None:
    """Bash 工具 schema 加 cwd optional 字段。"""
    props = bash_mod.TOOL.parameters["properties"]
    assert "cwd" in props
    assert props["cwd"]["type"] == "string"
    # optional(不在 required 里)
    assert "cwd" not in bash_mod.TOOL.parameters["required"]
    # command 仍是必填
    assert "command" in bash_mod.TOOL.parameters["required"]