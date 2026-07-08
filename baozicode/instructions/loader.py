"""instructions 加载器 — 三段扫盘 + 单层读取。

按 openspec/changes/v0-8-memory-and-sessions/specs/instructions-loader/spec.md:
1. `~/.baozicode/BaoZiCode.md`     (user_global, 基础规则)
2. `<project_root>/.baozicode/BaoZiCode.md`  (project_local, 项目级)
3. `<project_root>/BaoZiCode.md`            (project_root, 覆盖)

返回的 3 个 candidate path 不存在的会静默跳过。
"""

from __future__ import annotations

import logging
from pathlib import Path

from baozicode.instructions.schema import InstructionLayer, InstructionSource

log = logging.getLogger(__name__)


def _candidate_paths(project_root: Path) -> list[tuple[InstructionSource, Path]]:
    """返回三层 candidate (source, path),按优先级低 → 高(后写覆盖)。

    实际拼接顺序:user_global → project_local → project_root。
    """
    home_baozicode = Path.home() / ".baozicode" / "BaoZiCode.md"
    project_local = project_root / ".baozicode" / "BaoZiCode.md"
    project_root_md = project_root / "BaoZiCode.md"
    return [
        ("user_global", home_baozicode),
        ("project_local", project_local),
        ("project_root", project_root_md),
    ]


def scan_three_tiers(project_root: Path) -> list[Path]:
    """返回存在的 candidate path 列表,按 user_global → project_local → project_root 排序。

    不读文件内容,不报错;调用方按此顺序加载并拼接。
    """
    found: list[Path] = []
    for _source, path in _candidate_paths(project_root):
        if path.is_file():
            found.append(path)
    return found


def load_layer(path: Path) -> InstructionLayer:
    """读单个 BaoZiCode.md → InstructionLayer。

    - 读 UTF-8,strip 末尾空白
    - source 由 path 反推:_candidate_paths() 中的顺序
    - 文件不存在或读失败 → 抛 FileNotFoundError(调用方应在 scan 后再调)
    """
    source = _infer_source(path)
    text = path.read_text(encoding="utf-8")
    return InstructionLayer(source=source, path=path, raw_text=text.strip())


def _infer_source(path: Path) -> InstructionSource:
    """从 path 反推 source。失败时抛 ValueError。"""
    resolved = path.resolve()
    home_baozicode = (Path.home() / ".baozicode" / "BaoZiCode.md").resolve()
    if resolved == home_baozicode:
        return "user_global"
    # 剩下的两层只能靠 path 后缀判断:
    #   <root>/.baozicode/BaoZiCode.md → project_local
    #   <root>/BaoZiCode.md            → project_root
    parent_name = path.parent.name
    if parent_name == ".baozicode":
        return "project_local"
    return "project_root"


__all__ = ["scan_three_tiers", "load_layer"]
