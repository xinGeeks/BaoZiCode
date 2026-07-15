# configuration Specification — v1.4 ADDED Requirements

> This is the v1.4 foundation delta on top of existing configuration
> capability. v1.3 `WorktreeConfig` / `SubAgentsConfig` 字段 +
> `app.py` 装配流程 **不变**。v1.4 foundation 在 `AppConfig` 顶层新
> 增 `teams: TeamsConfig | None` 块。

## ADDED Requirements

### Requirement: TeamsConfig schema (NEW)

`AppConfig` MUST expose an optional top-level field
`teams: TeamsConfig | None` in v1.4 foundation. The system MUST use the
following Pydantic model definition for `TeamsConfig`:

```python
class TeamsConfig(BaseModel):
    dir: str = "~/.config/baozicode/teams/"
```

加载时 `~` MUST 展开为 `Path.home()`;不合法值(空字符串)Pydantic 走标
准字符串校验(`min_length=1`)。

`AppConfig.teams = None` MUST 走 bootstrap 默认(等价于
`TeamsConfig()` 全默认对象)。

`BaoZiCodeApp._build_teams_registry()` MUST 在 `on_mount` 末尾调
`TeamsRegistry.bootstrap(config)`,挂 `self.teams: TeamsRegistry` 句柄,
**位置在 `permissions → hooks → instructions → memory → sessions →
commands → skills → teams`** 之后(v1.4 foundation 是新加的最末一步)。

#### Scenario: Default teams_dir
- **WHEN** `AppConfig()` 不传 `teams`
- **THEN** `config.teams` 是 `TeamsConfig(dir="~/.config/baozicode/teams/")`
  默认实例(走 Pydantic 字段默认值)

#### Scenario: Custom teams_dir
- **WHEN** YAML 含 `teams.dir: /var/baozicode/teams/`
- **THEN** `AppConfig.teams.dir == "/var/baozicode/teams/"`

#### Scenario: Tilde expansion
- **WHEN** `TeamsConfig(dir="~/custom/teams/")`
- **THEN** `TeamsRegistry.bootstrap(config).teams_dir ==
  Path.home() / "custom/teams/"`(读时展开,不写时改)

#### Scenario: Empty dir rejected
- **WHEN** `TeamsConfig(dir="")`
- **THEN** Pydantic `ValidationError`(string_too_short)

#### Scenario: App bootstraps registry
- **WHEN** `BaoZiCodeApp.on_mount()` 跑完所有 bootstrap 步骤
- **THEN** `self.teams is TeamsRegistry` +
  `self.teams.teams_dir == Path("~/.config/baozicode/teams/").expanduser()`

#### Scenario: Out of scope (deferred)
- **WHEN** 用户配置 `teams.coordinator:` / `teams.pane_backend:` 等子块
- **THEN** 加载时 Pydantic `extra="ignore"`(留给后续 proposal 加,
  v1.4 foundation 不实现)

### Requirement: config.example.yaml update for teams (NEW)

`config.example.yaml` MUST contain an inline, documented example block
for `teams:` in v1.4 foundation:

```yaml
teams:
  # 团队目录:所有 team / mailbox / shared tasks 持久化位置
  # 默认 ~/.config/baozicode/teams/,跨项目共享
  dir: ~/.config/baozicode/teams/

  # 后续 v1-4-team-coordinator / v1-4-team-pane-backend proposal 会加:
  # coordinator:
  #   enabled: false
  # pane_backend:
  #   priority: [pane-tmux, pane-iterm2, pane-windows-terminal, coroutine]
```

#### Scenario: User copies example to config.yaml
- **WHEN** 用户从 `config.example.yaml` 复制 `teams:` 段到自己 `config.yaml`
- **THEN** `TeamsConfig` 加载该段成功 +
  `BaoZiCodeApp.on_mount()` 末尾 bootstrap 出 `self.teams` 句柄

## MODIFIED Requirements

无。v1.3 foundation 的 `WorktreeConfig` / `SubAgentsConfig` /
`AppConfig.hooks` / `MemoryConfig` / `SessionsConfig` 等所有现有字段语
义不变;v1.4 foundation 只在 `AppConfig` 顶层新增可选 `teams` 子键。

## REMOVED Requirements

无。