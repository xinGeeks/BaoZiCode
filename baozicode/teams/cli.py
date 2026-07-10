"""v1.4 Team Foundation — Team lifecycle CLI 子命令。

公开 API:

- `add_subcommand(subparsers)` — 把 `team` 二级子命令挂到顶层 argparse
- `main(argv=None)` — 独立跑入口(`python -m baozicode.teams.cli`)

子命令结构(`team <action> [...]`):

- `create <name>` — 建新 team(`--lead` / `--scope`)
- `list` — 列所有 team(`--scope`)
- `show <name>` — 打印 pretty JSON(`--scope`)
- `use <name>` — 激活 team;foundation 阶段仅打印 "已激活" 占位
  (后续 v1-4-team-tools / team-coordinator proposal 实现完整 dispatch)
- `destroy <name>` — 删 team(`--yes/-y` 跳交互 / `--force` 容错目录不在)

全局 flag(`team` 这一层):

- `--config / -c PATH` — 配置文件(默认走 bootstrap 查找顺序)
- `--teams-dir PATH` — 覆盖 `teams.dir`(应急 / 测试用)

退出码:

- 0 = success
- 1 = 通用错误(参数解析失败 / 用户拒删等)
- 2 = team 名不合法(`TeamName*`)
- 3 = team / member 不存在(`TeamNotFound` / `MemberNotFound`)
- 4 = 权限 / IO 错(`PermissionError` / `OSError` / `FileExistsError`)
- 5 = 配置错误(`ConfigError`)

错误输出格式(stderr):

    Error: <EnumClass>: <detail>

设计保证:

- Foundation 阶段 `--scope` 是占位 — 总是 user-global(`TeamsConfig.dir`),
  后续 proposal 才会扩展 project scope。这样 LLM 写 `team create --scope
  project foo` 不会报错,只是行为等同 `--scope user`。
- `destroy` 默认走 stdin 确认(`Destroy team 'x'? [y/N]`),`--yes` 跳过。
  这跟 v1.3 worktree 的 `--yes` 风格一致。
- `list` 空 → `(no teams)`,让 LLM / 脚本能区分"目录不存在" vs "team
  还没创建"。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from baozicode.config.loader import ConfigError, load_config

from .registry import TeamsRegistry
from .schema import (
    TeamAlreadyExists,
    TeamNameInvalid,
    TeamNotFound,
)


# ---------------------------------------------------------------------------
# 退出码常量
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_NAME_INVALID = 2
EXIT_NOT_FOUND = 3
EXIT_IO = 4
EXIT_CONFIG = 5


# ---------------------------------------------------------------------------
# Registry 构造(统一处理 config / --teams-dir / 默认 dir)
# ---------------------------------------------------------------------------


def _build_registry(args: argparse.Namespace) -> TeamsRegistry:
    """从 args 构造 TeamsRegistry。

    优先顺序:
    1. `--teams-dir`(CLI 显式覆盖,用于测试 + 应急)
    2. `config.teams.dir`(走 bootstrap)
    3. 默认 `~/.config/baozicode/teams/`

    Raises:
        SystemExit: 配置加载失败时(用 EXIT_CONFIG 退出)
    """
    teams_dir_raw = getattr(args, "teams_dir", None)
    if teams_dir_raw:
        teams_dir = Path(teams_dir_raw).expanduser()
        teams_dir.mkdir(parents=True, exist_ok=True)
        return TeamsRegistry(teams_dir)

    try:
        config = load_config(getattr(args, "config", None))
    except ConfigError as exc:
        print(f"Error: ConfigError: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG)
    return TeamsRegistry.bootstrap(config)


# ---------------------------------------------------------------------------
# 子命令注册
# ---------------------------------------------------------------------------


def add_subcommand(subparsers: argparse._SubParsersAction) -> None:
    """注册 `team` 子命令到顶层 argparse。

    顶层结构::

        baozicode [team] [--config C] [--teams-dir D] <action> [...]

    Args:
        subparsers: 顶层 `parser.add_subparsers(...)` 返回的 _SubParsersAction
    """
    team_parser = subparsers.add_parser(
        "team",
        help="Team lifecycle management (create / list / show / use / destroy)",
        description="管理 BaoZiCode team lifecycle",
    )
    team_parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="配置文件路径(默认依次查找 ./config.yaml、~/.config/baozicode/config.yaml)",
    )
    team_parser.add_argument(
        "--teams-dir",
        type=str,
        default=None,
        help="覆盖 teams.dir(应急 / 测试用)",
    )

    action_subs = team_parser.add_subparsers(
        dest="team_action",
        required=True,
        metavar="<action>",
    )

    # create
    p_create = action_subs.add_parser(
        "create", help="创建新 team(原子建目录 + 写 team.json)"
    )
    p_create.add_argument("name", type=str, help="team 名(2-30 字符,[a-z0-9-])")
    p_create.add_argument(
        "--lead", type=str, default="lead", help="Lead 名(默认 'lead')"
    )
    p_create.add_argument(
        "--scope",
        choices=["user", "project"],
        default="user",
        help="scope(foundation 总是 user-global,占位参数)",
    )

    # list
    p_list = action_subs.add_parser("list", help="列出所有 team(字典序)")
    p_list.add_argument(
        "--scope",
        choices=["user", "project"],
        default="user",
        help="scope(foundation 总是 user-global,占位参数)",
    )

    # show
    p_show = action_subs.add_parser("show", help="展示 team 详情(pretty JSON)")
    p_show.add_argument("name", type=str, help="team 名")
    p_show.add_argument(
        "--scope",
        choices=["user", "project"],
        default="user",
        help="scope(foundation 总是 user-global,占位参数)",
    )

    # use
    p_use = action_subs.add_parser(
        "use",
        help="激活 team(foundation 仅打印占位,完整 dispatch 见后续 proposal)",
    )
    p_use.add_argument("name", type=str, help="team 名")
    p_use.add_argument(
        "--scope",
        choices=["user", "project"],
        default="user",
        help="scope(foundation 总是 user-global,占位参数)",
    )

    # destroy
    p_destroy = action_subs.add_parser("destroy", help="删除 team(rmtree)")
    p_destroy.add_argument("name", type=str, help="team 名")
    p_destroy.add_argument(
        "--scope",
        choices=["user", "project"],
        default="user",
        help="scope(foundation 总是 user-global,占位参数)",
    )
    p_destroy.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="跳过交互确认(等价于 confirm=True)",
    )
    p_destroy.add_argument(
        "--force",
        action="store_true",
        help="强制删除(目录不在也跳过,exit 0)",
    )


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def _cmd_create(args: argparse.Namespace) -> int:
    reg = _build_registry(args)
    try:
        store = reg.create_team(args.name, lead=args.lead)
    except TeamNameInvalid as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_NAME_INVALID
    except TeamAlreadyExists as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_IO
    except (OSError, PermissionError) as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_IO

    print(f"Created team {args.name!r} at {store.team_dir}")
    return EXIT_OK


def _cmd_list(args: argparse.Namespace) -> int:
    reg = _build_registry(args)
    names = reg.list_teams()
    if not names:
        print("(no teams)")
        return EXIT_OK
    for n in names:
        print(n)
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    reg = _build_registry(args)
    store = reg.get(args.name)
    if store is None:
        print(f"Error: TeamNotFound: team {args.name!r} 不存在", file=sys.stderr)
        return EXIT_NOT_FOUND
    team = store.show()
    print(team.to_json(indent=2))
    return EXIT_OK


def _cmd_use(args: argparse.Namespace) -> int:
    reg = _build_registry(args)
    store = reg.get(args.name)
    if store is None:
        print(f"Error: TeamNotFound: team {args.name!r} 不存在", file=sys.stderr)
        return EXIT_NOT_FOUND
    member_count = len(store.list_members())
    print(
        f"Activated team {args.name!r} ({member_count} members, "
        f"lead={store.show().lead})"
    )
    print(
        "Note: foundation 仅打印激活信息;"
        "v1-4-team-tools proposal 提供完整 dispatch。",
        file=sys.stderr,
    )
    return EXIT_OK


def _cmd_destroy(args: argparse.Namespace) -> int:
    reg = _build_registry(args)

    # 默认走 stdin 确认;--yes 跳过
    if not args.yes:
        try:
            ans = input(f"Destroy team {args.name!r}? [y/N]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "y":
            print("Aborted.", file=sys.stderr)
            return EXIT_GENERIC

    try:
        reg.delete_team(args.name, confirm=True)
    except TeamNotFound as exc:
        if args.force:
            print(f"Warn: {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_OK
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    except PermissionError as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_IO
    except OSError as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_IO

    print(f"Destroyed team {args.name!r}")
    return EXIT_OK


_DISPATCH: dict[str, Callable[[argparse.Namespace], int]] = {
    "create": _cmd_create,
    "list": _cmd_list,
    "show": _cmd_show,
    "use": _cmd_use,
    "destroy": _cmd_destroy,
}


# ---------------------------------------------------------------------------
# 独立跑入口
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 独立跑入口(`python -m baozicode.teams.cli ...`)。

    Args:
        argv: 参数列表;None 走 `sys.argv[1:]`

    Returns:
        退出码(见模块顶部 EXIT_* 常量)
    """
    parser = argparse.ArgumentParser(
        prog="baozicode-team",
        description="BaoZiCode team lifecycle CLI",
    )
    top_subs = parser.add_subparsers(
        dest="_top_action", required=True, metavar="<action>"
    )
    # 复用 add_subcommand 的注册逻辑 — 但加一层占位让 add_subcommand 不冲突
    # add_subcommand 期望的 subparsers dest 是 team_action;
    # 我们顶层 dest 是 _top_action,然后 `team` 子命令注册时 team_parser 自己
    # 会再 add_subparsers(dest="team_action")。
    # 直接调用 add_subcommand(top_subs) 即可。
    add_subcommand(top_subs)

    args = parser.parse_args(argv)

    # 顶层 subparser 把 dest="_top_action" 设为 "team";
    # 我们再从 args.team_action 拿二级 action。
    handler = _DISPATCH.get(args.team_action)
    if handler is None:
        parser.print_help()
        return EXIT_GENERIC
    return handler(args)


__all__ = [
    "add_subcommand",
    "main",
    "EXIT_OK",
    "EXIT_GENERIC",
    "EXIT_NAME_INVALID",
    "EXIT_NOT_FOUND",
    "EXIT_IO",
    "EXIT_CONFIG",
]


if __name__ == "__main__":
    raise SystemExit(main())