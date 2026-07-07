"""环境信息 — cwd / OS / Python / git。"""

from baozicode.prompt.types import BuildContext


def render(ctx: BuildContext) -> str:
    return f"""## 环境信息
- cwd: {ctx.cwd}
- os: {ctx.os_name}
- python: {ctx.python_version}
- git: {ctx.git_branch or "(no git)"} @ {ctx.git_commit or "(no commit)"}
- project: {ctx.project_name or "(unnamed)"}
- 时间: {ctx.now_iso}""".rstrip()


__all__ = ["render"]
