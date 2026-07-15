# v1-4-team-coordinator — Design

## 1. 三锁门(配置 + 环境变量 + team.json)

```python
def coordinator_enabled(config: AppConfig, team: Team) -> bool:
    """三锁全命中才返 True。"""
    coord_cfg = config.teams.coordinator if config.teams else None
    if coord_cfg is None or not coord_cfg.enabled:
        return False
    env_var = coord_cfg.env_var or "BAOZICODE_COORDINATOR"
    if os.environ.get(env_var, "").lower() not in ("1", "true", "yes"):
        return False
    if not team.coordinator:
        return False
    return True
```

**锁 1 — 配置层**:`teams.coordinator.enabled` 在 YAML 配置里显式设 true
(默认 False,向后兼容)。整块 `coordinator:` 省略也视为 False。

**锁 2 — 环境变量**:`os.environ["BAOZICODE_COORDINATOR"]` 必须 truthy
(`1` / `true` / `yes`,大小写不敏感)。默认名 `BAOZICODE_COORDINATOR`,
配置 `env_var` 可覆盖。

**锁 3 — team 意图**:team.json 含 `"coordinator": true`。默认 False,
用户显式打开。持久化 — 重启后意图保留。

**为什么三锁而非两锁**:
- 配置文件可能被共享 / 上 git,设错影响大
- 环境变量由运维控制(生产部署 / 测试环境)
- team.json 是用户对单个 team 的明确意图
- 三者全部命中 = 显式 ≥ 3 处都做了「我确实想启用 coordinator」

**降级**:三锁任一不命中 → 走现有 Lead 路径(`role='lead'` + 13 工具),
不报错。`team use --coordinator` 命令行工具会显式报告「哪一锁缺失」。

## 2. 角色与工具白名单

`AGENT_ROLES` 在 v1-4-team-tools 已含 `coordinator`,无需扩展。

### 7 个内置工具的 role_visibility 调整

| 工具 | 当前 role_visibility | 调整后 |
|------|---------------------|--------|
| Read | None | `['subagent', 'lead']` |
| Write | None | `['subagent', 'lead']` |
| Edit | None | `['subagent', 'lead']` |
| Bash | None | `['subagent', 'lead']` |
| Grep | None | `['subagent', 'lead']` |
| Glob | None | `['subagent', 'lead']` |
| WebFetch | None | `['subagent', 'lead']` |
| load_skill(internal) | None | 保持 None(`internal` 不受白名单约束) |
| task(internal) | None | 保持 None |

`role='member'`(v1-4-pane-backend)已经走 7 工具子集单独构造,不受影响。
`role='subagent'`(默认,无 active team)照常用。
`role='lead'`(active team)全部 13 工具照常用。
`role='coordinator'`(新)只看到:
  - `role_visibility is None AND tool_type != 'external-mutating'`
  - 简化: `role_visibility 含 'coordinator'`

但把 7 个内置工具设为 `['subagent', 'lead']` 会让 `role='subagent'`
也看不到它们 —— 错了。修正:
- 默认(`role_visibility=None`)保持全员可见(向后兼容)
- 新增 `role='coordinator'` 过滤:在 `ToolRegistry.get_all_tools(role='coordinator')`
  里**显式剔除** `Write` / `Edit` / `Bash`(写类工具),保留其余
- 不改 `role_visibility` 字段,改 `ToolRegistry.get_all_tools` 的
  `role='coordinator'` 分支

### 6 个 team_* 工具保持 `role_visibility=['lead']`

Coordinator 也要能看到这 6 个工具 — 改成 `['lead', 'coordinator']`。
测试需要兜底:`register_team_tools` 注册时 `role='coordinator'` Agent
能调到 `team_dispatch`。

## 3. App 接线

