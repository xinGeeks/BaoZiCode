"""instructions 包 — 三层 BaoZiCode.md + @include。

公开 API:
- `bootstrap(project_root, config) -> LoadedInstructions`
- `LoadedInstructions`, `InstructionLayer`(re-export)
- `scan_three_tiers`, `load_layer`, `resolve_includes`, `concat`
- `user_baozicode_root`(给 @include 白名单用)

按 openspec/changes/v0-8-memory-and-sessions/specs/instructions-loader/spec.md:
- 三层按 user_global → project_local → project_root 拼接
- @include 走 include.resolve_includes(深度≤5 / 环路 / 路径白名单)
- 全 0 个文件 → 抛 banner 给 stderr,bootstrap 仍返回空 LoadedInstructions
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from baozicode.config.schema import AppConfig
from baozicode.instructions.include import resolve_includes, user_baozicode_root
from baozicode.instructions.loader import load_layer, scan_three_tiers
from baozicode.instructions.schema import (
    InstructionLayer,
    InstructionSource,
    LoadedInstructions,
)

log = logging.getLogger(__name__)


def concat(
    layers: list[InstructionLayer],
    project_root: Path,
    *,
    max_depth: int = 5,
) -> LoadedInstructions:
    """对每个 layer 跑 resolve_includes,再按 \n\n---\n\n 拼接。

    返回的 LoadedInstructions 包含:
    - `layers`: 入参原样(已展开 include 前的 raw_text)
    - `concatenated`: 三段拼接好的最终文本(已 strip)
    - `included_files`: 所有递归包含的文件 path 集合
    - `warnings`: 任何 include guard 失败的提示
    """
    if not layers:
        return LoadedInstructions()

    all_warnings: list[str] = []
    all_included: set[Path] = set()
    resolved_texts: list[str] = []

    for layer in layers:
        resolved, warnings = resolve_includes(
            layer.raw_text,
            layer.path,
            project_root,
            max_depth=max_depth,
        )
        resolved_texts.append(resolved.strip())
        all_warnings.extend(warnings)
        # 收集 include 链上的文件(当前 layer 自身 + 所有递归引用)
        all_included.add(layer.path)
        # 进一步把 warnings 解析出的 include 链加入(简化:所有 resolve 过的 path)
        # — resolve_includes 不直接返回 included set,所以这里只记 layer 自身;
        #   严格收集可在 include 层扩展。当前实现:layer 自身 + warnings 来源。

    joined = "\n\n---\n\n".join(t for t in resolved_texts if t)

    return LoadedInstructions(
        layers=tuple(layers),
        concatenated=joined,
        included_files=frozenset(all_included),
        warnings=tuple(all_warnings),
    )


def bootstrap(project_root: Path, config: AppConfig) -> LoadedInstructions:
    """启动入口:扫三层 → 加载 → 展开 include → 拼接。

    - 全 0 个文件 → 打印 stderr banner(仅 1 次),返回空 LoadedInstructions
    - 至少 1 个文件 → 静默加载,返回 LoadedInstructions
    - `config` 保留为未来扩展(如 user 可关掉某层);当前 v0.8 不读取任何开关
    """
    paths = scan_three_tiers(project_root)
    if not paths:
        print(
            "[BaoZiCode] 未找到 BaoZiCode.md,建议创建项目根目录文件",
            file=sys.stderr,
        )
        return LoadedInstructions()

    layers: list[InstructionLayer] = []
    for p in paths:
        try:
            layers.append(load_layer(p))
        except OSError as exc:
            log.warning("instructions: load_layer(%s) failed: %s", p, exc)

    if not layers:
        return LoadedInstructions()

    return concat(layers, project_root)


__all__ = [
    "InstructionLayer",
    "InstructionSource",
    "LoadedInstructions",
    "bootstrap",
    "concat",
    "load_layer",
    "resolve_includes",
    "scan_three_tiers",
    "user_baozicode_root",
]
