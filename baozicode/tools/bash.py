"""Bash 工具 — 异步执行 shell 命令,带 cwd 三状态机 + 逃逸检查。

cwd 状态机:
  1. 会话启动: cwd 锁定到 project_root
  2. cd 命令: 跟随 cd 到新目录(目标必须是 project_root 的子目录)
  3. 任意命令执行前: 解析最终 cwd,Path.resolve() 防逃逸

逃逸处理: 拒绝执行,is_error=True,cwd 不变。

注意: 本实现识别 leading `cd` + `&&`/`;` 链式中的 cd。对以下情况不防护:
- 命令中嵌入子 shell 改 cwd(如 `bash -c 'cd /etc'`)— plan 阶段看不到
- 命令先创建逃逸用 symlink 再 cd(如 `ln -s /etc tmp; cd tmp`):
  plan 阶段 symlink 不存在,resolve 不会跳到 /etc;真正执行时 cd 才跟随
  symlink — 这是已知边界,在 v0.3 通过 per-step cwd 校验补齐。

编码处理: 子进程 stdout/stderr 在 Windows 中文系统上默认 GBK (cp936);
直接 UTF-8 解码会乱码。本工具先尝试 UTF-8,失败回落到系统偏好编码,
再不行用 cp936 (Windows 中文默认)兜底,errors="replace" 兜底兜底。
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Callable

from baozicode.tools.base import ToolDefinition, ToolResult, decode_subprocess_output

DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 600
MAX_OUTPUT_BYTES = 30_000

TOOL = ToolDefinition(
    name="Bash",
    description=(
        "Execute a shell command inside the project working directory. "
        "Captures stdout and stderr (combined). The session tracks cwd "
        "across calls — `cd <dir>` is followed, but targets must remain "
        "inside the project root. Commands whose resolved cwd would escape "
        "the project root are rejected before execution."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute (passed to /bin/sh -c).",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    f"Optional timeout in seconds "
                    f"(default {DEFAULT_TIMEOUT_SECONDS}, max {MAX_TIMEOUT_SECONDS})."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "v1.3 — Optional absolute path overriding cwd for this "
                    "single call. When set, the command runs in this "
                    "directory; `_sessions.cwd` is NOT updated (fire-and-"
                    "forget). Used by sub-Agent worktree isolation to keep "
                    "Bash calls in the worktree directory."
                ),
            },
        },
        "required": ["command"],
    },
    risk="high",
    side_effect=True,
    path_args=[],  # Bash 的路径由 PathSandbox 用 regex 自行提取
)


class BashSession:
    """单 project_root 一个会话 — 持有 cwd + 边界。"""

    def __init__(self, project_root: Path):
        self.project_root: Path = project_root.resolve()
        self.cwd: Path = self.project_root

    def plan_cd(self, command: str) -> tuple[Path | None, str | None]:
        """Walk command 找 cd 操作,返回 (final_cwd, error_or_none)。

        仅识别段首的 `cd` + `&&`/`;` 链式中的 cd;不识别嵌入子 shell 的 cd。
        """
        segments = re.split(r"\s*(?:&&|;|\|\|)\s*", command)
        current = self.cwd
        for seg in segments:
            cd_match = re.match(
                r"""^\s*cd\s+(?:"([^"]*)"|'([^']*)'|(\S+))?\s*(.*)$""",
                seg.strip(),
            )
            if not cd_match:
                continue
            target = cd_match.group(1) or cd_match.group(2) or cd_match.group(3)
            if target is None:
                return None, "cd with no argument resolves to $HOME (outside project)"
            if target == "-":
                return None, "cd - references OLDPWD (outside project)"
            if target.startswith("~") or "$" in target or "`" in target:
                return None, f"cd {target!r} expands outside project (shell expansion not safe)"
            resolved = self._resolve(target, current)
            if not self._is_inside(resolved):
                return None, f"cd {target!r} escapes project root"
            current = resolved
        return current, None

    def _resolve(self, path_str: str, base: Path) -> Path:
        if os.path.isabs(path_str):
            return Path(path_str).resolve()
        return (base / path_str).resolve()

    def _is_inside(self, path: Path) -> bool:
        try:
            path.relative_to(self.project_root)
            return True
        except ValueError:
            return False

    def display_cwd(self) -> str:
        try:
            rel = self.cwd.relative_to(self.project_root)
            return "." if str(rel) == "." else str(rel)
        except ValueError:
            return str(self.cwd)


_sessions: dict[str, BashSession] = {}

