# configuration Specification — v1.3 ADDED Requirements

> This is the v1.3 delta on top of existing configuration capability.
> v1.2 `SubAgentsConfig` 字段 + 三层 YAML 加载逻辑 **不变**。v1.3
> 在 `SubAgentsConfig` 内嵌 `WorktreeConfig` 子配置。

## ADDED Requirements

### Requirement: WorktreeConfig schema (NEW)

`SubAgentsConfig` MUST expose an optional nested field
`worktree: WorktreeConfig | None` in v1.3. The system MUST use the
following Pydantic model definition for `WorktreeConfig`:

```python
class WorktreeConfig(BaseModel):
    enabled: bool = True  # 总开关(冗余,real decision 来自 frontmatter isolation)
    link_paths: list[str] = [".venv", "node_modules", ".cargo"]
    copy_paths: list[str] = [
        ".baozicode/BaoZiCode.md",
        ".env",
        "config.yaml",
        ".claude/",
    ]
    retention_minutes: int = 60
    daemon_interval_seconds: int = 60
    max_concurrent_worktrees: int = 5
```

#### Scenario: SubAgentsConfig with worktree block
- **WHEN** YAML 含 `subagents.worktree.link_paths: [.venv,
  custom_lib]`(覆盖默认)
- **THEN** `AppConfig.subagents.worktree.link_paths == [".venv",
  "custom_lib"]`(覆盖 Pydantic 默认 list)

#### Scenario: SubAgentsConfig without worktree block
- **WHEN** YAML 没 `subagents.worktree:` 子键
- **THEN** `AppConfig.subagents.worktree is None`;bootstrap 路
  径用 `WorktreeConfig()` 全默认对象兜底

#### Scenario: Pydantic validates bad types
- **WHEN** YAML 含 `subagents.worktree.retention_minutes: -5`
- **THEN** `AppConfig` 加载报错 Pydantic `greater_than_equal
  错误(retention_minutes 必须 ≥ 0)`

#### Scenario: Max concurrent cap enforced
- **WHEN** YAML 含 `subagents.worktree.max_concurrent_worktrees:
  100`
- **THEN** `WorktreeManager` 创建第 `max_concurrent_worktrees +
  1` 个 worktree 时,**默认**仍允许(只 warn + 继续);worker
  count 可通过 `/status` 看到

### Requirement: config.example.yaml update (NEW)

`config.example.yaml` MUST contain an inline, documented example block
for `subagents.worktree` in v1.3:

```yaml
subagents:
  enabled: true
  max_concurrent: 5
  default_timeout_seconds: 300
  task_retention_minutes: 5
  plugins_enabled: true
  background_whitelist: [Read, Grep, Glob, WebFetch, notify_complete]

  # v1.3 嵌套块 — Worktree Isolation 配置
  worktree:
    enabled: true
    link_paths:
      - .venv
      - node_modules
      - .cargo
    copy_paths:
      - .baozicode/BaoZiCode.md
      - .env
      - config.yaml
      - .claude/
    retention_minutes: 60
    daemon_interval_seconds: 60
    max_concurrent_worktrees: 5
```

#### Scenario: User copies example to config.yaml
- **WHEN** 用户从 `config.example.yaml` 复制 `subagents.worktree`
  段到自己 `config.yaml`
- **THEN** `WorktreeConfig` 加载该段成功 + `WorktreeInitializer`
  按 `link_paths` / `copy_paths` 初始化 worktree

## MODIFIED Requirements

无(v1.2 `SubAgentsConfig` 自身字段语义不变;只新增可选 `worktree` 子
键)。

## REMOVED Requirements

无。
