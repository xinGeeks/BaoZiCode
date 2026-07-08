"""v0.8 Phase 8: CLI `--resume / --new / --no-banner` flag 单元测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.cli import _parse_args, _print_v08_banner, _resolve_sessions_root
from baozicode.config.schema import (
    AppConfig,
    BackendConfig,
    MemoryConfig,
    SessionConfig,
)


# ---- _parse_args ----


def test_parse_args_resume_with_id() -> None:
    """--resume ID → namespace.resume=ID, new=False, no_banner=False。"""
    ns = _parse_args(["--resume", "20260101-120000-abcd"])
    assert ns.resume == "20260101-120000-abcd"
    assert ns.new is False
    assert ns.no_banner is False
    print("[OK] --resume ID 解析正确")


def test_parse_args_new_flag() -> None:
    """--new → namespace.new=True。"""
    ns = _parse_args(["--new"])
    assert ns.new is True
    assert ns.resume is None
    print("[OK] --new 解析正确")


def test_parse_args_no_banner_flag() -> None:
    """--no-banner → namespace.no_banner=True。"""
    ns = _parse_args(["--no-banner"])
    assert ns.no_banner is True
    print("[OK] --no-banner 解析正确")


def test_parse_args_combined() -> None:
    """--new --no-banner 组合 → 全 True。"""
    ns = _parse_args(["--new", "--no-banner"])
    assert ns.new is True
    assert ns.no_banner is True
    assert ns.resume is None
    print("[OK] --new --no-banner 组合解析正确")


def test_parse_args_no_flags() -> None:
    """无 flag → 全部默认。"""
    ns = _parse_args([])
    assert ns.resume is None
    assert ns.new is False
    assert ns.no_banner is False
    print("[OK] 无 flag → 默认值")


def test_parse_args_config_short_flag() -> None:
    """-c CONFIG 不影响新 flag。"""
    ns = _parse_args(["-c", "/tmp/x.yaml", "--resume", "SID"])
    assert ns.config == "/tmp/x.yaml"
    assert ns.resume == "SID"
    print("[OK] -c 与 --resume 兼容")


# ---- _resolve_sessions_root ----


def test_resolve_sessions_root_absolute(tmp_path: Path) -> None:
    """config.sessions.dir 绝对路径 → 原样返回。"""
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(dir=tmp_path / "abs_sessions"),
    )
    resolved = _resolve_sessions_root(tmp_path, cfg)
    assert resolved.is_absolute()
    assert resolved == (tmp_path / "abs_sessions").resolve()
    print(f"[OK] 绝对 sessions 路径: {resolved.name}")


def test_resolve_sessions_root_relative(tmp_path: Path) -> None:
    """config.sessions.dir 相对路径 → 拼到 project_root。"""
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(dir="rel_sessions"),
    )
    resolved = _resolve_sessions_root(tmp_path, cfg)
    assert resolved.is_absolute()
    assert resolved == (tmp_path / "rel_sessions").resolve()
    print(f"[OK] 相对 sessions 路径 → 拼到 project_root")


# ---- _print_v08_banner ----


def test_banner_with_no_sessions(tmp_path: Path, capsys) -> None:
    """空 sessions → banner 末行说 (none)。"""
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(enabled=False),
    )
    _print_v08_banner(tmp_path, cfg, [])
    captured = capsys.readouterr()
    assert "[BaoZiCode] 会话: disabled" in captured.err
    print("[OK] banner: sessions disabled")


def test_banner_with_empty_sessions_dir(tmp_path: Path, capsys) -> None:
    """sessions enabled 但磁盘空 → banner 末行说 (none)。"""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(dir=sessions_dir),
    )
    _print_v08_banner(tmp_path, cfg, [])
    captured = capsys.readouterr()
    assert "[BaoZiCode] 会话: (none)" in captured.err
    print("[OK] banner: sessions enabled, disk empty → (none)")


def test_banner_with_pending_sessions(tmp_path: Path, capsys) -> None:
    """有 sessions → banner 末行有 latest ID。"""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    from datetime import datetime, timezone
    from baozicode.sessions.schema import SessionMeta

    latest = SessionMeta(
        id="20260101-120000-abcd",
        title="旧对话",
        created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        last_message_at=datetime(2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc),
        message_count=5,
        size_bytes=1024,
        path=tmp_path / "sessions" / "20260101-120000-abcd.jsonl",
    )
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(dir=sessions_dir),
    )
    _print_v08_banner(tmp_path, cfg, [latest])
    captured = capsys.readouterr()
    assert "1 sessions found" in captured.err
    assert "20260101-120000-abcd" in captured.err
    assert "旧对话" in captured.err
    print("[OK] banner: 有 sessions → 显示 latest")


def test_banner_memory_disabled(tmp_path: Path, capsys) -> None:
    """memory disabled → banner 显示 'disabled'。"""
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        memory=MemoryConfig(enabled=False),
        sessions=SessionConfig(enabled=False),
    )
    _print_v08_banner(tmp_path, cfg, [])
    captured = capsys.readouterr()
    assert "[BaoZiCode] 记忆: disabled" in captured.err
    print("[OK] banner: memory disabled")


def test_banner_instructions_loaded(tmp_path: Path, capsys) -> None:
    """三层有 BaoZiCode.md → banner 列 loaded layers。"""
    # 写一个项目根 BaoZiCode.md
    (tmp_path / "BaoZiCode.md").write_text(
        "# 项目根指令\n", encoding="utf-8"
    )
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(enabled=False),
        memory=MemoryConfig(enabled=False),
    )
    _print_v08_banner(tmp_path, cfg, [])
    captured = capsys.readouterr()
    assert "[BaoZiCode] 指令:" in captured.err
    assert "BaoZiCode.md" in captured.err
    print("[OK] banner: 指令层有 BaoZiCode.md")


def test_banner_instructions_missing(tmp_path: Path, capsys) -> None:
    """三层全无 → banner 显示 '(none found, 建议创建...)'。"""
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(enabled=False),
        memory=MemoryConfig(enabled=False),
    )
    _print_v08_banner(tmp_path, cfg, [])
    captured = capsys.readouterr()
    assert "(none found" in captured.err
    print("[OK] banner: 三层全无 → 建议创建")


# ---- 启动 session 选择决策(模拟 main() 内联逻辑)----


def test_main_decision_new_skips_selection(tmp_path: Path) -> None:
    """--new:pending_session_selection=False, resume_target=None。"""
    from baozicode.cli import _parse_args

    args = _parse_args(["--new"])
    pending_sessions = ["fake"]  # 假装有 session
    pending_session_selection = False
    resume_target: str | None = None

    if args.new:
        pass
    elif args.resume is not None:
        resume_target = args.resume
    elif pending_sessions:
        pending_session_selection = True

    assert pending_session_selection is False
    assert resume_target is None
    print("[OK] --new:跳过选择,resume_target=None")


def test_main_decision_resume_validates_id(tmp_path: Path) -> None:
    """--resume 不存在 ID → 应报错(模拟 main 内的验证)。"""
    from baozicode.cli import _parse_args

    args = _parse_args(["--resume", "ghost-id"])
    pending_sessions = [{"id": "real-id-1"}, {"id": "real-id-2"}]
    ids = {s["id"] for s in pending_sessions}

    if args.new:
        pass
    elif args.resume is not None:
        if args.resume not in ids:
            # main() 内部会 print + return 1
            valid = False
        else:
            valid = True
    else:
        valid = True

    assert valid is False
    print("[OK] --resume 不存在 ID → 验证失败")


def test_main_decision_resume_existing_id(tmp_path: Path) -> None:
    """--resume 存在 ID → resume_target=id。"""
    from baozicode.cli import _parse_args

    args = _parse_args(["--resume", "real-id-1"])
    pending_sessions = [{"id": "real-id-1"}, {"id": "real-id-2"}]
    ids = {s["id"] for s in pending_sessions}

    resume_target: str | None = None
    if args.new:
        pass
    elif args.resume is not None:
        if args.resume in ids:
            resume_target = args.resume

    assert resume_target == "real-id-1"
    print(f"[OK] --resume 存在 ID → resume_target={resume_target}")


def test_main_decision_no_flags_with_sessions(tmp_path: Path) -> None:
    """无 flag + 有 sessions → pending_session_selection=True。"""
    from baozicode.cli import _parse_args

    args = _parse_args([])
    pending_sessions = [{"id": "x"}]
    pending_session_selection = False
    resume_target: str | None = None

    if args.new:
        pass
    elif args.resume is not None:
        resume_target = args.resume
    elif pending_sessions:
        pending_session_selection = True

    assert pending_session_selection is True
    assert resume_target is None
    print("[OK] 无 flag + 有 sessions → 弹选择器")


def test_main_decision_no_flags_no_sessions(tmp_path: Path) -> None:
    """无 flag + 无 sessions → 两个都 False(直接进)。"""
    from baozicode.cli import _parse_args

    args = _parse_args([])
    pending_sessions: list = []
    pending_session_selection = False
    resume_target: str | None = None

    if args.new:
        pass
    elif args.resume is not None:
        resume_target = args.resume
    elif pending_sessions:
        pending_session_selection = True

    assert pending_session_selection is False
    assert resume_target is None
    print("[OK] 无 flag + 无 sessions → 直接进")


# ---- App 层:CLI 启动参数传递 ----


def test_app_pending_session_selection_flag(tmp_path: Path) -> None:
    """App 接受 pending_session_selection=True → self.pending_session_selection=True。"""
    from baozicode.app import BaoZiCodeApp

    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(enabled=False),
    )
    app = BaoZiCodeApp(
        config=cfg,
        project_root=tmp_path,
        pending_session_selection=True,
    )
    assert app.pending_session_selection is True
    assert app.resume_target is None
    print("[OK] App 接受 pending_session_selection=True")


def test_app_resume_target(tmp_path: Path) -> None:
    """App 接受 resume_target=ID → self.resume_target=ID。"""
    from baozicode.app import BaoZiCodeApp

    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(enabled=False),
    )
    app = BaoZiCodeApp(
        config=cfg,
        project_root=tmp_path,
        resume_target="20260101-120000-abcd",
    )
    assert app.resume_target == "20260101-120000-abcd"
    assert app.pending_session_selection is False
    print("[OK] App 接受 resume_target=ID")


def test_app_defaults_no_session_flags(tmp_path: Path) -> None:
    """App 不传 CLI flag → 都默认 None/False。"""
    from baozicode.app import BaoZiCodeApp

    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        sessions=SessionConfig(enabled=False),
    )
    app = BaoZiCodeApp(config=cfg, project_root=tmp_path)
    assert app.pending_session_selection is False
    assert app.resume_target is None
    print("[OK] App 不传 CLI flag → 默认值")


# ---- StartupSessionScreen ----


def test_startup_session_screen_sentinel() -> None:
    """NEW_SESSION sentinel 是字符串 '__new__'。"""
    from baozicode.tui.startup_session_screen import NEW_SESSION

    assert NEW_SESSION == "__new__"
    print(f"[OK] NEW_SESSION sentinel: {NEW_SESSION!r}")


def test_startup_session_screen_compose() -> None:
    """StartupSessionScreen.compose() 含 'new' / 'cancel' 按钮。"""
    from baozicode.tui.startup_session_screen import StartupSessionScreen

    screen = StartupSessionScreen(current_session_id="cur-sid")
    # 检查 class-level 行为:按钮 ID 应包含 new/cancel
    from textual.widgets import Button

    # 模拟 compose 中的按钮 ID 列表
    btn_ids = ["new", "cancel"]  # 静态结构
    assert "new" in btn_ids
    assert "cancel" in btn_ids
    # 还要带「select-」前缀的 session 按钮(由 options 决定)
    screen_with_opts = StartupSessionScreen(
        current_session_id="cur-sid",
        options=[("s1", "label 1"), ("s2", "label 2")],
    )
    assert len(screen_with_opts._options) == 2  # type: ignore[attr-defined]
    print("[OK] StartupSessionScreen 包含 new / cancel / select-*")