# v1.3 — D11:`set_cwd_validator` 注入 closure 让 Bash.execute 在
# 显式 cwd 模式下验证 cwd 是否合法。SubAgentManager._run_subagent 在
# `asyncio.create_task(...)` 之前 set,任务结束 unset。Closure 通常
# 检查 cwd 是否在 sub-Agent 的 effective_project_root(worktree) 内。
_cwd_validator: Callable[[Path], bool] | None = None


def set_cwd_validator(validator: Callable[[Path], bool] | None) -> None:
    """注入 cwd 校验 closure — D11。

    Args:
        validator: 接 Path 返回 bool;`True` 表示 cwd 在某有效 root 内。
            传 `None` 清除。
    """
    global _cwd_validator
    _cwd_validator = validator


def configure(project_root: str | Path) -> BashSession:
    """初始化或重置 project_root 的会话。App 启动时调用一次。"""
    key = str(Path(project_root).resolve())
    session = BashSession(Path(project_root))
    _sessions[key] = session
    return session


def get_session(project_root: str | Path) -> BashSession | None:
    key = str(Path(project_root).resolve())
    return _sessions.get(key)


def _resolve_default_root() -> Path | None:
    """回退:用环境变量 BAZ_PROJECT_ROOT 或当前工作目录。"""
    env = os.environ.get("BAZ_PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


async def execute(arguments: dict) -> ToolResult:
    command = arguments.get("command")
    if not command:
        return ToolResult.error_result("", "Bash: missing required argument 'command'")

    project_root = _resolve_default_root()
    if project_root is None:
        return ToolResult.error_result(
            "",
            "Bash: cannot determine project root. "
            "Set BAZ_PROJECT_ROOT env var or call configure(project_root).",
        )

    session = get_session(project_root)
    if session is None:
        session = configure(project_root)

    timeout_raw = arguments.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    timeout = max(1, min(timeout, MAX_TIMEOUT_SECONDS))

    # ---- v1.3 D5:显式 cwd 模式 ----
    # 非 None → 在指定 cwd 执行;不调 plan_cd、不更 _sessions.cwd
    # (fire-and-forget)。worktree sub-Agent 的所有 Bash 调用都走这条
    # 路径,主 Agent 默认走 v1.2 老路径。
    cwd_override = arguments.get("cwd")
    if cwd_override is not None:
        override_path = Path(cwd_override)
        # 安全校验 1:必须绝对路径
        if not override_path.is_absolute():
            return ToolResult.error_result(
                "",
                f"Bash: cwd 必须是绝对路径,得到 {cwd_override!r}",
            )
        # 安全校验 2:必须存在且是目录
        if not override_path.exists():
            return ToolResult.error_result(
                "", f"Bash: cwd 不存在: {cwd_override}",
            )
        if not override_path.is_dir():
            return ToolResult.error_result(
                "", f"Bash: cwd 不是目录: {cwd_override}",
            )
        # 强制 resolve 防 symlink 逃逸
        resolved = override_path.resolve()
        # 安全校验 3:必须在某有效 root 内(主 project_root 或
        # 注入的 cwd_validator 接受的 root —— D11)
        inside_main = session._is_inside(resolved)
        inside_extra = (
            _cwd_validator is not None and _cwd_validator(resolved)
        )
        if not inside_main and not inside_extra:
            return ToolResult.error_result(
                "",
                f"Bash: cwd {cwd_override} 不在任何有效 root 内",
            )
        subprocess_cwd = str(resolved)
    else:
        new_cwd, plan_err = session.plan_cd(command)
        if plan_err is not None:
            return ToolResult.error_result(
                "",
                f"Bash: rejected — {plan_err}. "
                f"cwd remains '{session.display_cwd()}'.",
            )
        subprocess_cwd = str(new_cwd)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=subprocess_cwd,
        )
    except OSError as exc:
        return ToolResult.error_result("", f"Bash: failed to spawn: {exc}")

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except OSError:
            pass
        return ToolResult.error_result(
            "",
            f"Bash: timed out after {timeout}s",
        )

    # cwd 已验证过才提交 — 只在老路径(无 cwd_override)commit session.cwd
    if cwd_override is None:
        session.cwd = new_cwd

    stdout = decode_subprocess_output(stdout_b).rstrip()
    stderr = decode_subprocess_output(stderr_b).rstrip()
    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    output = "\n".join(parts) if parts else "(no output)"

    if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        truncated = output.encode("utf-8")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        output = truncated + f"\n... [truncated: output exceeded {MAX_OUTPUT_BYTES} bytes]"

    if proc.returncode != 0:
        return ToolResult.error_result(
            "",
            f"{output}\n[exit code {proc.returncode}]",
        )
    return ToolResult.success("", output)


__all__ = [
    "BashSession",
    "TOOL",
    "configure",
    "execute",
    "get_session",
    "set_cwd_validator",
]