"""v1.4 Team Foundation — TeamsRegistry 全局索引。

公开 API:

- `TeamsRegistry.bootstrap(config: AppConfig) -> TeamsRegistry` —
  从 `TeamsConfig.dir` 建索引,扫所有 `<team>/team.json`
- `registry.teams_dir` — 索引目录(展开 ~ 后)
- `registry.list_teams() -> list[str]` — 字典序列出所有 team 名
- `registry.get(name) -> TeamStore | None` — 查 team,找不到 None
- `registry.create_team(name, *, lead="lead") -> TeamStore` —
  调 `TeamStore.create`(唯一性 + 原子)
- `registry.delete_team(name, *, confirm=False) -> None` — 调
  `TeamStore.destroy`

设计保证:

- `bootstrap` 时 `teams_dir.mkdir(parents=True, exist_ok=True)` —
  首次启动自动建目录(零手动干预)。
- `list_teams` 字典序,跟 `ls` 一致。
- `create_team` / `delete_team` 单一入口,LLM 不会绕过 Registry 直接
  调 TeamStore(虽然 TeamStore 本身也可用)。
"""

from __future__ import annotations

from pathlib import Path

from .schema import TeamNameValidator
from .store import TeamStore


class TeamsRegistry:
    """全局 team 索引 — 持有 `teams_dir`,提供 list/get/create/delete。

    由 `BaoZiCodeApp._build_teams_registry` 在 on_mount 阶段 bootstrap
    创建,挂在 `app.teams: TeamsRegistry`。
    """

    def __init__(self, teams_dir: Path) -> None:
        self.teams_dir = teams_dir

    @classmethod
    def bootstrap(cls, config) -> TeamsRegistry:
        """从 AppConfig 建 registry。

        Args:
            config: `AppConfig`(读 `config.teams.dir`,缺省走默认)

        Returns:
            TeamsRegistry 实例
        """
        teams_cfg = getattr(config, "teams", None)
        if teams_cfg is None:
            raw_dir = "~/.config/baozicode/teams/"
        else:
            raw_dir = teams_cfg.dir
        teams_dir = Path(raw_dir).expanduser()
        teams_dir.mkdir(parents=True, exist_ok=True)
        return cls(teams_dir)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def list_teams(self) -> list[str]:
        """字典序列出所有 team 名(<teams_dir>/<team>/team.json 存在)。"""
        names: list[str] = []
        if not self.teams_dir.exists():
            return names
        for entry in sorted(self.teams_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / "team.json").exists():
                continue
            names.append(entry.name)
        return names

    def get(self, name: str) -> TeamStore | None:
        """查 team;找不到返回 None(不抛)。"""
        team_dir = self.teams_dir / name
        if not team_dir.exists() or not (team_dir / "team.json").exists():
            return None
        return TeamStore.load(team_dir)

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def create_team(self, name: str, *, lead: str = "lead") -> TeamStore:
        """创建新 team(走 TeamStore.create,带唯一性约束)。"""
        return TeamStore.create(self.teams_dir, name, lead=lead)

    def delete_team(self, name: str, *, confirm: bool = False) -> None:
        """删除 team(走 TeamStore.destroy,需 confirm=True)。"""
        store = self.get(name)
        if store is None:
            from .schema import TeamNotFound

            raise TeamNotFound(f"team {name!r} 不存在")
        store.destroy(confirm=confirm)


__all__ = ["TeamsRegistry"]