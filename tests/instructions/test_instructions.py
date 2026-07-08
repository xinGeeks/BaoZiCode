"""v0.8 instructions loader + @include resolver + concat."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr
from dataclasses import dataclass
from pathlib import Path

import pytest

from baozicode.instructions import (
    InstructionLayer,
    LoadedInstructions,
    bootstrap,
    concat,
    load_layer,
    resolve_includes,
    scan_three_tiers,
)
from baozicode.instructions.include import user_baozicode_root


# ---------------------------------------------------------------------------
# 测试用 fake config(bootstrap() 接受 AppConfig,但 v0.8 不读任何开关)
# ---------------------------------------------------------------------------


@dataclass
class FakeConfig:
    """bootstrap() 不读任何字段;只用于满足签名。"""

    pass


# ---------------------------------------------------------------------------
# 1. scan_three_tiers / load_layer — 基础扫盘与读取
# ---------------------------------------------------------------------------


def test_scan_three_tiers_no_files_returns_empty(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """三层都不存在 → scan 返回空列表。"""
    assert scan_three_tiers(tmp_project_root) == []


def test_scan_three_tiers_finds_all_three(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    user_md = mock_user_baozicode_dir / ".baozicode" / "BaoZiCode.md"
    user_md.parent.mkdir(parents=True)
    user_md.write_text("# user", encoding="utf-8")

    project_local = tmp_project_root / ".baozicode" / "BaoZiCode.md"
    project_local.parent.mkdir(parents=True)
    project_local.write_text("# project_local", encoding="utf-8")

    project_root_md = tmp_project_root / "BaoZiCode.md"
    project_root_md.write_text("# project_root", encoding="utf-8")

    found = scan_three_tiers(tmp_project_root)
    assert found == [user_md, project_local, project_root_md]


def test_load_layer_assigns_correct_source(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    project_local = tmp_project_root / ".baozicode" / "BaoZiCode.md"
    project_local.parent.mkdir(parents=True)
    project_local.write_text("  # project_local  \n", encoding="utf-8")

    layer = load_layer(project_local)
    assert layer.source == "project_local"
    assert layer.path == project_local
    # raw_text 已被 strip
    assert layer.raw_text == "# project_local"


# ---------------------------------------------------------------------------
# 2. bootstrap — 全 0 文件 → banner
# ---------------------------------------------------------------------------


def test_bootstrap_no_files_prints_banner(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """三层都不存在 → bootstrap 打印 stderr banner,返回空 LoadedInstructions。"""
    buf = io.StringIO()
    with redirect_stderr(buf):
        result = bootstrap(tmp_project_root, FakeConfig())  # type: ignore[arg-type]

    assert result.layers == ()
    assert result.concatenated == ""
    assert "未找到 BaoZiCode.md" in buf.getvalue()


# ---------------------------------------------------------------------------
# 3. bootstrap — 单文件 + 三文件拼接顺序
# ---------------------------------------------------------------------------


def test_bootstrap_one_file_concatenates(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    project_root_md = tmp_project_root / "BaoZiCode.md"
    project_root_md.write_text("# Project Rules\n\n- Use type hints", encoding="utf-8")

    result = bootstrap(tmp_project_root, FakeConfig())  # type: ignore[arg-type]
    assert "# Project Rules" in result.concatenated
    assert "Use type hints" in result.concatenated
    # 单文件无 --- 分隔
    assert "---" not in result.concatenated


def test_bootstrap_three_files_concatenate_in_priority_order(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """user_global → project_local → project_root,后写覆盖。

    即拼接串里 user_global 在前,project_root 在后。
    """
    user_md = mock_user_baozicode_dir / ".baozicode" / "BaoZiCode.md"
    user_md.parent.mkdir(parents=True)
    user_md.write_text("USER_LAYER", encoding="utf-8")

    project_local = tmp_project_root / ".baozicode" / "BaoZiCode.md"
    project_local.parent.mkdir(parents=True)
    project_local.write_text("PROJECT_LOCAL_LAYER", encoding="utf-8")

    project_root_md = tmp_project_root / "BaoZiCode.md"
    project_root_md.write_text("PROJECT_ROOT_LAYER", encoding="utf-8")

    result = bootstrap(tmp_project_root, FakeConfig())  # type: ignore[arg-type]

    # 验证顺序:user 出现在 project_local 之前,project_local 出现在 project_root 之前
    idx_user = result.concatenated.find("USER_LAYER")
    idx_local = result.concatenated.find("PROJECT_LOCAL_LAYER")
    idx_root = result.concatenated.find("PROJECT_ROOT_LAYER")
    assert -1 < idx_user < idx_local < idx_root
    # 三段之间用 \n\n---\n\n 隔开
    assert "---" in result.concatenated


# ---------------------------------------------------------------------------
# 4. @include 解析 — 相对路径 / 深度 / 环路 / 路径白名单 / 缺文件
# ---------------------------------------------------------------------------


def test_resolve_includes_relative_path_resolved_from_current_file_parent(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """`@include snippets/a.md` → 基于 current_file.parent/snippets/a.md。"""
    snippets = tmp_project_root / "snippets"
    snippets.mkdir()
    (snippets / "a.md").write_text("INCLUDED_A", encoding="utf-8")

    main = tmp_project_root / "BaoZiCode.md"
    main.write_text("MAIN\n@include snippets/a.md\nEND", encoding="utf-8")

    text, warnings = resolve_includes(
        main.read_text(encoding="utf-8"),
        main,
        tmp_project_root,
    )
    assert warnings == []
    assert "INCLUDED_A" in text
    assert text.startswith("MAIN")
    assert text.endswith("END")


def test_resolve_includes_absolute_path_escapes_is_rejected(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """`@include /etc/passwd` → 跳出 project_root,被白名单拒。"""
    main = tmp_project_root / "BaoZiCode.md"
    main.write_text("@include /etc/passwd", encoding="utf-8")

    text, warnings = resolve_includes(
        main.read_text(encoding="utf-8"),
        main,
        tmp_project_root,
    )
    assert "escapes" in ";".join(warnings) or "path escape" in ";".join(warnings)
    # 原 include 替换为 HTML 注释,正文不应包含 passwd 内容(不会去读)
    assert "passwd" not in text.lower() or "@include skipped" in text


def test_resolve_includes_path_escape_rejected(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """`@include ../outside.md` → 跳出 project_root,被白名单拒。"""
    outside = tmp_project_root.parent / "outside.md"
    outside.write_text("SHOULD_NOT_LOAD", encoding="utf-8")

    main = tmp_project_root / "BaoZiCode.md"
    main.write_text("@include ../outside.md", encoding="utf-8")

    try:
        text, warnings = resolve_includes(
            main.read_text(encoding="utf-8"),
            main,
            tmp_project_root,
        )
        assert any("escapes" in w or "path escape" in w for w in warnings)
        assert "SHOULD_NOT_LOAD" not in text
    finally:
        if outside.exists():
            outside.unlink()


def test_resolve_includes_cycle_detected(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """A → B → A → 检测到 cycle,记 warning 跳过。"""
    a = tmp_project_root / "a.md"
    b = tmp_project_root / "b.md"
    a.write_text("A_START\n@include b.md\nA_END", encoding="utf-8")
    b.write_text("B_START\n@include a.md\nB_END", encoding="utf-8")

    text, warnings = resolve_includes(
        a.read_text(encoding="utf-8"), a, tmp_project_root
    )
    assert any("cycle" in w for w in warnings)
    # A_END 仍出现(主文件内容完整)
    assert "A_END" in text
    # b.md 的内容加载了,但 b.md 试图 include a.md 被 cycle 拒,
    # 留下 <!-- @include skipped (cycle): a.md --> 注释。
    assert "B_END" in text
    assert "@include skipped (cycle): a.md" in text
    # a.md 的内容不会被无限递归(只出现一次)
    assert text.count("A_START") == 1


def test_resolve_includes_depth_limit(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """嵌套 6 层 → 超过 max_depth=5,记 warning,内容不展开。"""
    chain = []
    for i in range(7):
        path = tmp_project_root / f"level_{i}.md"
        chain.append(path)
    for i, path in enumerate(chain):
        if i < len(chain) - 1:
            path.write_text(f"LEVEL_{i}\n@include {chain[i + 1].name}\n", encoding="utf-8")
        else:
            path.write_text(f"LEVEL_{i}_DEEP", encoding="utf-8")

    text, warnings = resolve_includes(
        chain[0].read_text(encoding="utf-8"), chain[0], tmp_project_root,
        max_depth=5,
    )
    assert any("depth" in w for w in warnings)
    # 最后一级(L6)未被加载
    assert "LEVEL_6_DEEP" not in text


def test_resolve_includes_missing_file_warning_other_includes_continue(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """一个 include 缺文件 → warning;另一个 include OK → 仍正常展开。"""
    good = tmp_project_root / "good.md"
    good.write_text("GOOD_CONTENT", encoding="utf-8")

    main = tmp_project_root / "BaoZiCode.md"
    main.write_text(
        "BEFORE\n@include missing.md\nMID\n@include good.md\nAFTER",
        encoding="utf-8",
    )

    text, warnings = resolve_includes(
        main.read_text(encoding="utf-8"), main, tmp_project_root
    )
    assert any("not found" in w or "missing" in w for w in warnings)
    assert "GOOD_CONTENT" in text


# ---------------------------------------------------------------------------
# 5. CLAUDE.md 不被读取(只读 BaoZiCode.md)
# ---------------------------------------------------------------------------


def test_loader_does_not_read_existing_claudemd(
    tmp_project_root: Path, mock_user_baozicode_dir: Path
) -> None:
    """存在 CLAUDE.md 但无 BaoZiCode.md → bootstrap 仍视为无文件,打印 banner。"""
    claudemd = tmp_project_root / "CLAUDE.md"
    claudemd.write_text("# For Claude Code", encoding="utf-8")

    buf = io.StringIO()
    with redirect_stderr(buf):
        result = bootstrap(tmp_project_root, FakeConfig())  # type: ignore[arg-type]

    # CLAUDE.md 不应被加载
    assert "For Claude Code" not in result.concatenated
    # 仍触发 banner(因为没找到 BaoZiCode.md)
    assert "未找到 BaoZiCode.md" in buf.getvalue()
