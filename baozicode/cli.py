"""CLI 入口：解析参数 → 加载配置 → 启动 TUI。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from baozicode.app import BaoZiCodeApp
from baozicode.config.loader import ConfigError, load_config
from baozicode.mcp import bootstrap as mcp_bootstrap
from baozicode.sessions import list_sessions


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="baozicode",
        description="BaoZiCode - 命令行 AI 编码助手",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="配置文件路径（默认依次查找 ./config.yaml、~/.config/baozicode/config.yaml）",
    )
    # v0.8:启动 session 选择
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        metavar="SESSION_ID",
        help="直接恢复指定 session(YYYYMMDD-HHMMSS-xxxx),跳过启动选择器",
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="强制开新 session(即使磁盘上有旧的)",
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="抑制启动 banner(指令 / 记忆 / 会话摘要)",
    )
    # v1.4:顶层子命令分发(`team` + `member`);无子命令 → 走 TUI 启动老路径
    sub = parser.add_subparsers(dest="top_command", metavar="<command>")
    from baozicode.teams.cli import add_subcommand, add_member_subcommand
    add_subcommand(sub)
    add_member_subcommand(sub)
    return parser.parse_args(argv)


def _print_mcp_banner(manager) -> None:
    """启动前打印 MCP 状态摘要(banner 风格)。

    失败 server 一行一条警告;connected/failed 都打印在 stderr,
    TUI 在 mounted 后还能用 /mcp slash 命令查完整状态。
    """
    states = manager.states
    if not states:
        return
    connected = sum(1 for s in states.values() if s.status == "connected")
    failed = sum(1 for s in states.values() if s.status == "failed")
    print(
        f"[mcp] servers: {len(states)} configured, "
        f"{connected} connected, {failed} failed",
        file=sys.stderr,
    )
    for name, state in states.items():
        if state.status != "connected":
            print(
                f"[mcp]   ! {name}: {state.status} — {state.error}",
                file=sys.stderr,
            )
        else:
            tool_names = ", ".join(t.name for t in state.tools)
            print(
                f"[mcp]   + {name}: {len(state.tools)} tools [{tool_names}]",
                file=sys.stderr,
            )


def _print_v08_banner(
    project_root: Path,
    config,
    pending_sessions: list,
) -> None:
    """v0.8 启动 banner — 指令 / 记忆 / 会话摘要,各一行 stderr。"""
    # 1. 指令
    try:
        from baozicode.instructions import bootstrap as instructions_bootstrap
        loaded = instructions_bootstrap(project_root, config)
        layer_names = [layer.path.name for layer in loaded.layers]
        if layer_names:
            print(
                f"[BaoZiCode] 指令: {len(layer_names)} layers loaded ({' + '.join(layer_names)})",
                file=sys.stderr,
            )
        else:
            print(
                "[BaoZiCode] 指令: (none found, 建议创建项目根目录的 BaoZiCode.md)",
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001
        pass

    # 2. 记忆
    if config.memory.enabled:
        try:
            from baozicode.memory import bootstrap as mem_bootstrap
            user_store, project_store = mem_bootstrap(project_root, config)
            u_idx = user_store.read_index()
            p_idx = project_store.read_index()
            u_count = len(u_idx.entries)
            p_count = len(p_idx.entries)
            # 状态判断
            from baozicode.memory.overflow import MemoryOverflowHandler
            state = MemoryOverflowHandler._classify(  # type: ignore[attr-defined]
                u_idx.total_lines + p_idx.total_lines,
                u_idx.total_bytes + p_idx.total_bytes,
            )
            print(
                f"[BaoZiCode] 记忆: {u_count + p_count} notes "
                f"(user: {u_count}, project: {p_count}), "
                f"index: {u_idx.total_lines + p_idx.total_lines} lines / "
                f"{u_idx.total_bytes + p_idx.total_bytes} bytes "
                f"(state: {state.name})",
                file=sys.stderr,
            )
        except Exception:  # noqa: BLE001
            print("[BaoZiCode] 记忆: (读取失败)", file=sys.stderr)
    else:
        print("[BaoZiCode] 记忆: disabled", file=sys.stderr)

    # 3. 会话
    if config.sessions.enabled:
        if pending_sessions:
            latest = pending_sessions[0]
            title = (latest.title or "(无标题)")[:30]
            print(
                f"[BaoZiCode] 会话: {len(pending_sessions)} sessions found, "
                f"latest: {latest.id} ({title})",
                file=sys.stderr,
            )
        else:
            print("[BaoZiCode] 会话: (none)", file=sys.stderr)
    else:
        print("[BaoZiCode] 会话: disabled", file=sys.stderr)


def _resolve_sessions_root(project_root: Path, config) -> Path:
    """解出 sessions 目录的绝对路径(与 app._resolve_sessions_root 同逻辑)。"""
    sessions_dir = Path(config.sessions.dir)
    if not sessions_dir.is_absolute():
        sessions_dir = project_root / sessions_dir
    return sessions_dir


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # v1.4:顶层子命令分发(`team` + `member`);无 top_command → TUI 启动老路径
    if getattr(args, "top_command", None) == "team":
        from baozicode.teams import cli as teams_cli

        # argv 是 [prog_name, "team", <flags>, <action>, ...];
        # teams_cli.main 自己有 argparse,期望从 `["team", ...]` 开始
        # 解析。把 prog_name 去掉,再在最前面加 "team"。
        raw = argv[1:] if argv is not None else sys.argv[1:]
        return teams_cli.main(["team", *raw])

    if getattr(args, "top_command", None) == "member":
        from baozicode.teams.cli import main_member_run

        # `member run` 是 async 入口;`add_member_subcommand` 已把
        # `--team` / `--name` / `--cwd` 解析到 args 上。
        # member_action 由 argparse required=True 兜底,这里只会
        # 在 member_action="run" 时到达。
        return asyncio.run(main_member_run(args))

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"启动失败: {exc}", file=sys.stderr)
        return 1

    # v0.6:同步 bootstrap MCP,banner 提示后再进 TUI(失败不阻塞)
    mcp_manager = None
    if config.mcp_servers:
        try:
            mcp_manager = asyncio.run(mcp_bootstrap(config))
            _print_mcp_banner(mcp_manager)
        except Exception as exc:  # noqa: BLE001
            print(f"[mcp] bootstrap failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            mcp_manager = None

    # v0.8:解析项目根、列已有 sessions、决定启动行为
    project_root = Path.cwd().resolve()
    pending_sessions: list = []
    sessions_root: Path | None = None
    if config.sessions.enabled:
        sessions_root = _resolve_sessions_root(project_root, config)
        try:
            pending_sessions = list_sessions(sessions_root)
        except Exception:  # noqa: BLE001
            pending_sessions = []

    # ---- 启动 session 选择决策 ----
    pending_session_selection = False
    resume_target: str | None = None

    if args.new:
        # --new:显式开新 session,跳过所有选择
        pass
    elif args.resume is not None:
        # --resume ID:验证 ID 存在,不存在则报错退出
        ids = {m.id for m in pending_sessions}
        if args.resume not in ids:
            print(
                f"[BaoZiCode] --resume: 找不到 session {args.resume!r}。\n"
                f"  已有 sessions: {', '.join(sorted(ids)) or '(none)'}",
                file=sys.stderr,
            )
            return 1
        resume_target = args.resume
    elif pending_sessions:
        # 无 flag 且磁盘有 sessions → on_mount 弹选择器
        pending_session_selection = True

    # ---- 打印 v0.8 banner(除非 --no-banner)----
    if not args.no_banner:
        _print_v08_banner(project_root, config, pending_sessions)

    app = BaoZiCodeApp(
        config,
        mcp_manager=mcp_manager,
        pending_session_selection=pending_session_selection,
        resume_target=resume_target,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