```python
def use_team(self, name: str, *, coordinator: bool = False) -> None:
    """激活 team — 现有路径 + 新增 coordinator 模式。

    Args:
        name: team 名
        coordinator: True → 三锁命中后 role='coordinator' +
            白名单工具;False → 现有 Lead 路径。
            不命中 → 走 Lead 路径(降级,不报错)。
    """
    if self.teams is None:
        raise ValueError("team system 未启用")
    store = self.teams.get(name)
    if store is None:
        raise ValueError(f"team {name!r} 不存在")
    team = store.show()

    if coordinator:
        enabled = coordinator_enabled(self.config, team)
        if not enabled:
            # 报告哪一锁缺失,降级到 Lead
            self._report_coordinator_lock_mismatch(team)
            self.active_role = "lead"
            self.active_coordinator = False
        else:
            self.active_role = "coordinator"
            self.active_coordinator = True
    else:
        self.active_role = "lead"
        self.active_coordinator = False

    self.active_team_name = name
    self.mailbox_notifier = MailboxNotifier(self.teams, name)
```

新增 `App.active_role` 和 `App.active_coordinator` 字段。`ChatScreen`
重建 Agent 时读这两个字段:
- `active_role == 'coordinator'` → Agent(role='coordinator',
  available_tools=`coordinator whitelist`)
- 其他 → 现有路径

## 4. CLI 扩展

`baozicode team use --coordinator <name>` 在 v1-4-team-foundation 阶段
的 `_cmd_use` 加分支:

```python
def _cmd_use(args):
    reg = _build_registry(args)
    store = reg.get(args.name)
    if store is None:
        print(f"Error: TeamNotFound: team {args.name!r} 不存在")
        return EXIT_NOT_FOUND
    team = store.show()
    member_count = len(store.list_members())

    if args.coordinator:
        # 三锁检查
        try:
            config = load_config(getattr(args, "config", None))
        except ConfigError as exc:
            print(f"Error: ConfigError: {exc}")
            return EXIT_CONFIG
        if not coordinator_enabled(config, team):
            missing = _check_coordinator_locks(config, team)
            print(
                f"Note: coordinator 三锁未命中 (缺: {missing});"
                f"降级到 Lead 模式。",
                file=sys.stderr,
            )

    # 通知 App(use_team 在 ChatScreen mount 时由 runtime 调用,
    # CLI 这一层仅 print)
    print(
        f"Activated team {args.name!r} ({member_count} members, "
        f"lead={team.lead}, coordinator={args.coordinator})"
    )
```

实际 `app.use_team` 调用在 TUI 启动后 ChatScreen.on_mount 通过
`/session use` slash 命令触发,不是 CLI 子命令。CLI 这一层只 print。

## 5. Read tool 读 outbox.jsonl

Coordinator Agent 看到 Read tool 即可。`<teams_dir>/<team>/<member>/outbox.jsonl`
路径在 L2 PathSandbox 白名单内(v1.3 已有 worktree 路径白名单机制,
v1.4-pane-backend 已加 `teams/` 路径)。

不需要新工具,不需要新权限。

## 6. Schema 改动

### Team 加 `coordinator: bool = False` 字段

```python
@dataclass(frozen=True)
class Team:
    name: str
    lead: str = "lead"
    created_at: datetime = ...
    members: dict[str, Member]
    metadata: dict[str, Any]
    coordinator: bool = False  # NEW

    def __post_init__(self):
        TeamNameValidator.validate(self.name)
        ...
        if not isinstance(self.coordinator, bool):
            raise ValueError(f"Team.coordinator 必须是 bool,得到 {type(self.coordinator).__name__}")
```

向后兼容:旧 team.json 无 `coordinator` 字段 → `from_dict` 默认 False。

### TeamsConfig 加 `coordinator: CoordinatorConfig | None` 字段

```python
class CoordinatorConfig(BaseModel):
    enabled: bool = False
    env_var: str = "BAOZICODE_COORDINATOR"
    allowed_tools: list[str] = Field(default_factory=lambda: [
        "Read", "Grep", "Glob", "WebFetch",
        "team_dispatch", "team_send_message", "team_cancel",
        "team_merge", "team_task_create", "team_task_query",
    ])
```

### AppConfig.teams 嵌套 `coordinator` 子块

```yaml
teams:
  enabled: true
  dir: ~/.config/baozicode/teams/
  coordinator:
    enabled: true
    env_var: BAOZICODE_COORDINATOR
    allowed_tools:
      - Read
      - ...
```

## 7. 失败模式

