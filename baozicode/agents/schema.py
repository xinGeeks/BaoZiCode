"""v1.2 SubAgent Delegation — frontmatter schema + 解析。

公开 API:
- `AgentFrontmatter` — Pydantic 强校验 model
- `AgentDef` — frozen dataclass(frontmatter + body + source + path)
- `parse_agent(md_text, file_path)` — 抽 `---` 包围的 YAML,返回 (frontmatter, body)

与 Skill v1.0 区别:
- 删 `mode` 字段(Agent 永远 independent)
- 增 `tools-deny` / `max-iterations` / `permission-mode` / `nesting-depth`
- `model` 字段限定为 `inherit | haiku | sonnet | opus` 字面量
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Agent name 格式:小写字母开头 + 小写字母/数字/中划线(跟 Skill 一致)
_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")

# model 取值字面量
AgentModel = Literal["inherit", "haiku", "sonnet", "opus"]

# permission-mode 取值字面量
AgentPermissionMode = Literal["strict", "default", "permissive"]

# nesting-depth 上限(0 = 不允许 spawn;预留 1-3 给 future v1.3+)
MAX_NESTING_DEPTH = 3

# max-iterations 上限(防 LLM 死循环)
MAX_ITERATIONS = 100


class AgentFrontmatter(BaseModel):
    """YAML frontmatter 的强校验 Pydantic 模型。

    必填: name, description
    可选: tools / tools-deny / model / max-iterations / permission-mode /
          nesting-depth / hidden

    字段命名:
    - Python 用下划线(YAML kebab-case 优先,`populate_by_name=True` 兜底)
    """

    model_config = ConfigDict(
        extra="ignore",       # 未识别字段静默丢弃(给 future 扩展留口子)
        populate_by_name=True,
    )

    name: str
    description: str
    tools: list[str] | None = None
    tools_deny: list[str] | None = Field(default=None, alias="tools-deny")
    model: AgentModel | None = None  # None = inherit(用主 Agent 的 model)
    max_iterations: int = Field(default=20, alias="max-iterations")
    permission_mode: AgentPermissionMode | None = Field(
        default=None, alias="permission-mode"
    )  # None = inherit(用主 Agent 的 mode)
    nesting_depth: int = Field(default=0, alias="nesting-depth")
    hidden: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                f"agent name 不合法 (需 ^[a-z][a-z0-9-]*$): {v!r}"
            )
        return v

    @field_validator("max_iterations")
    @classmethod
    def _validate_max_iterations(cls, v: int) -> int:
        if v < 1 or v > MAX_ITERATIONS:
            raise ValueError(
                f"max-iterations 越界 (1 ≤ n ≤ {MAX_ITERATIONS}): {v}"
            )
        return v

    @field_validator("nesting_depth")
    @classmethod
    def _validate_nesting_depth(cls, v: int) -> int:
        if v < 0 or v > MAX_NESTING_DEPTH:
            raise ValueError(
                f"nesting-depth 越界 (0 ≤ n ≤ {MAX_NESTING_DEPTH}): {v}"
            )
        return v

    @field_validator("tools", "tools_deny")
    @classmethod
    def _validate_tools_list(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        seen: set[str] = set()
        for tool in v:
            if not isinstance(tool, str) or not tool:
                raise ValueError(
                    f"tools / tools-deny 列表元素必须是非空字符串,得到: {tool!r}"
                )
            if tool in seen:
                raise ValueError(
                    f"tools / tools-deny 列表有重复: {tool!r}"
                )
            seen.add(tool)
        return v


@dataclass(frozen=True)
class AgentDef:
    """Agent 全量数据 — registry / runtime / manager 共享。

    - frontmatter: Pydantic 模型(已校验)
    - body: 原始 Markdown 正文,占位符 {var} 保留
    - source: builtin / user / project / plugin
    - path: 源文件绝对路径(plugin 时是 `Path("<mcp://<server>/<name>>")`)
    """

    frontmatter: AgentFrontmatter
    body: str
    source: Literal["builtin", "user", "project", "plugin"]
    path: Path

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def description(self) -> str:
        return self.frontmatter.description

    @property
    def tools(self) -> list[str] | None:
        return self.frontmatter.tools

    @property
    def tools_deny(self) -> list[str] | None:
        return self.frontmatter.tools_deny

    @property
    def model(self) -> AgentModel | None:
        return self.frontmatter.model

    @property
    def max_iterations(self) -> int:
        return self.frontmatter.max_iterations

    @property
    def permission_mode(self) -> AgentPermissionMode | None:
        return self.frontmatter.permission_mode

    @property
    def nesting_depth(self) -> int:
        return self.frontmatter.nesting_depth

    @property
    def hidden(self) -> bool:
        return self.frontmatter.hidden


def parse_agent(
    md_text: str,
    *,
    file_path: Path | None = None,
) -> tuple[AgentFrontmatter, str]:
    """从 Markdown 文本抽 frontmatter + body。

    格式约定:文档以 `---` 起头 + YAML 块 + `---` 结束 + body。
    没有 frontmatter → 整段当 body,frontmatter 用全部默认值(但 name 必填)。

    Args:
        md_text: 完整 Markdown 文本
        file_path: 可选,只用于错误消息(path 本身不解析)

    Returns:
        (AgentFrontmatter, body) tuple

    Raises:
        ValueError: YAML 解析错 / 字段类型错 / 必填缺失 / 字段值越界
    """
    # file_path 走 forward-slash 风格(避免 Windows 把 /tmp 渲染成 \tmp)
    if file_path is not None:
        path_str = str(file_path).replace("\\", "/")
        prefix = f"{path_str}: "
    else:
        prefix = ""

    lines = md_text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        # 不以 `---` 起头 — 不视为 frontmatter
        fm_dict: dict = {}
        body = md_text
    else:
        # 找第二个 `---` 行
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                end_idx = i
                break
        if end_idx is None:
            raise ValueError(
                f"{prefix}frontmatter 未正确以 `---` 结束"
            )
        yaml_content = "\n".join(lines[1:end_idx])
        body_lines = lines[end_idx + 1 :]
        while body_lines and body_lines[0] == "":
            body_lines.pop(0)
        body = "\n".join(body_lines)
        try:
            parsed = yaml.safe_load(yaml_content) if yaml_content.strip() else {}
        except yaml.YAMLError as e:
            raise ValueError(f"{prefix}frontmatter YAML 解析失败: {e}") from e
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ValueError(
                f"{prefix}frontmatter 必须是 YAML mapping(顶层 dict),"
                f"实际是 {type(parsed).__name__}"
            )
        fm_dict = parsed

    try:
        fm = AgentFrontmatter.model_validate(fm_dict)
    except Exception as e:
        msg = _simplify_validation_error(str(e))
        raise ValueError(f"{prefix}frontmatter 校验失败: {msg}") from e

    return fm, body


def _simplify_validation_error(msg: str) -> str:
    """Pydantic v2 ValidationError 消息很长,只保留第一条 error 的核心信息。"""
    lines = msg.split("\n")
    if len(lines) >= 3:
        return f"{lines[1].strip()}: {lines[2].strip()}"
    return msg


__all__ = [
    "AgentDef",
    "AgentFrontmatter",
    "AgentModel",
    "AgentPermissionMode",
    "MAX_ITERATIONS",
    "MAX_NESTING_DEPTH",
    "parse_agent",
]
