"""v1.4 Team Foundation — TeamsConfig 配置 schema 测试。

覆盖 `openspec/changes/v1-4-team-foundation/specs/configuration/spec.md`
delta acceptance scenario:

- TeamsConfig 默认 dir
- 自定义 dir 覆盖
- AppConfig.teams = None 时 config.teams 仍可读为 None
- YAML 加载 `teams:` 块能成功
- config.example.yaml 中的 `teams:` 段能解析
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from baozicode.config.loader import load_config
from baozicode.config.schema import AppConfig, TeamsConfig


# ---------------------------------------------------------------------------
# TeamsConfig 独立构造
# ---------------------------------------------------------------------------


class TestTeamsConfigDefaults:
    def test_default_enabled(self) -> None:
        cfg = TeamsConfig()
        assert cfg.enabled is True

    def test_default_dir(self) -> None:
        cfg = TeamsConfig()
        # ~/.config/baozicode/teams/ 展开后
        assert cfg.dir == Path("~/.config/baozicode/teams/").expanduser()
        assert cfg.dir.name == "teams"
        assert cfg.dir.parent.name == "baozicode"

    def test_extra_fields_ignored(self) -> None:
        # 未来 proposal(coordinator / pane_backend)加字段时,旧 config 不挂
        cfg = TeamsConfig.model_validate(
            {"enabled": True, "dir": "/tmp/x", "future_field": "ignore_me"}
        )
        assert cfg.dir == Path("/tmp/x")
        assert not hasattr(cfg, "future_field")


class TestTeamsConfigCustom:
    def test_custom_dir(self) -> None:
        cfg = TeamsConfig(dir="/tmp/my-teams")
        assert cfg.dir == Path("/tmp/my-teams")

    def test_custom_dir_tilde_expansion_at_construction(self) -> None:
        # Pydantic Field default_factory 跑 expanduser,
        # 但显式传 dir 时不会自动展开 —— 留给 loader / bootstrap 处理
        cfg = TeamsConfig(dir="~/myteams")
        assert cfg.dir == Path("~/myteams")  # 未展开,只是 literal

    def test_enabled_false(self) -> None:
        cfg = TeamsConfig(enabled=False)
        assert cfg.enabled is False


# ---------------------------------------------------------------------------
# AppConfig.teams 字段
# ---------------------------------------------------------------------------


def _minimal_app_config(**overrides) -> dict:
    """构造最小可加载的 AppConfig 字典(backend 必填 4 个)。"""
    base = {
        "backend": "anthropic",
        "anthropic": {"api_key": "k", "model": "m"},
        "openai": {"api_key": "k", "model": "m"},
        "minimax": {"api_key": "k", "model": "m"},
        "deepseek": {"api_key": "k", "model": "m"},
    }
    base.update(overrides)
    return base


class TestAppConfigTeamsField:
    def test_default_none(self) -> None:
        cfg = AppConfig.model_validate(_minimal_app_config())
        assert cfg.teams is None

    def test_teams_block_loaded(self) -> None:
        cfg = AppConfig.model_validate(
            _minimal_app_config(teams={"enabled": True, "dir": "/tmp/t"})
        )
        assert cfg.teams is not None
        assert cfg.teams.enabled is True
        assert cfg.teams.dir == Path("/tmp/t")

    def test_teams_disabled(self) -> None:
        cfg = AppConfig.model_validate(
            _minimal_app_config(teams={"enabled": False})
        )
        assert cfg.teams is not None
        assert cfg.teams.enabled is False
        # dir 走默认
        assert cfg.teams.dir.name == "teams"


# ---------------------------------------------------------------------------
# YAML 加载端到端
# ---------------------------------------------------------------------------


class TestYamlLoading:
    def test_minimal_yaml_with_teams(self, tmp_path: Path) -> None:
        yaml_text = """
backend: anthropic
anthropic:
  api_key: k
  model: m
openai:
  api_key: k
  model: m
minimax:
  api_key: k
  model: m
deepseek:
  api_key: k
  model: m
teams:
  enabled: true
  dir: /tmp/custom-teams
"""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml_text, encoding="utf-8")

        cfg = load_config(str(cfg_path))
        assert cfg.teams is not None
        assert cfg.teams.enabled is True
        assert cfg.teams.dir == Path("/tmp/custom-teams")

    def test_yaml_without_teams_block(self, tmp_path: Path) -> None:
        yaml_text = """
backend: anthropic
anthropic:
  api_key: k
  model: m
openai:
  api_key: k
  model: m
minimax:
  api_key: k
  model: m
deepseek:
  api_key: k
  model: m
"""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml_text, encoding="utf-8")

        cfg = load_config(str(cfg_path))
        # 不写 teams → config.teams = None(由 AppConfig 默认处理)
        assert cfg.teams is None

    def test_config_example_yaml_parses(self) -> None:
        """config.example.yaml 应包含合法 `teams:` 段并能解析。"""
        example_path = Path("config.example.yaml")
        assert example_path.exists(), "config.example.yaml 不存在"

        data = yaml.safe_load(example_path.read_text(encoding="utf-8"))
        assert "teams" in data, "config.example.yaml 缺少 teams: 段"

        teams_data = data["teams"]
        assert "enabled" in teams_data
        assert "dir" in teams_data

        # 解析为 TeamsConfig 不应抛
        teams_cfg = TeamsConfig.model_validate(teams_data)
        assert teams_cfg.enabled is True
        assert "~/.config/baozicode/teams/" in str(teams_cfg.dir) or teams_cfg.dir.name == "teams"