| 场景 | 行为 |
|------|------|
| `team use --coordinator foo`,配置 disabled | 降级 Lead,stderr 报告 |
| `team use --coordinator foo`,env var 未设 | 降级 Lead,stderr 报告 |
| `team use --coordinator foo`,team.json 无 `coordinator:true` | 降级 Lead,stderr 报告 |
| 三锁全命中,`Write` 工具被 LLM 调 | `ToolRegistry.get_all_tools(role='coordinator')` 不含 Write → LLM 看不到 → 不发生 |
| Coordinator 绕过白名单直接用 Bash | L5 modal 弹?—— 不,v0.5 五层防御默认给 Bash pass through;但 `get_all_tools(role='coordinator')` 不返 Bash 给 LLM |
| 配置文件解析失败 | ConfigError → exit 5 |

## 8. 不引入新过滤层

- `ToolRegistry.get_all_tools(role='coordinator')` 已经存在(v1-4-team-tools)
- `Agent(role='coordinator')` keyword-only 参数已存在
- `ToolDefinition.role_visibility` 校验已含 `coordinator`
- `AGENT_ROLES` 已含 `coordinator`

唯一新加:
1. `Team.coordinator: bool` 字段
2. `TeamsConfig.coordinator: CoordinatorConfig` 字段
3. `coordinator_enabled(config, team)` 三锁判断函数
4. `App.use_team(..., coordinator=False)` 新 keyword-only 参数
5. `App.active_role` / `App.active_coordinator` 字段
6. ChatScreen 重建 Agent 时读 `active_role` 决定 role
7. `team_*` 工具 `role_visibility` 从 `['lead']` → `['lead', 'coordinator']`
8. ToolRegistry.get_all_tools 在 `role='coordinator'` 时显式剔除
   Write/Edit/Bash(不通过 `role_visibility`,而是通过 `tool_type` 标签)
9. CLI `team use --coordinator` 扩展

### 备选方案(考虑过,未采纳)

- **Bash 改用工具名级别 allowlist**——在 `_v5_executor` 里硬编码
  `coordinator` role 禁用 Bash:可以,但绕过 `role_visibility` 抽象;
  而且要写测试覆盖太繁琐
- **新增 `CoordinatorAgent` 子类**——增加类型层级,反而绕开现有
  `Agent(role='...')` 抽象
- **不引入三锁门**——只检查 env var + config,加法灵活但失去
  「用户对单个 team 显式声明意图」的持久化层
- **不扩展 team.json,只读 team metadata 字段**——`metadata["coordinator"]
  = True` 可以但语义模糊,加显式字段更清楚

## 9. 测试覆盖

- `tests/test_teams_v14_coordinator.py` ~15 个:
  - 三锁门:配置 / env / team.json 各 1,全命中 1,各缺失 1
  - `coordinator_enabled(config, team)` 边界
  - 降级路径:三锁不全 → 仍走 Lead
- `tests/test_teams_v14_team_use_coordinator.py` ~5 个:
  - `--coordinator` argparse 解析
  - 三锁全命中 → 走 coordinator
  - 任一锁缺失 → 降级 + stderr 报告
  - team 不存在 → exit 3
- `tests/test_tools_role_coordinator.py` ~5 个:
  - `ToolRegistry.get_all_tools(role='coordinator')` 不含 Write/Edit/Bash
  - 含 Read/Grep/Glob/WebFetch + 6 个 team_*
  - 含 load_skill(internal 不受白名单)
- 集成 `tests/integration/test_team_coordinator_e2e.py` ~5 个:
  - 端到端:CLI → use_team → ChatScreen → Agent(role='coordinator') 构造
  - 三锁命中 + team.json.coordinator=true + env_var set
  - Read tool 能读 outbox.jsonl
  - team_dispatch 仍可调(member 接收消息)

## 10. 文档同步

- `CHANGELOG.md` 加 `v1.4.0-coordinator` 段
- `CLAUDE.md` v1.4 范围段加 `v1-4-team-coordinator` 子段
- `README.md` 加 "Coordinator Mode" 段
- `docs/migrations/v1.4-pane-backend-to-v1.4-coordinator.md`(新)
- `config.example.yaml` 加 `teams.coordinator:` 配置示例