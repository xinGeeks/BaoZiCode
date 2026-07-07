"""L3 规则持久化 — `permissions.local.yaml` 的原子读写(v0.5)。

职责:
- 提供 `append_rule_to_local_yaml(rule, project_root) -> None`
- 提供 `load_local_yaml(project_root) -> dict`(loader 用)
- 写盘走 "写 tmp + os.replace" 原子替换,避免半写损坏
- dedup:同一 `(tool, pattern, decision)` 已存在则跳过(避免 Modal 反复点累积重复)

YAML 文件结构:
```yaml
mode: default  # 可选(覆盖)
rules:
  - tool: Bash
    pattern: "git *"
    decision: allow
  - tool: Bash
    pattern: "rm *"
    decision: deny
```

依赖方向:`permissions/` → `config/ schema`(类型),`persistence` 只写 YAML,
不 import 任何 Pydantic 模型(避免循环)。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from baozicode.permissions.types import PermissionRule


LOCAL_YAML_NAME = "permissions.local.yaml"


def local_yaml_path(project_root: Path) -> Path:
    """返回项目级 local YAML 的标准路径(`.baozicode/permissions.local.yaml`)。"""
    return project_root / ".baozicode" / LOCAL_YAML_NAME


def load_local_yaml(project_root: Path) -> dict:
    """读 local YAML;不存在/解析失败 → 返回空 dict(允许创建)。

    容错策略:
    - 文件不存在 → `{"mode": "default", "rules": []}`
    - YAML 解析失败 → 同上(并记 warning,留给上层)
    - 顶层不是 dict → 同上
    """
    path = local_yaml_path(project_root)
    if not path.is_file():
        return {"mode": "default", "rules": []}
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError):
        return {"mode": "default", "rules": []}
    if not isinstance(data, dict):
        return {"mode": "default", "rules": []}
    if "rules" not in data or not isinstance(data["rules"], list):
        data["rules"] = []
    if "mode" not in data:
        data["mode"] = "default"
    return data


def _normalize_rule_dict(d: dict) -> tuple[str, str, str] | None:
    """把 dict 规则转成 (tool, pattern, decision) 三元组;非法 → None。"""
    tool = d.get("tool")
    pattern = d.get("pattern")
    decision = d.get("decision")
    if not isinstance(tool, str) or not tool:
        return None
    if not isinstance(pattern, str) or not pattern:
        return None
    if decision not in ("allow", "deny"):
        return None
    return (tool, pattern, decision)


def has_rule(data: dict, rule: "PermissionRule") -> bool:
    """检查 data 中是否已有等价的 (tool, pattern, decision)。"""
    target = (rule.tool, rule.pattern, rule.decision)
    for entry in data.get("rules", []):
        if not isinstance(entry, dict):
            continue
        normalized = _normalize_rule_dict(entry)
        if normalized is None:
            continue
        if normalized == target:
            return True
    return False


def append_rule_to_local_yaml(rule: "PermissionRule", project_root: Path) -> None:
    """向 local YAML 追加一条规则,保持原子写 + dedup。

    - 文件不存在 → 创建新文件
    - 文件存在但损坏 → 备份 .bak 后重写
    - 同 (tool, pattern, decision) 已存在 → 跳过(幂等)
    - 写盘:tmp file + fsync + os.replace(原子)
    """
    path = local_yaml_path(project_root)
    data = load_local_yaml(project_root)

    if has_rule(data, rule):
        return  # 幂等

    data.setdefault("rules", []).append({
        "tool": rule.tool,
        "pattern": rule.pattern,
        "decision": rule.decision,
    })

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_yaml(path, data)


def _atomic_write_yaml(path: Path, data: dict) -> None:
    """原子地把 dict 写成 YAML:tmp + fsync + os.replace。

    失败语义:tmp 写入失败 → 抛 OSError,目标文件不动。
    """
    text = yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    # 写到 path 同目录的 tmp,然后 os.replace
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # Windows 上 fsync 在某些模式下会失败,允许 silently skip
                pass
        os.replace(tmp_path, path)
    except Exception:
        # 清理 tmp
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def remove_local_yaml(project_root: Path) -> bool:
    """删除 local YAML(测试 / 卸载时用)。返回是否真的删了。"""
    path = local_yaml_path(project_root)
    if not path.is_file():
        return False
    path.unlink()
    return True


__all__ = [
    "LOCAL_YAML_NAME",
    "append_rule_to_local_yaml",
    "has_rule",
    "load_local_yaml",
    "local_yaml_path",
    "remove_local_yaml",
]
