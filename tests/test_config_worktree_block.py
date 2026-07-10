"""v1.3 — `WorktreeConfig` + `SubAgentsConfig.worktree` schema 测试。

覆盖:
- `WorktreeConfig` 缺省字段生效
- `WorktreeConfig` 字段校验(越界值拒)
- `SubAgentsConfig.worktree` 缺省 None + 显式设值
- 嵌套序列化/反序列化(走 AppConfig 主路径)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from baozicode.config.schema import (
    AppConfig,
    BackendConfig,
    SubAgentsConfig,
    WorktreeConfig,
)


def _make_minimal_app_config() -> AppConfig:
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
    )


# ---------------------------------------------------------------------------
# WorktreeConfig 缺省默认
# ---------------------------------------------------------------------------


def test_worktree_config_defaults() -> None:
    """WorktreeConfig 缺省值符合 spec。"""
    cfg = WorktreeConfig()
    assert cfg.enabled is True
    assert cfg.link_paths == [".venv", "node_modules", ".cargo"]
    assert cfg.copy_paths == [
        ".baozicode/BaoZiCode.md",
        ".env",
        "config.yaml",
        ".claude/",
    ]
    assert cfg.hooks_relpath == "../_hooks/"
    assert cfg.gitignore_pattern == r"^\.worktrees/?$"
    assert cfg.max_concurrent == 5
    assert cfg.retention_minutes == 30
    assert cfg.daemon_interval_seconds == 60


def test_worktree_config_custom_values() -> None:
    """WorktreeConfig 全字段覆盖生效。"""
    cfg = WorktreeConfig(
        enabled=True,
        link_paths=[".venv", "vendor"],
        copy_paths=[".env"],
        hooks_relpath="../.githooks/",
        gitignore_pattern=r"^\.wt/?$",
        max_concurrent=3,
        retention_minutes=10,
        daemon_interval_seconds=30,
    )
    assert cfg.link_paths == [".venv", "vendor"]
    assert cfg.copy_paths == [".env"]
    assert cfg.hooks_relpath == "../.githooks/"
    assert cfg.gitignore_pattern == r"^\.wt/?$"
    assert cfg.max_concurrent == 3
    assert cfg.retention_minutes == 10
    assert cfg.daemon_interval_seconds == 30


# ---------------------------------------------------------------------------
# WorktreeConfig 字段校验
# ---------------------------------------------------------------------------


def test_max_concurrent_too_high_rejected() -> None:
    """max_concurrent 超过 20 → Pydantic 拒。"""
    with pytest.raises(ValidationError) as exc_info:
        WorktreeConfig(max_concurrent=100)
    assert "max_concurrent" in str(exc_info.value)


def test_max_concurrent_zero_rejected() -> None:
    """max_concurrent < 1 → Pydantic 拒。"""
    with pytest.raises(ValidationError):
        WorktreeConfig(max_concurrent=0)


def test_retention_minutes_too_low_rejected() -> None:
    """retention_minutes < 1 → Pydantic 拒。"""
    with pytest.raises(ValidationError):
        WorktreeConfig(retention_minutes=0)


def test_daemon_interval_too_low_rejected() -> None:
    """daemon_interval_seconds < 5 → Pydantic 拒。"""
    with pytest.raises(ValidationError):
        WorktreeConfig(daemon_interval_seconds=1)


def test_extra_fields_ignored() -> None:
    """未知字段不报错(extra=ignore)—— 向前兼容老 YAML。"""
    cfg = WorktreeConfig(extra_unknown_field=42)  # type: ignore[call-arg]
    assert cfg.enabled is True


# ---------------------------------------------------------------------------
# SubAgentsConfig.worktree 字段
# ---------------------------------------------------------------------------


def test_subagents_config_default_worktree_is_none() -> None:
    """SubAgentsConfig.worktree 缺省 None → worktree 系统不启用。"""
    cfg = SubAgentsConfig()
    assert cfg.worktree is None
    assert cfg.enabled is True
    assert cfg.max_concurrent == 5


def test_subagents_config_with_worktree_block() -> None:
    """SubAgentsConfig.worktree 显式设值 → 整块透传。"""
    wt = WorktreeConfig(retention_minutes=120, max_concurrent=10)
    cfg = SubAgentsConfig(worktree=wt)
    assert cfg.worktree is not None
    assert cfg.worktree.retention_minutes == 120
    assert cfg.worktree.max_concurrent == 10


# ---------------------------------------------------------------------------
# AppConfig 嵌套序列化
# ---------------------------------------------------------------------------


def test_app_config_with_worktree_block_serializes() -> None:
    """AppConfig.subagents.worktree 嵌套结构可序列化。"""
    app = _make_minimal_app_config()
    app.subagents = SubAgentsConfig(
        worktree=WorktreeConfig(max_concurrent=8),
    )
    assert app.subagents is not None
    assert app.subagents.worktree is not None
    assert app.subagents.worktree.max_concurrent == 8


def test_app_config_without_worktree_block_works() -> None:
    """AppConfig 不设 subagents.worktree → 整套系统不启用 worktree。"""
    app = _make_minimal_app_config()
    assert app.subagents is None
    # SubAgentsConfig 缺省 worktree=None → OK
    sa = SubAgentsConfig()
    assert sa.worktree is None