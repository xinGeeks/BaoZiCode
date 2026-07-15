## ADDED Requirements

### Requirement: Team.coordinator intent field

`Team.coordinator: bool` MUST 显式声明该 team 是否启用 coordinator 模式。

- 默认 `False`(向后兼容,旧 team.json 无字段 → False)
- `True` 必须三锁命中才真正启用(coordinator_enabled)
- `Team.__post_init__` 校验类型(bool)
- `Team.from_dict` 缺字段默认 False
- `Team.to_dict` 输出 `"coordinator": true/false`
- `TeamsRegistry.create_team(..., coordinator=False)` 接受新 kwarg

#### Scenario: Default coordinator is False
- **WHEN** `Team(name="devops", members={...})` 不传 coordinator
- **THEN** `team.coordinator is False`

#### Scenario: Load old team.json without coordinator field
- **WHEN** `Team.from_dict({"name": "devops", "members": {...}})`
- **THEN** `team.coordinator is False`(向后兼容)

#### Scenario: Explicit coordinator=True
- **WHEN** `Team(name="devops", coordinator=True, members={...})`
- **THEN** `team.coordinator is True`,`team.to_dict()["coordinator"] is True`

#### Scenario: Reject non-bool coordinator
- **WHEN** `Team(name="devops", coordinator="yes")`
- **THEN** 抛 `ValueError`

### Requirement: TeamsConfig.coordinator nested config

`TeamsConfig.coordinator: CoordinatorConfig | None` MUST 提供配置层开关 + 环境变量名 + 工具白名单。

- `CoordinatorConfig.enabled: bool = False` — 配置层开关
- `CoordinatorConfig.env_var: str = "BAOZICODE_COORDINATOR"` — 环境变量名
- `CoordinatorConfig.allowed_tools: list[str]` — 默认 Read/Grep/Glob/WebFetch + 6 个 team_*
- 整块 `coordinator:` 省略 → `None` → 视为未启用
- `AppConfig.teams.coordinator` YAML 嵌套块

#### Scenario: Default coordinator config is disabled
- **WHEN** `TeamsConfig()` 不传 coordinator
- **THEN** `TeamsConfig().coordinator is None` 或 `enabled is False`

#### Scenario: YAML loads nested coordinator
- **WHEN** YAML `teams.coordinator.enabled: true`
- **THEN** `config.teams.coordinator.enabled is True`

#### Scenario: Custom env var name
- **WHEN** `CoordinatorConfig(env_var="MY_COORD")`
- **THEN** 检查 `os.environ["MY_COORD"]` 而非 `BAOZICODE_COORDINATOR`

#### Scenario: Custom allowed_tools
- **WHEN** `CoordinatorConfig(allowed_tools=["Read", "Grep"])`
- **THEN** Coordinator Agent 仅看到这两个 + 不受白名单约束的 internal 工具

### Requirement: coordinator_enabled triple-lock gate

`coordinator_enabled(config: AppConfig, team: Team) -> bool` MUST 三锁全命中才返 True。

- 锁 1:`config.teams.coordinator.enabled is True`
- 锁 2:`os.environ[env_var]` 是 truthy(`1` / `true` / `yes`,大小写不敏感)
- 锁 3:`team.coordinator is True`
- 任一不命中 → 返 False(不报错)
- 不依赖 LLM / Agent / 工具中心

#### Scenario: All three locks set
- **WHEN** config enabled + env_var=1 + team.coordinator=True
- **THEN** `coordinator_enabled(config, team) is True`

#### Scenario: Config disabled blocks
- **WHEN** config.enabled=False(其他全命中)
- **THEN** 返 False

#### Scenario: Env var missing blocks
- **WHEN** env var 未设(其他全命中)
- **THEN** 返 False

#### Scenario: Env var empty string blocks
- **WHEN** env var="" 或 "0"
- **THEN** 返 False

#### Scenario: Team.coordinator False blocks
- **WHEN** team.coordinator=False(其他全命中)
- **THEN** 返 False

#### Scenario: Missing config block blocks
- **WHEN** `config.teams is None` 或 `coordinator is None`
- **THEN** 返 False

### Requirement: ToolRegistry.get_all_tools strips mutating tools for role='coordinator'

`ToolRegistry.get_all_tools(role='coordinator')` MUST 显式剔除写类工具,不论其 role_visibility。

- 写类工具:Write / Edit / Bash(`name in {"Write", "Edit", "Bash"}`)
- Coordinator 看到的工具 = 全部 - {Write, Edit, Bash}
- 含 load_skill + task(`tool_type="internal"`,不受 role_visibility 约束,也不受 coordinator 剔除)
- 含 Read / Grep / Glob / WebFetch(role_visibility=None 默认全员可见)
- 含全部 6 个 team_*(role_visibility 改为 ['lead', 'coordinator'])

#### Scenario: Write/Edit/Bash hidden from coordinator
- **WHEN** `get_all_tools(role='coordinator')`
- **THEN** 返列表不含 "Write" / "Edit" / "Bash"

#### Scenario: Read-only and team_* visible to coordinator
- **WHEN** `get_all_tools(role='coordinator')`
- **THEN** 含 "Read" / "Grep" / "Glob" / "WebFetch" + 6 个 team_*

#### Scenario: Internal tools always visible
- **WHEN** `get_all_tools(role='coordinator')`
- **THEN** 含 "load_skill" + "task"(tool_type='internal')

