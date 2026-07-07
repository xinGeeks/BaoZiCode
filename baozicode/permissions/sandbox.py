"""L2 PathSandbox — 路径沙箱(v0.5)。

纵深防御第 2 层:文件操作必须落在项目根目录之内。

设计要点:
- `real_root` 启动时解析,消除 symlink 防逃逸
- 文件工具(Read/Write/Edit/Grep/Glob)从 `ToolDefinition.path_args` 提取 path
- Bash 走保守 regex 从 raw command 中抽 path literal
- 抽到 path 后,先做 symlink 解析(`Path.resolve()`),再做 `is_relative_to(real_root)`
- 任何 shell expansion marker(`$VAR` / `${VAR}` / `~` / 反引号)直接拒
  (无法静态确定展开后的真实路径,保守拒,让 Modal/L3 处理)
- Bash 链式命令中只要有一个 path 逃逸 → 整条拒(最严格)

依赖方向:`permissions/` → `tools/base.py`(单向)。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from baozicode.permissions.types import PermissionDecision

if TYPE_CHECKING:
    from baozicode.tools.base import ToolCall


# 工具名 → 声明的 path_args 的映射(运行时通过 registry.get_tool(name).path_args 拿)
# 这里写一份静态副本,作为测试时的对照
DEFAULT_PATH_ARGS: dict[str, list[str]] = {
    "Read": ["file_path"],
    "Write": ["file_path"],
    "Edit": ["file_path"],
    "Grep": ["path"],
    "Glob": ["path"],
    "Bash": [],   # Bash 走 regex 抽
    "WebFetch": [],  # URL 不算文件系统路径
}


# 触发 deny 的 shell expansion 标记
# 设计取舍:这些标记使得 L2 无法静态判定展开后的真实路径,直接拒
SHELL_EXPANSION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"),  # $VAR / ${VAR}
    re.compile(r"\$\([^)]*\)"),                       # $(...)
    re.compile(r"`[^`]*`"),                            # `cmd`
    re.compile(r"(?:^|[\s|&;>])(~)(?=/|\s|$)"),     # ~ 后面跟 / 或空白(单独的 ~ 也是)
)


# 从 Bash raw command 中抽 path literal 的保守 regex
# 要求:
#   - path 必须由 [\s|&;>] 之前缀(命令边界,避免在 --foo=/path 中误抓)
#   - path 内容不能含 shell 特殊字符(空格 / pipe / & / ; / > / ' / ")
#   - 可选 ~ 开头(但 ~ 已经在 SHELL_EXPANSION_PATTERNS 里被拒)
#   - 绝对路径 / .相对 / ..相对 三种
BASH_PATH_RE = re.compile(
    r"(?:^|[\s|&;>])"
    r"("
    r"~?/(?:[^\s|&;>'\"]+)"   # 绝对路径(可选 ~ 头)
    r"|"
    r"\.{0,2}/(?:[^\s|&;>'\"]+)"  # 相对路径(. / .. / ./foo / ../foo)
    r"|"
    r"~"  # 单独的 ~ 也能匹配到(后接边界)
    r")"
)


class PathSandbox:
    """L2 路径沙箱 — 限制文件操作必须落在 `real_root` 之内。

    用法:
        sandbox = PathSandbox(real_root=Path("/proj").resolve())
        decision = sandbox.check(call)
        if decision.decision == "deny":
            # 拦截,ToolResult.is_error = True

    设计保证:
    - `real_root` 在构造时 resolve,消除 symlink 逃逸
    - 任何要 check 的 path 都先 `Path.resolve()` 再做前缀判断
    - 任何含 shell expansion 标记的 path 都被拒
    - 抽到 path literal 的 Bash 链式命令,只要有一个逃逸,整条拒
    """

    def __init__(self, real_root: Path):
        self.real_root = real_root.resolve()

    # ---- 工具 ----

    def is_inside(self, path: Path) -> bool:
        """检查 `path`(或其 symlink 解析后)是否在 `real_root` 之内。

        用 `is_relative_to`(Python 3.9+)——比手写 relative_to + except 干净。
        """
        try:
            resolved = path.resolve()
        except OSError:
            return False
        try:
            return resolved.is_relative_to(self.real_root)
        except (AttributeError, ValueError):
            # Python <3.9 fallback
            try:
                resolved.relative_to(self.real_root)
                return True
            except ValueError:
                return False

    def _check_one_path(self, path_str: str) -> PermissionDecision | None:
        """对单个 path literal 做沙箱检查。

        返回:
        - PermissionDecision(deny) — path 逃逸 / shell expansion / 解析失败
        - None — path 在沙箱内或不存在(L3/Mode/User 继续判定)
        """
        # 单独的 ~ 永远 shell expansion,直接拒
        if path_str == "~":
            return PermissionDecision(
                decision="deny",
                layer="L2_sandbox",
                reason=f"PathSandbox: '~' 展开路径无法静态判定(shell expansion)",
                matched_pattern="~",
            )

        # 检查 shell expansion marker
        for pat in SHELL_EXPANSION_PATTERNS:
            if pat.search(path_str):
                return PermissionDecision(
                    decision="deny",
                    layer="L2_sandbox",
                    reason=f"PathSandbox: 含 shell expansion marker,无法静态判定: {path_str!r}",
                    matched_pattern=pat.pattern,
                )

        # 解析为绝对路径
        if path_str.startswith("~"):
            # 已经被上面 SHELL_EXPANSION 拒了,这里只是防御性兜底
            return PermissionDecision(
                decision="deny",
                layer="L2_sandbox",
                reason=f"PathSandbox: '~' 展开路径无法静态判定: {path_str!r}",
                matched_pattern="~",
            )
        path = Path(path_str)
        if not path.is_absolute():
            # 相对路径 → 相对沙箱根解析
            path = self.real_root / path_str

        # 检查是否在沙箱内
        if not self.is_inside(path):
            return PermissionDecision(
                decision="deny",
                layer="L2_sandbox",
                reason=(
                    f"PathSandbox: 路径 {path_str!r} 解析后在项目根之外 "
                    f"(real_root={self.real_root})"
                ),
                matched_pattern=path_str,
            )

        return None  # 放行(交给 L3 / L4 / L5)

    def _check_path_arg(self, call: "ToolCall", arg_name: str) -> PermissionDecision | None:
        """从 `call.arguments[arg_name]` 拿 path,做一次沙箱检查。"""
        raw = call.arguments.get(arg_name)
        if not raw or not isinstance(raw, str):
            return None
        return self._check_one_path(raw)

    def _check_bash(self, call: "ToolCall") -> PermissionDecision | None:
        """对 Bash 命令做 path literal 抽取 + 沙箱检查。

        保守策略:
        - 只要一个 path 逃逸 → 整条拒
        - 任何 path 含 shell expansion → 整条拒
        - 没有 path literal → fallthrough(命令可能是纯计算,如 echo / cat / 等)
        """
        command = str(call.arguments.get("command", ""))
        if not command:
            return None

        # 先全 command 扫一遍 shell expansion(防御性,如果展开的不是 path 也无害)
        for pat in SHELL_EXPANSION_PATTERNS:
            if pat.search(command):
                return PermissionDecision(
                    decision="deny",
                    layer="L2_sandbox",
                    reason=(
                        f"PathSandbox: Bash 命令含 shell expansion marker,无法静态判定 "
                        f"路径({pat.pattern[:40]})"
                    ),
                    matched_pattern=pat.pattern,
                )

        # 抽 path literal
        candidates: list[str] = []
        for m in BASH_PATH_RE.finditer(command):
            candidate = m.group(1)
            # 去掉前缀空白(让 reason 好看)
            candidate = candidate.strip()
            if candidate:
                candidates.append(candidate)

        if not candidates:
            return None  # 没抽到 path,fallthrough

        for cand in candidates:
            decision = self._check_one_path(cand)
            if decision is not None:
                return decision  # 任何 path 逃逸 → 整条拒
        return None

    def check(self, call: "ToolCall") -> PermissionDecision:
        """主入口 — 检查 ToolCall 的 path 是否落在沙箱内。"""
        # 1. 文件类工具:从 path_args 抽
        path_args = DEFAULT_PATH_ARGS.get(call.name, [])
        for arg in path_args:
            decision = self._check_path_arg(call, arg)
            if decision is not None:
                return decision

        # 2. Bash 走 regex 抽
        if call.name == "Bash":
            decision = self._check_bash(call)
            if decision is not None:
                return decision

        # 3. 没有 path / 不需要沙箱检查 → fallthrough
        return PermissionDecision.fallthrough()


__all__ = [
    "BASH_PATH_RE",
    "DEFAULT_PATH_ARGS",
    "PathSandbox",
    "SHELL_EXPANSION_PATTERNS",
]
