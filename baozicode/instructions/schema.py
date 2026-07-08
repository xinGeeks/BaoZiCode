"""instructions 包的核心数据类型。

三段优先级(user_global → project_local → project_root)按
openspec/changes/v0-8-memory-and-sessions/specs/instructions-loader/spec.md
"## 项目指令" 段注入到 system prompt 顶部。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

InstructionSource = Literal["user_global", "project_local", "project_root"]


@dataclass(frozen=True)
class InstructionLayer:
    """一个 BaoZiCode.md 文件,经过 @include 展开前的原始 raw_text。"""

    source: InstructionSource
    path: Path
    raw_text: str


@dataclass(frozen=True)
class LoadedInstructions:
    """三段加载 + @include 解析后的合并结果。"""

    layers: tuple[InstructionLayer, ...] = ()
    concatenated: str = ""
    included_files: frozenset[Path] = field(default_factory=frozenset)
    warnings: tuple[str, ...] = ()


__all__ = [
    "InstructionLayer",
    "InstructionSource",
    "LoadedInstructions",
]
