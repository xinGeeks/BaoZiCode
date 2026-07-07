"""L1 DangerousCommandBlacklist — 硬拦截黑名单(v0.5)。

纵深防御 = 文本层(快路径正则) + shlex token 层(精确判断)。

硬编码:`TEXT_PATTERNS` / `SUSPICIOUS_KEYWORDS` / `_DANGEROUS_ARGV0`
均为 Python 模块级常量,不可被任何配置层、session rule 或 Modal
用户选择放行。`RuleEngine` 在 L3 评估时,deny 规则也不能覆盖 L1 的 deny
(L1 在 L3 之前短路)。

设计取舍:
- 文本层先扫 raw command / file_path,大部分危险情况在 0.01ms 内被拒
- 仅当文本层未命中且命令含 SUSPICIOUS_KEYWORDS 时,才启动 shlex 解析
- shlex 解析失败(未闭合引号等)→ 保守拒
- bash -c / sh -c 整条拒,不解析内部字符串(无法静态分析,默认拒绝最安全)
"""

from __future__ import annotations

import re
import shlex

from baozicode.permissions.types import PermissionDecision
from baozicode.tools.base import ToolCall


# 文本层正则:硬编码,不可配置
# 注:每条 pattern 都注释了匹配的真实命令示例
TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # rm -rf / ; rm -rf /* ; rm -Rf / ; rm -rf /var (针对根级目录)
    # 设计取舍:只对"针对根级目录"的 rm 拒;`rm -rf build/` 这种项目内清理
    # 留给 L2 沙箱 / L3 规则(否则 LLM 无法清理 build artifacts)。
    re.compile(
        r"\brm\s+(?:-\w*r\w*f\w*|--recursive)\s+/"
        r"(?:\s|$|\*|[;&|]|bin\b|etc\b|var\b|usr\b|home\b|root\b"
        r"|tmp\b|opt\b|boot\b|sbin\b|lib\b|mnt\b|sys\b|proc\b|dev\b)"
    ),
    # sudo rm / sudo chmod / sudo chown / sudo mv / sudo cp / sudo dd / sudo mkfs / sudo kill / sudo halt / sudo reboot / sudo shutdown
    re.compile(r"\bsudo\s+(rm|chmod|chown|mv|cp|dd|mkfs|kill|halt|reboot|shutdown)\b"),
    # chmod -R 777 / chmod -R 0777
    re.compile(r"\bchmod\s+(-R\s+0*777|--recursive\s+0*777|-R\s+777)\b"),
    # dd if=... of=/dev/sda / of=/dev/nvme0n1
    re.compile(r"\bdd\s+if=[^&;]*\bof=/dev/(sd|hd|nvme|vd|xvd)"),
    # mkfs / mkfs.xfs / mkfs.ext4
    re.compile(r"\bmkfs(\.\w+)?\b"),
    # curl | sh / curl | bash / wget | sh / wget | bash
    re.compile(r"\b(curl|wget)\s+[^|]*\|\s*(sh|bash)\b"),
    # fork bomb :(){ :|:& };:
    re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    # mv / /dev/null
    re.compile(r"\bmv\s+/\s+/dev/null\b"),
    # /etc/passwd / /etc/shadow 直接路径访问(允许出现在路径任意位置)
    re.compile(r"(?:^|/)etc/(passwd|shadow)(?:\s|$|'|\"|$)"),
    # ~/.ssh/authorized_keys 直接路径访问(允许前面有路径段)
    re.compile(r"\.ssh/authorized_keys\b"),
    # ~/.bashrc / ~/.bash_profile / ~/.bash_logout
    re.compile(r"\.(bashrc|bash_profile|bash_logout)\b"),
    # bash -c / sh -c 任何形式 — 整条拒,不解析内部字符串
    re.compile(r"\b(bash|sh)\s+-c\b"),
)


# 触发 shlex token 解析层的"可疑关键词"
# 仅当 command 含这些词时,才付出 shlex 解析的开销
SUSPICIOUS_KEYWORDS: frozenset[str] = frozenset({
    "sudo", "rm", "chmod", "chown", "dd", "mkfs", "curl", "wget",
})


# shlex 解析后,argv[0] 直接触发 deny 的危险可执行名
# 注:完整的"危险 token"集合(对调用方公开)
DANGEROUS_TOKENS: frozenset[str] = frozenset({
    "sudo", "rm", "chmod", "chown", "dd", "mkfs", "mv", "cp",
    "kill", "halt", "reboot", "shutdown",
})


