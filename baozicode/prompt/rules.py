"""关键规则注册表 — 双重强化 system prompt 和工具 description。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from baozicode.tools.base import ToolDefinition


@dataclass(frozen=True)
class Rule:
    """一条关键规则,同时出现在 system prompt 和匹配工具的 description。

    - `prompt_text`: 完整版,带"为什么"和"怎么做",进 system prompt
    - `applies_to`: 适用工具名元组,("*" ,) 表示全局适用
    - `tool_prefix`: 简短版(1-2 句),进工具 description 开头
    """

    id: str
    prompt_text: str
    applies_to: tuple[str, ...]
    tool_prefix: str = ""


DEFAULT_RULES: tuple[Rule, ...] = (
    Rule(
        id="edit_requires_read",
        prompt_text=(
            "1. Edit/Write 前必先 Read：用 Edit 修改文件前,必须先用 Read 读完整文件内容；"
            "用 Write 覆盖文件前,必须先用 Read 确认当前内容。"
        ),
        applies_to=("Edit", "Write"),
        tool_prefix="【必读】调用前必须先用 Read 读完整文件,否则 Edit 的 old_text 无法匹配。",
    ),
    Rule(
        id="prefer_specialized_tools",
        prompt_text=(
            "2. 优先用专用工具:能用 Read/Grep/Glob 就不要用 Bash+cat/grep/find；"
            "能用 Edit 就不要用 sed。"
        ),
        applies_to=("Read", "Grep", "Glob", "Edit"),
        tool_prefix="【优先】需要此能力时优先调用本工具,而不是用 Bash 模拟。",
    ),
    Rule(
        id="bash_timeout",
        prompt_text=(
            "3. Bash 命令必设 timeout:所有 Bash 调用必须传 `timeout: 30` 参数"
            "(最长 120),避免无限卡住。"
        ),
        applies_to=("Bash",),
        tool_prefix="【必传 timeout】参数 timeout 必填,默认 30,最长 120,否则可能无限挂起。",
    ),
    Rule(
        id="parallel_limit",
        prompt_text=(
            "4. 并行上限 4:单次响应里无副作用工具(Read/Grep/Glob/WebFetch)的并行数"
            "不超过 4 个。"
        ),
        applies_to=("Read", "Grep", "Glob", "WebFetch"),
        tool_prefix="【并行上限 4】本工具可与其他无副作用工具并行,但单次不超过 4 个。",
    ),
    Rule(
        id="error_then_decide",
        prompt_text=(
            "5. 工具返回 is_error=true 时,必须先分析错误原因,再决定重试、换工具、"
            "还是改方案,不要盲目重试。"
        ),
        applies_to=("*",),
        tool_prefix="",  # 全局规则,不污染工具 description
    ),
    Rule(
        id="absolute_paths",
        prompt_text=(
            "6. 路径必为绝对路径:所有 file_path / path 参数必传绝对路径,"
            "cwd 已在环境信息给出。"
        ),
        applies_to=("Read", "Write", "Edit", "Grep", "Glob"),
        tool_prefix="【绝对路径】path/file_path 参数必为绝对路径(以 / 或盘符开头),不要传相对路径。",
    ),
    Rule(
        id="webfetch_to_file",
        prompt_text=(
            "7. WebFetch 后内容用 Read 风格处理:WebFetch 返回的 markdown 文本超过 2000 字时,"
            "先把它写入临时文件再用 Read 分页。"
        ),
        applies_to=("WebFetch",),
        tool_prefix="【大内容提示】超过 2000 字时建议写入临时文件后用 Read 读取,避免直接 paste 占用上下文。",
    ),
)


class RuleRegistry:
    """关键规则注册表。Agent 启动时构造一次,PromptBuilder 用它增强工具描述。"""

    def __init__(self, rules: tuple[Rule, ...] = DEFAULT_RULES) -> None:
        self._rules = rules

    @property
    def rules(self) -> tuple[Rule, ...]:
        return self._rules

    def for_tool(self, tool_name: str) -> list[Rule]:
        """返回适用于指定工具名的规则列表。"""
        out: list[Rule] = []
        for r in self._rules:
            if "*" in r.applies_to or tool_name in r.applies_to:
                out.append(r)
        return out

    def render_for_prompt(self) -> str:
        """把所有规则的 prompt_text 拼成 system prompt 里的 7 条编号列表。"""
        return "\n".join(r.prompt_text for r in self._rules)

    def augment_tool(self, tool: ToolDefinition) -> ToolDefinition:
        """对单个工具,把所有适用规则的 tool_prefix 拼到 description 开头。"""
        prefixes = [r.tool_prefix for r in self.for_tool(tool.name) if r.tool_prefix]
        if not prefixes:
            return tool
        new_desc = "\n".join(prefixes) + "\n\n" + tool.description
        return replace(tool, description=new_desc)


__all__ = ["Rule", "RuleRegistry", "DEFAULT_RULES"]
