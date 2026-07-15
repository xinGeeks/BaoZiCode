"""v1.4 Team Foundation — Team lifecycle CLI 子命令。

公开 API:

- `add_subcommand(subparsers)` — 把 `team` 二级子命令挂到顶层 argparse
- `add_member_subcommand(subparsers)` — 把 `member` 二级子命令挂到顶层 argparse
- `main(argv=None)` — 独立跑入口(`python -m baozicode.teams.cli`)
- `main_member_run(args)` — async entry for `member run`

子命令结构(`team <action> [...]`):

- `create <name>` — 建新 team(`--lead` / `--scope`)
- `list` — 列所有 team(`--scope`)
- `show <name>` — 打印 pretty JSON(`--scope`)
- `use <name>` — 激活 team;foundation 阶段仅打印 "已激活" 占位
  (后续 v1-4-team-tools / team-coordinator proposal 实现完整 dispatch)
- `destroy <name>` — 删 team(`--yes/-y` 跳交互 / `--force` 容错目录不在)

子命令结构(`member run [...]`):

- `run --team <name> --name <member> [--cwd PATH]` — 起 member 长生命周期
  polling 进程(`MemberMainLoop`);SIGINT/SIGTERM → graceful terminate

全局 flag(`team` / `member` 这一层):

- `--config / -c PATH` — 配置文件(默认走 bootstrap 查找顺序)
- `--teams-dir PATH` — 覆盖 `teams.dir`(应急 / 测试用)

退出码:

- 0 = success
- 1 = 通用错误(参数解析失败 / 用户拒删等)
- 2 = team 名不合法(`TeamName*`)
- 3 = team 不存在(`TeamNotFound`)
- 4 = 权限 / IO 错(`PermissionError` / `OSError` / `FileExistsError`)
- 5 = 配置错误(`ConfigError`)
- 6 = member 不存在(`MemberNotFound`)

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
- `member run` 是 v1.4-pane-backend 阶段新增的 Member 进程入口;
  派生路径(coroutine / tmux / iTerm2 / WT)由 BackendManager.spawn_if_offline
  决定,CLI 这层只负责启动 polling loop。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from baozicode.config.loader import ConfigError, load_config
from baozicode.llm.factory import create_client
from baozicode.permissions import bootstrap as perm_bootstrap

from .member_loop import MemberMainLoop
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
EXIT_MEMBER_NOT_FOUND = 6


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


# ---------------------------------------------------------------------------
# `member run` 子命令(异步入口)
# ---------------------------------------------------------------------------


def add_member_subcommand(subparsers: argparse._SubParsersAction) -> None:
    """注册 `member` 子命令到顶层 argparse。

    结构::

        baozicode [member] [--config C] [--teams-dir D] run \
            --team <team> --name <member> [--cwd <path>]

    Args:
        subparsers: 顶层 `parser.add_subparsers(...)` 返回的 _SubParsersAction
    """
    member_parser = subparsers.add_parser(
        "member",
        help="Member runtime (run a member process in the foreground)",
        description="启动 Member 长生命周期 polling 进程;常驻前台。",
    )
    member_parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="配置文件路径(默认走 bootstrap 查找顺序)",
    )
    member_parser.add_argument(
        "--teams-dir",
        type=str,
        default=None,
        help="覆盖 teams.dir(应急 / 测试用)",
    )

    member_subs = member_parser.add_subparsers(
        dest="member_action",
        required=True,
        metavar="<action>",
    )

    # run
    p_run = member_subs.add_parser(
        "run",
        help="起 Member 进程(`MemberMainLoop.run()`);SIGINT/SIGTERM → graceful terminate",
    )
    p_run.add_argument(
        "--team",
        required=True,
        type=str,
        help="team 名(`team list` 看)",
    )
    p_run.add_argument(
        "--name",
        required=True,
        type=str,
        help="member 名(team.json `members.<name>`)",
    )
    p_run.add_argument(
        "--cwd",
        type=str,
        default=None,
        help="覆盖 member.workdir(默认走 team.json 字段)",
    )


async def main_member_run(args: argparse.Namespace) -> int:
    """`baozicode member run --team T --name M` 的 async 入口。

    流程:
      1. bootstrap registry(team 不存在 → exit 3)
      2. `team = store.show()`;member 不存在 → exit 6
      3. chdir 到 `--cwd` 或 `member.workdir`(不存在则 mkdir)
      4. bootstrap LLM client + permissions
      5. `loop = MemberMainLoop(...)`
      6. 注册 SIGINT/SIGTERM handler → `loop.request_terminate()`
      7. `await loop.run()`(graceful exit 后写 state=offline)

    Returns:
        退出码
    """
    import asyncio as _asyncio
    import os as _os
    import signal as _signal

    registry = _build_registry(args)
    store = registry.get(args.team)
    if store is None:
        print(
            f"Error: TeamNotFound: team {args.team!r} 不存在",
            file=sys.stderr,
        )
        return EXIT_NOT_FOUND
    team = store.show()
    if args.name not in team.members:
        print(
            f"Error: MemberNotFound: member {args.name!r} 在 team "
            f"{args.team!r} 中不存在",
            file=sys.stderr,
        )
        return EXIT_MEMBER_NOT_FOUND
    member = team.members[args.name]

    # 3) chdir
    cwd_raw = getattr(args, "cwd", None) or member.workdir or f".worktrees/{args.name}/"
    cwd = Path(cwd_raw).expanduser()
    if not cwd.is_absolute():
        cwd = Path.cwd() / cwd
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        _os.chdir(cwd)
    except OSError as exc:
        print(
            f"Error: OSError: chdir {cwd} 失败: {exc}",
            file=sys.stderr,
        )
        return EXIT_IO

    # 4) bootstrap config + LLM + permissions
    try:
        config = load_config(getattr(args, "config", None))
    except ConfigError as exc:
        print(f"Error: ConfigError: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        llm_client = create_client(config)
    except Exception as exc:  # noqa: BLE001
        print(
            f"Error: LLMClientError: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    permissions = perm_bootstrap(cwd, config)

    # 5) MemberMainLoop
    loop = MemberMainLoop(
        registry,
        args.team,
        args.name,
        llm_client=llm_client,
        config=config,
        permissions=permissions,
        project_root=cwd,
    )

    # 6) signal handler — graceful terminate
    # 用 `loop.add_signal_handler`(Unix 优先),Windows fallback 到
    # `signal.signal`。两者都装,asyncio 不会冲突。
    running_loop = _asyncio.get_running_loop()
    _sigterm_handler = lambda: loop.request_terminate()  # noqa: E731
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(_signal, sig_name, None)
        if sig is None:
            continue
        try:
            running_loop.add_signal_handler(sig, _sigterm_handler)
        except (NotImplementedError, RuntimeError):
            # Windows:fallback 到 signal.signal
            _signal.signal(sig, lambda *_: _sigterm_handler())

    # 7) run loop
    try:
        await loop.run()
    except _asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        loop.request_terminate()
    return EXIT_OK


__all__ = [
    "add_subcommand",
    "add_member_subcommand",
    "main",
    "main_member_run",
    "EXIT_OK",
    "EXIT_GENERIC",
    "EXIT_NAME_INVALID",
    "EXIT_NOT_FOUND",
    "EXIT_IO",
    "EXIT_CONFIG",
    "EXIT_MEMBER_NOT_FOUND",
]


if __name__ == "__main__":
    raise SystemExit(main())