def _extract_raw(call: ToolCall) -> str:
    """从 ToolCall 提取要扫描的原始字符串。

    Bash → command 字段;
    Read/Write/Edit → file_path 字段;
    其他工具 → 空字符串(不参与 L1 扫描)。
    """
    if call.name == "Bash":
        return str(call.arguments.get("command", ""))
    if call.name in {"Read", "Write", "Edit"}:
        return str(call.arguments.get("file_path", ""))
    return ""


def _text_scan(call: ToolCall) -> PermissionDecision | None:
    """文本层:对 raw command / file_path 做正则匹配。

    返回 PermissionDecision(deny) 或 None(fallthrough)。
    """
    raw = _extract_raw(call)
    if not raw:
        return None
    for pat in TEXT_PATTERNS:
        if pat.search(raw):
            return PermissionDecision(
                decision="deny",
                layer="L1_blacklist",
                reason=f"硬拦截黑名单命中(正则: `{pat.pattern[:60]}{'...' if len(pat.pattern) > 60 else ''}`)",
                matched_pattern=pat.pattern,
            )
    return None


def _token_scan(call: ToolCall) -> PermissionDecision | None:
    """shlex token 层:解析后逐 token 判断。

    总是尝试解析 Bash 命令(不依赖 SUSPICIOUS_KEYWORDS):
    - 解析失败(未闭合引号等)→ 保守拒
    - 解析成功且命令含 SUSPICIOUS_KEYWORDS → 做 token 级判定
    """
    if call.name != "Bash":
        return None
    command = str(call.arguments.get("command", ""))
    if not command:
        return None

    try:
        tokens = shlex.split(command)
    except ValueError:
        # shlex 解析失败(未闭合引号等),保守拒
        return PermissionDecision(
            decision="deny",
            layer="L1_blacklist",
            reason="Bash: 无法安全解析命令(可能引号未闭合)",
            matched_pattern="shlex_parse_error",
        )

    if not tokens:
        return None

    # 只对含 SUSPICIOUS_KEYWORDS 的命令做 token 级深入判定
    if not any(kw in command for kw in SUSPICIOUS_KEYWORDS):
        return None

    argv0 = tokens[0]

    # sudo <dangerous_subcommand>
    if argv0 == "sudo" and len(tokens) >= 2:
        sub = tokens[1]
        if sub in {"rm", "chmod", "chown", "mv", "cp", "dd", "mkfs"}:
            return PermissionDecision(
                decision="deny",
                layer="L1_blacklist",
                reason=f"sudo {sub} 被 L1 token 层拦截",
                matched_pattern=f"sudo {sub}",
            )

    # 直接 mkfs(以及将来扩展到 DANGEROUS_TOKENS 中的无 flag 危险命令)
    if argv0 in DANGEROUS_TOKENS and argv0 == "mkfs":
        return PermissionDecision(
            decision="deny",
            layer="L1_blacklist",
            reason=f"{argv0} 被 L1 token 层拦截",
            matched_pattern=argv0,
        )

    # dd if=... of=/dev/...
    if argv0 == "dd":
        for tok in tokens[1:]:
            if tok.startswith("of=/dev/"):
                return PermissionDecision(
                    decision="deny",
                    layer="L1_blacklist",
                    reason="dd 写入 /dev/... 设备被 L1 token 层拦截",
                    matched_pattern="dd of=/dev/",
                )

    return None


class DangerousCommandBlacklist:
    """L1 硬拦截黑名单 — 纵深防御。

    用法:
        bl = DangerousCommandBlacklist()
        decision = bl.check(call)
        if decision.decision == "deny":
            # 拦截,ToolResult.is_error = True,内容为 decision.reason
            ...

    设计保证(不可被配置覆盖):
    - `TEXT_PATTERNS` 是模块级常量,任何 YAML / session rule / Modal
      选择都不能影响它的内容
    - L1 在 5 层防御流水线最前,任何 allow 规则(L3 / L4 / L5)
      都不能让 L1 deny 变成 allow
    """

    def check(self, call: ToolCall) -> PermissionDecision:
        # 先文本层,命中即拒
        denied = _text_scan(call)
        if denied is not None:
            return denied
        # 再 token 层(只对 Bash 启动,且仅当含可疑关键词)
        denied = _token_scan(call)
        if denied is not None:
            return denied
        return PermissionDecision.fallthrough()


__all__ = [
    "DANGEROUS_TOKENS",
    "DangerousCommandBlacklist",
    "SUSPICIOUS_KEYWORDS",
    "TEXT_PATTERNS",
]