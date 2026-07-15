"""v1-4-team-coordinator — Coordinator 模式三锁门。

`coordinator_enabled(config, team)` 判断三锁是否全部命中:

1. **配置层** — `config.teams.coordinator.enabled is True`
2. **环境变量** — `os.environ[env_var]` 是 truthy
   (`1` / `true` / `yes`,大小写不敏感)
3. **team 意图** — `team.coordinator is True`

任一锁缺失即视为未启用 → 调用方走 Lead 降级路径。
`_check_coordinator_locks(config, team)` 返缺失锁列表(给 stderr 报告用)。

不依赖 LLM / Agent / 工具中心 — 纯静态判断,可在 CLI / TUI 任意位置
调,无副作用。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from baozicode.config.schema import AppConfig
    from baozicode.teams.schema import Team


__all__ = [
    "check_coordinator_locks",
    "coordinator_enabled",
]


_TRUTHY_ENV = frozenset({"1", "true", "yes"})


def coordinator_enabled(config: "AppConfig | None", team: "Team") -> bool:
    """三锁全命中才返 True。

    任一锁缺失即返 False(不报错),由调用方决定降级策略。
    """
    if config is None or config.teams is None:
        return False
    coord_cfg = config.teams.coordinator
    if coord_cfg is None or not coord_cfg.enabled:
        return False
    env_var = coord_cfg.env_var or "BAOZICODE_COORDINATOR"
    env_value = os.environ.get(env_var, "").strip().lower()
    if env_value not in _TRUTHY_ENV:
        return False
    if not team.coordinator:
        return False
    return True


def check_coordinator_locks(
    config: "AppConfig | None", team: "Team"
) -> list[str]:
    """返缺失锁列表(给 stderr 报告用)。

    返回值示例:
    - `[]` — 三锁全命中
    - `["config.enabled"]` — 配置层 disabled
    - `["env_var"]` — 环境变量未设 / 非 truthy
    - `["team.coordinator"]` — team.json 无 `"coordinator": true`
    - 多项同时缺失则全部列出
    """
    missing: list[str] = []
    if config is None or config.teams is None:
        missing.append("config.teams")
        return missing
    coord_cfg = config.teams.coordinator
    if coord_cfg is None or not coord_cfg.enabled:
        missing.append("config.enabled")
    if coord_cfg is not None:
        env_var = coord_cfg.env_var or "BAOZICODE_COORDINATOR"
        env_value = os.environ.get(env_var, "").strip().lower()
        if env_value not in _TRUTHY_ENV:
            missing.append("env_var")
    if not team.coordinator:
        missing.append("team.coordinator")
    return missing