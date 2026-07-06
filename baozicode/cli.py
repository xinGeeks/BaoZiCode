"""CLI 入口：解析参数 → 加载配置 → 启动 TUI。"""

from __future__ import annotations

import argparse
import sys

from baozicode.app import BaoZiCodeApp
from baozicode.config.loader import ConfigError, load_config


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

    app = BaoZiCodeApp(config)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
