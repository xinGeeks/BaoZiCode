"""v1.0 Skills — Pydantic 元数据 + frontmatter 解析。

公开 API:
- `SkillFrontmatter` — frontmatter YAML 的 Pydantic 强校验模型
- `SkillDef`        — Skill 全量数据(frontmatter + body + source + path),frozen dataclass
- `parse_frontmatter(md_text)` — 抽 `---` 包围的 YAML,返回 (frontmatter, body)

失败模式:任何字段类型错 / 必填缺失 / 字段值越界 → `ValueError`,
带文件名信息(让 caller 决定 WARN skip 还是 panic)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Skill name 格式:小写字母开头 + 小写字母/数字/中划线
_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")

# mode 取值
ExecutionMode = Literal["shared", "independent"]

# history_bubbles 上限(v1.0 硬编码,防止子对话撑爆)
MAX_HISTORY_BUBBLES = 50


class SkillFrontmatter(BaseModel):
    """YAML frontmatter 的强校验 Pydantic 模型。

    必填: name, description
    可选: mode, allowed-tools, history-bubbles, model, hidden

    字段命名规则:
    - Python 字段用下划线(YAML 里两种写法都接受:`history-bubbles` 或
      `history_bubbles` —— Pydantic v2 默认开启 `populate_by_name`,我们
      显式 `alias` 优先 kebab-case)
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    name: str = Field(alias="name")
    description: str = Field(alias="description")
    mode: ExecutionMode = Field(default="shared", alias="mode")
    allowed_tools: list[str] | None = Field(default=None, alias="allowed-tools")
    history_bubbles: int = Field(default=0, alias="history-bubbles")
    model: str | None = Field(default=None, alias="model")
    hidden: bool = Field(default=False, alias="hidden")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                f"skill name 不合法 (需 ^[a-z][a-z0-9-]*$): {v!r}"
            )
        return v

    @field_validator("history_bubbles")
    @classmethod
    def _validate_history_bubbles(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"history_bubbles 不能为负数: {v}")
        if v > MAX_HISTORY_BUBBLES:
            raise ValueError(
                f"history_bubbles 超过上限 {MAX_HISTORY_BUBBLES}: {v}"
            )
        return v

    @field_validator("allowed_tools")
    @classmethod
    def _validate_allowed_tools(cls, v: list[str] | None) -> list[str] | None:
        # 不在 schema 层检查 tool 是否存在(boot 时 registry 才看 tool_registry)
        # 只查重名 + 空字符串
        if v is None:
            return v
        seen: set[str] = set()
        for tool in v:
            if not isinstance(tool, str) or not tool:
                raise ValueError(
                    f"allowed-tools 列表元素必须是非空字符串,得到: {tool!r}"
                )
            if tool in seen:
                raise ValueError(f"allowed-tools 列表有重复: {tool!r}")
            seen.add(tool)
        return v


@dataclass(frozen=True)
class SkillDef:
    """Skill 全量数据 — 供 registry / activation / loader 共享。

    - frontmatter: Pydantic 模型(已校验)
    - body: 原始 Markdown 正文,占位符 {var} 保留
    - source: builtin / user / project — 哪个目录来的(优先级信息)
    - path: 源文件绝对路径(供 hot-reload 用)
    """

    frontmatter: SkillFrontmatter
    body: str
    source: Literal["builtin", "user", "project"]
    path: Path

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def description(self) -> str:
        return self.frontmatter.description

    @property
    def mode(self) -> ExecutionMode:
        return self.frontmatter.mode

    @property
    def allowed_tools(self) -> list[str] | None:
        return self.frontmatter.allowed_tools

    @property
    def history_bubbles(self) -> int:
        return self.frontmatter.history_bubbles

    @property
    def model(self) -> str | None:
        return self.frontmatter.model

    @property
    def hidden(self) -> bool:
        return self.frontmatter.hidden


def parse_frontmatter(md_text: str, *, file_path: Path | None = None) -> tuple[SkillFrontmatter, str]:
    """从 Markdown 文本抽 frontmatter + body。

    格式约定:
    - 文档以 `---` 起头,后跟 YAML 块,以 `---` 结束
    - 块后面是正文(可能为空,可能多行)
    - 没有 frontmatter 的整段当 body,frontmatter 用全部默认值(但 name
      必填,缺 name 仍 ValueError)

    Args:
        md_text: 完整 Markdown 文本
        file_path: 可选,只用于错误消息(path 本身不解析)

    Returns:
        (SkillFrontmatter, body) tuple

    Raises:
        ValueError: YAML 解析错 / 字段类型错 / 必填缺失 / 字段值越界
    """
    # file_path 用 forward-slash 风格(避免 Windows 把 /tmp 渲染成 \tmp)
    if file_path is not None:
        path_str = str(file_path).replace("\\", "/")
        prefix = f"{path_str}: "
    else:
        prefix = ""

    # 抽 frontmatter: 行扫描 — 找第一个 `---` 行(开头)和下一个 `---` 行(结束)
    lines = md_text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        # 不以 `---` 起头(可能是 `---foo` 或纯 body),不视为 frontmatter
        fm_dict: dict = {}
        body = md_text
    else:
        # 找第二个 `---` 行(结束标记)
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].rstrip() == "---":
                end_idx = i
                break
        if end_idx is None:
            raise ValueError(
                f"{prefix}frontmatter 未正确以 `---` 结束 "
                f"(从第 1 行到 EOF 共 {len(lines)} 行都没找到结束行)"
            )
        # YAML 内容是 lines[1:end_idx],body 是 lines[end_idx+1:]
        yaml_content = "\n".join(lines[1:end_idx])
        body_lines = lines[end_idx + 1 :]
        # body 去掉前导空行
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

    # Pydantic 校验
    try:
        fm = SkillFrontmatter.model_validate(fm_dict)
    except Exception as e:
        # Pydantic v2 的 ValidationError 消息很啰嗦,简化下
        msg = _simplify_validation_error(str(e))
        raise ValueError(f"{prefix}frontmatter 校验失败: {msg}") from e

    return fm, body


def _simplify_validation_error(msg: str) -> str:
    """Pydantic v2 ValidationError 消息很长,只保留第一条 error 的核心信息。"""
    # 形如: "1 validation error for SkillFrontmatter\nname\n  Field required ..."
    lines = msg.split("\n")
    if len(lines) >= 3:
        # 取第二行(字段名)和第三行(原因),拼成 "name: Field required"
        return f"{lines[1].strip()}: {lines[2].strip()}"
    return msg


__all__ = [
    "ExecutionMode",
    "MAX_HISTORY_BUBBLES",
    "SkillDef",
    "SkillFrontmatter",
    "parse_frontmatter",
]
