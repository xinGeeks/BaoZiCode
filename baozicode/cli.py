"""CLI 入口：解析参数 → 加载配置 → 启动 TUI。"""

from __future__ import annotations

import argparse
import asyncio
import sys

from baozicode.app import BaoZiCodeApp
from baozicode.config.loader import ConfigError, load_config
from baozicode.mcp import bootstrap as mcp_bootstrap


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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
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

    app = BaoZiCodeApp(config, mcp_manager=mcp_manager)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
