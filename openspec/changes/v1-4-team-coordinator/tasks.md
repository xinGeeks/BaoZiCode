# v1-4-team-coordinator — Tasks

## 0. 决策锁定(已完成 — explore 阶段)

- ✅ Activation:扩展 `team use --coordinator <name>`
- ✅ Whitelist:Read/Grep/Glob/WebFetch + 全部 6 个 team_*
- ✅ Scope:team.json `coordinator: true` + 配置 + 环境变量三锁
- ✅ Visibility:Coordinator 直接 Read outbox.jsonl

## 1. 提案 / 设计 / Spec / Tasks 骨架

- [x] 1.1 创建 `openspec/changes/v1-4-team-coordinator/` 目录(骨架已就
- [x] 1.2 写 `proposal.md`(Why / What Changes / Out of Scope / 4 锁定决策)
- [x] 1.3 写 `design.md`(三锁门 / role_visibility / App 接线 / CLI 扩展
      / Read 读 outbox / Schema / 测试覆盖 / 文档同步)
- [x] 1.4 写 `specs/team-management/spec.md` delta(9 个新 Requirement:
      Team.coordinator / TeamsConfig.coordinator / coordinator_enabled /
      ToolRegistry coordinator 剔除 / team_* role_visibility /
      App.use_team kwarg / ChatScreen active_role / team use --coordinator
      flag / Coordinator 读 outbox)
- [x] 1.5 写 `tasks.md`(本文档 — 8 阶段分解)
- [x] 1.6 `openspec validate v1-4-team-coordinator --strict` 通过

## 2. Schema 改动(Team.coordinator + TeamsConfig.coordinator)

- [x] 2.1 `baozicode/teams/schema.py` — `Team` dataclass 加
      `coordinator: bool = False` 字段 + `__post_init__` 校验 bool +
      `to_dict` 输出 + `from_dict` 缺字段默认 False
- [x] 2.2 `baozicode/config/schema.py` — 新增
      `CoordinatorConfig(BaseModel)`:`enabled: bool = False` /
      `env_var: str = "BAOZICODE_COORDINATOR"` /
      `allowed_tools: list[str] = Field(default_factory=...)`
- [x] 2.3 `baozicode/config/schema.py` — `TeamsConfig` 加
      `coordinator: CoordinatorConfig | None = None`
- [x] 2.4 `baozicode/teams/registry.py` — `TeamsRegistry.create_team` 接
      `coordinator=False` kwarg → 构造 Team 时传
- [x] 2.5 测试 `tests/test_teams_v14_team_coordinator_field.py` ~5 个:
      default False / from_dict 缺字段 False / to_dict 输出 /
      create_team coordinator=True / 非 bool 拒
      (**实际 25 个通过**)

## 3. coordinator_enabled 三锁门

- [x] 3.1 `baozicode/teams/coordinator.py`(新)— `coordinator_enabled
      (config: AppConfig, team: Team) -> bool` 三锁门 +
      `check_coordinator_locks(config, team) -> list[str]` 返缺失锁列表
      (给 stderr 报告用)
- [x] 3.2 `baozicode/teams/__init__.py` 公开 API re-export
- [x] 3.3 测试 `tests/test_teams_v14_coordinator_gate.py` ~8 个:
      三锁全命中 True / 任一缺失 False / env var 大小写不敏感 /
      空字符串 / "0" 视为未设 / config disabled / team.coordinator False
      / 缺 config block
      (**实际 27 个通过**)

## 4. ToolRegistry.get_all_tools coordinator 角色过滤

- [x] 4.1 `baozicode/tools/registry.py` — `get_all_tools(role='coordinator')`
      分支:显式剔除 Write/Edit/Bash(写类工具);internal 工具不受
      剔除(load_skill / task 仍可见)
- [x] 4.2 `baozicode/teams/tools.py` — 6 个 team_* 的 role_visibility 从
      `['lead']` 改为 `['lead', 'coordinator']`
- [x] 4.3 测试 `tests/test_tools_role_coordinator.py` ~6 个:
      Write/Edit/Bash 不出现在 coordinator 列表 /
      Read/Grep/Glob/WebFetch 出现 / 6 个 team_* 出现 /
      load_skill / task 出现(internal)/
      role='coordinator' 通过 AGENT_ROLES 校验
      (**实际 22 个通过**)

## 5. App.use_team coordinator kwarg

- [x] 5.1 `baozicode/app.py` — `__init__` 加 `self.active_role: str =
      "subagent"` + `self.active_coordinator: bool = False` 字段
- [x] 5.2 `baozicode/app.py` — `use_team(name, *, coordinator=False)`
      keyword-only 参数;三锁检查 + 降级 + stderr 报告
- [x] 5.3 测试 `tests/test_teams_v14_app_coordinator.py` ~5 个:
      coordinator=False 默认 Lead 路径 /
      coordinator=True 三锁命中走 coordinator /
      coordinator=True 锁缺失降级 Lead + stderr 报告 /
      team 不存在 ValueError / teams disabled ValueError
      (**实际 9 个通过**)

## 6. ChatScreen active_role 接线

- [x] 6.1 `baozicode/tui/chat_screen.py` — Agent 重建处读
      `app.active_role` 决定 `role` kwarg + `available_tools` 走
      `get_all_tools(role=app.active_role)`
- [x] 6.2 测试 `tests/test_tui_chat_screen_coordinator.py` ~4 个:
      active_role='coordinator' → Agent(role='coordinator') /
      active_role='lead' → Agent(role='lead') /
      active_role='subagent' → Agent(role='subagent') /
      ChatScreen 重建时调 set_active_role
      (**实际 9 个通过**)

## 7. CLI `team use --coordinator` 扩展

- [x] 7.1 `baozicode/teams/cli.py` — `_cmd_use` 接受 args.coordinator
      flag;三锁检查 + 降级 + stderr 报告 + 退出码处理
- [x] 7.2 `baozicode/teams/cli.py` — `add_subcommand` 给 `use` 子命令
      加 `--coordinator` flag
- [x] 7.3 测试 `tests/test_teams_v14_team_use_coordinator.py` ~5 个:
      argparse --coordinator 解析 /
      三锁全命中 → 走 coordinator /
      锁缺失 → 降级 + stderr 报告具体缺哪个 /
      team 不存在 exit 3 /
      config 错误 exit 5
      (**实际 10 个通过**)

## 8. 集成 + 文档 + Release

- [x] 8.1 集成 `tests/integration/test_team_coordinator_e2e.py` ~5 个:
      端到端 CLI `team use --coordinator` →
      app.active_role='coordinator' → Agent(role='coordinator',
      available_tools 白名单) → Read 读 outbox.jsonl 成功
      (**实际 5 个通过**)
- [x] 8.2 `CHANGELOG.md` 增 `v1.4.0-coordinator` 段
- [x] 8.3 项目根 `CLAUDE.md` "v1.X 范围"段加 `coordinator` 子段 +
      模块结构段加 `coordinator.py`
- [x] 8.4 `README.md` 加 "Coordinator Mode" 段
- [x] 8.5 `docs/migrations/v1.4-pane-backend-to-v1.4-coordinator.md`(新)
- [x] 8.6 `config.example.yaml` 加 `teams.coordinator:` 配置示例
- [x] 8.7 全量 `pytest tests/ -v` 通过(预计 +25 个新测试,~487 个 v1.4)
      (**实际 +107 个新测试,561 个通过,5 个 pre-existing failure**)
- [x] 8.8 `openspec validate v1-4-team-coordinator --strict` 通过
- [ ] 8.9 `git commit -m "feat(v1.4-coordinator): 三锁门 + ToolRegistry
      coordinator 角色过滤 + team_* role_visibility 扩展 + App
      use_team kwarg + ChatScreen active_role + CLI --coordinator"`
- [ ] 8.10 `openspec archive v1-4-team-coordinator`(specs 合并到
      `openspec/specs/team-management/`)