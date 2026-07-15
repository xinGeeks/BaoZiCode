"""v1.4 Team Tools — `team_merge` git 顺序合并 helper。

公开 API:

- `run_team_merge(project_root, team, *, target='main', dry_run=False)
  -> dict` — 把 team 内所有 member 的 `wt/<name>` 分支顺序合并到 target;
  冲突走 `git merge --abort`,best-effort 收集 aborted 列表

设计要点:

- 字典序遍历 `team.members`(确定性 — 同输入永远同顺序)
- 每 member:`git merge --no-ff wt/<member> -m "..."`(non-fast-forward
  保留分支拓扑);冲突 → `git merge --abort` 立刻清场,留 aborted
- 全程不调 `git push`,只本地仓库;push 是用户后续手动的事
- `dry_run=True` → 只扫 members 列计划,不打 git
- 非 git repo / checkout 失败 → 返 `{status: 'error', error: '...'}`
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from .schema import Team

log = logging.getLogger(__name__)


def _run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess:
    """`git -C <project_root> <args...>` 简单封装。"""
    return subprocess.run(
        ["git", "-C", str(project_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def run_team_merge(
    project_root: Path,
    team: Team,
    *,
    target: str = "main",
    dry_run: bool = False,
) -> dict[str, Any]:
    """顺序合并 team 内所有 member 分支到 target。

    Args:
        project_root: git 仓库根目录
        team: Team 实例(读 team.members 字典序遍历)
        target: 目标分支,默认 `"main"`
        dry_run: True → 不调 git,只返 `{status: 'would-merge',
            members: [...]}` 计划

    Returns:
        dict with status / merged / aborted / target / error 字段:

        - 成功路径:`{status: 'complete', merged: [...], aborted: [],
          target: str}`
        - 部分成功:`{status: 'partial', merged: [...], aborted:
          [{member, reason}], target: str}` — 后续 member 跳过(已经
          merge --abort 干净)
        - 干跑:`{status: 'would-merge', members: [...], target: str}`
        - 非 git repo:`{status: 'error', error: '...'}`
        - checkout target 失败:`{status: 'error', error: '...'}`
    """
    members_sorted = sorted(team.members.keys())

    if dry_run:
        return {
            "status": "would-merge",
            "members": [f"wt/{m}" for m in members_sorted],
            "target": target,
        }

    # 1) 确认 git repo
    rev_parse = _run_git(project_root, "rev-parse", "--show-toplevel")
    if rev_parse.returncode != 0:
        return {
            "status": "error",
            "error": (
                f"team_merge: project_root is not a git repository "
                f"({project_root})"
            ),
        }

    # 2) checkout target
    checkout = _run_git(project_root, "checkout", target)
    if checkout.returncode != 0:
        err = (checkout.stderr or "").strip().splitlines()
        return {
            "status": "error",
            "error": (
                f"team_merge: git checkout {target} failed: "
                f"{err[0] if err else 'unknown error'}"
            ),
        }

    merged: list[str] = []
    aborted: list[dict[str, str]] = []

    for member_name in members_sorted:
        branch = f"wt/{member_name}"
        log.info("team_merge: merging %s into %s", branch, target)
        merge = _run_git(
            project_root,
            "merge",
            "--no-ff",
            branch,
            "-m",
            f"Merge {branch} from team {team.name}",
        )
        if merge.returncode == 0:
            merged.append(member_name)
        else:
            err = (merge.stderr or "").strip().splitlines()
            reason = err[0] if err else "merge conflict"
            log.warning(
                "team_merge: %s merge failed — %s; running git merge --abort",
                branch,
                reason,
            )
            _run_git(project_root, "merge", "--abort")
            aborted.append({"member": member_name, "reason": reason})

    status = "complete" if not aborted else "partial"
    return {
        "status": status,
        "merged": merged,
        "aborted": aborted,
        "target": target,
    }


__all__ = ["run_team_merge"]