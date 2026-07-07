"""L3 规则加载器 — 三层 YAML 合并(v0.5)。

按优先级从高到低搜索四个位置(实际合并时只取存在的):
1. `<project>/.baozicode/permissions.local.yaml`  — 最高(local 不进 git)
2. `<project>/.baozicode/permissions.yaml`        — 项目级
3. `~/.config/baozicode/permissions.yaml`          — 用户全局(最低)
4. (无文件)                                          — 空,使用默认

合并规则(deny 优先级 > allow,closest-layer wins):
- 同一 (tool, pattern) 出现在多层 → 来自高优先级层的胜出
- 同层内多个 rule:扫描顺序就是声明顺序(deny 命中即短路)
- 跨层:扫描时 session → local → project → user_global,扫描到 deny 立即短路

mode 解析优先级(高 → 低):
1. session mode(由 `Agent.__init__` 传入,loader 不管)
2. local YAML 的 `mode` 字段
3. project YAML 的 `mode` 字段
4. user_global YAML 的 `mode` 字段
5. 默认 "default"

错误处理:
- 文件不存在 → 静默跳过
- YAML 格式坏 → 静默跳过(上层用 `print_warning` 提示;这里不抛)
- 单条 rule 字段不全 → 跳过该条,继续解析其它
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from baozicode.permissions.persistence import load_local_yaml
from baozicode.permissions.types import (
    MergedPermissions,
    PermissionMode,
    PermissionRule,
    RuleSource,
)

log = logging.getLogger(__name__)


def _search_paths(project_root: Path) -> list[tuple[RuleSource, Path]]:
    """返回按优先级高 → 低排序的 (source, path) 列表。

    local 不进 git,放在 project 之前;每个 source 只出现一次。
    """
    home = Path.home() / ".config" / "baozicode" / "permissions.yaml"
    project = project_root / ".baozicode" / "permissions.yaml"
    local = project_root / ".baozicode" / "permissions.local.yaml"
    return [
        ("local", local),
        ("project", project),
        ("user_global", home),
    ]


def _parse_one_yaml(path: Path) -> tuple[PermissionMode, list[PermissionRule], bool]:
    """解析单个 YAML;返回 (mode, rules, parsed_ok)。

    - 文件不存在 → ("default", [], False)
    - 解析失败 → ("default", [], False),记 warning
    - 解析成功(可能 rules 为空)→ (mode, rules, True)
    """
    if not path.is_file():
        return ("default", [], False)

    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        log.warning("permissions: %s 解析失败,跳过: %s", path, exc)
        return ("default", [], False)

    if not isinstance(data, dict):
        log.warning("permissions: %s 顶层不是 dict,跳过", path)
        return ("default", [], False)

    mode_raw = data.get("mode", "default")
    mode: PermissionMode
    if mode_raw in ("strict", "default", "permissive"):
        mode = mode_raw  # type: ignore[assignment]
    else:
        log.warning("permissions: %s mode=%r 非法,回退 default", path, mode_raw)
        mode = "default"

    rules: list[PermissionRule] = []
    for entry in data.get("rules", []) or []:
        if not isinstance(entry, dict):
            log.warning("permissions: %s 规则不是 dict,跳过: %r", path, entry)
            continue
        tool = entry.get("tool")
        pattern = entry.get("pattern")
        decision = entry.get("decision")
        if not isinstance(tool, str) or not tool:
            log.warning("permissions: %s 规则缺 tool,跳过: %r", path, entry)
            continue
        if not isinstance(pattern, str) or not pattern:
            log.warning("permissions: %s 规则缺 pattern,跳过: %r", path, entry)
            continue
        if decision not in ("allow", "deny"):
            log.warning("permissions: %s 规则缺/非法的 decision,跳过: %r", path, entry)
            continue
        rules.append(PermissionRule(
            tool=tool,
            pattern=pattern,
            decision=decision,  # type: ignore[arg-type]
            source="user_global",  # 后续被赋值覆盖
        ))

    return (mode, rules, True)


def load_permissions_layers(project_root: Path) -> MergedPermissions:
    """从三层 YAML 加载并合并成 `MergedPermissions`。

    实际加载顺序:local → project → user_global(高 → 低)。
    规则被标注 `source`;后续 `RuleEngine.check()` 按 source 优先级短路扫描。
    mode 取最高优先级文件中的值(local > project > user_global > "default")。
    """
    merged = MergedPermissions()
    merged.real_root = project_root.resolve()

    final_mode: PermissionMode = "default"
    for source, path in _search_paths(project_root):
        mode, rules, parsed = _parse_one_yaml(path)
        if parsed:
            merged.sources_loaded.append(str(path))
        for r in rules:
            object.__setattr__(r, "source", source)
            merged.rules.append(r)
        # mode 优先级:local > project > user_global;首个非 default 的胜出
        if source == "local" and parsed:
            final_mode = mode
        elif source == "project" and parsed and final_mode == "default":
            final_mode = mode
        elif source == "user_global" and parsed and final_mode == "default":
            final_mode = mode

    merged.mode = final_mode
    return merged


__all__ = ["load_permissions_layers"]