#### Scenario: Coordinator role validation
- **WHEN** `get_all_tools(role='unknown_role')`
- **THEN** 抛 ValueError(coordinator 是合法 role,其他不在 AGENT_ROLES 内也拒)

### Requirement: team_* role_visibility includes coordinator

6 个 team_* 工具的 `ToolDefinition.role_visibility` MUST 含 `'coordinator'`。

- `role_visibility: ['lead', 'coordinator']`
- 现有 `['lead']` 改为 `['lead', 'coordinator']`
- v1-4-team-tools 测试(用 `role='lead'`)继续通过
- 新增 coordinator 测试(用 `role='coordinator'`)覆盖

#### Scenario: team_dispatch visible to coordinator
- **WHEN** `get_all_tools(role='coordinator')` 已注册 team_dispatch
- **THEN** 返列表含 "team_dispatch"

#### Scenario: team_merge visible to coordinator
- **WHEN** `get_all_tools(role='coordinator')` 已注册 team_merge
- **THEN** 返列表含 "team_merge"

### Requirement: App.use_team accepts coordinator kwarg

`BaoZiCodeApp.use_team(name: str, *, coordinator: bool = False)` MUST 接受 keyword-only coordinator 参数。

- `coordinator=False`(默认)→ 现有 Lead 路径,role='lead' + 13 工具
- `coordinator=True` → 三锁命中才 `active_role='coordinator'` + 工具白名单;
  不命中 → 降级到 Lead 路径 + stderr 报告哪一锁缺失
- 新增 `App.active_role: str = "subagent"` 字段,默认 "subagent"
- 新增 `App.active_coordinator: bool = False` 字段
- `use_team` 同步设 `active_team_name` + `mailbox_notifier`

#### Scenario: use_team without coordinator kwarg
- **WHEN** `app.use_team("devops")`
- **THEN** `app.active_role == "lead"`,`app.active_coordinator is False`

#### Scenario: use_team with coordinator=True, all locks hit
- **WHEN** 三锁全命中 + `app.use_team("devops", coordinator=True)`
- **THEN** `app.active_role == "coordinator"`,`app.active_coordinator is True`

#### Scenario: use_team with coordinator=True, lock missing
- **WHEN** env var 未设 + `app.use_team("devops", coordinator=True)`
- **THEN** `app.active_role == "lead"`,`app.active_coordinator is False`,stderr 报告

#### Scenario: Team not found
- **WHEN** `app.use_team("ghost")`
- **THEN** 抛 ValueError

#### Scenario: Teams disabled
- **WHEN** `app.teams is None` + `app.use_team("devops")`
- **THEN** 抛 ValueError("team system 未启用")

### Requirement: ChatScreen reconstructs Agent with active_role

`ChatScreen` 重建 Agent 时 MUST 读 `app.active_role` 决定 role。

- `app.active_role == "coordinator"` → `Agent(role="coordinator")` +
  `available_tools=get_all_tools(role="coordinator")`
- `app.active_role == "lead"` → 现有路径
- `app.active_role == "subagent"`(默认)→ 现有路径

#### Scenario: ChatScreen uses active_role
- **WHEN** `app.active_role == "coordinator"` + ChatScreen 重建 Agent
- **THEN** `Agent(... role="coordinator", available_tools=coordinator_whitelist)`

#### Scenario: ChatScreen falls back to subagent when no team
- **WHEN** `app.active_role == "subagent"`(默认)
- **THEN** `Agent(role="subagent", available_tools=7_builtin)`

### Requirement: baozicode team use --coordinator flag

`baozicode team use <name> --coordinator` MUST 扩展现有 use 子命令,接受 --coordinator flag。

- `--coordinator` 是 store_true flag
- 不传 → 现有路径(等价 `team use <name>`)
- 传 → 走 coordinator 路径,三锁检查 + 降级报告
- 退出码:0(成功)/ 3(team 不存在)/ 5(config 错误)

#### Scenario: --coordinator flag registered
- **WHEN** `parser.parse_args(["team", "use", "devops", "--coordinator"])`
- **THEN** `args.coordinator is True`

#### Scenario: --coordinator absent defaults False
- **WHEN** `parser.parse_args(["team", "use", "devops"])`
- **THEN** `args.coordinator is False`

#### Scenario: Lock mismatch prints which lock failed
- **WHEN** 三锁不全命中 + `team use --coordinator devops`
- **THEN** stderr 输出 "coordinator 三锁未命中 (缺: env_var, team.coordinator);降级到 Lead 模式"

#### Scenario: Team not found exits 3
- **WHEN** `team use --coordinator ghost`
- **THEN** stderr "TeamNotFound: team 'ghost' 不存在" + exit 3

### Requirement: Coordinator reads member outbox via Read tool

Coordinator Agent 看到的 Read tool MUST 能读 `<teams_dir>/<team>/<member>/outbox.jsonl`。

- 不引入新工具
- v0.5 L2 PathSandbox 白名单覆盖 teams 路径(v1-4-pane-backend 已加)
- Coordinator 自然能扫 member 进度

#### Scenario: Read tool reads outbox path
- **WHEN** Coordinator Agent 调 Read 读 `<teams_dir>/<team>/alice/outbox.jsonl`
- **THEN** 返回文件内容(member 写的消息)

#### Scenario: PathSandbox allows teams paths
- **WHEN** Read 调用 path = `<teams_dir>/<team>/alice/outbox.jsonl`
- **THEN** 不被 L2 沙箱拒(`real_root` 包含 teams 路径)