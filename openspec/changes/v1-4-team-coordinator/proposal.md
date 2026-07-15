# v1-4-team-coordinator

## Why

v1.4 team-tools + pane-backend 已落地 Lead Agent 完整 lifecycle:
6 个 team_* 协作工具 + 5 BackendType 派生 + wake/resume/state。
Lead Agent 既能派活又能 `Write` / `Edit` / `Bash` —— 在大规模派活场景下
风险过大(Lead 误改文件可能覆盖 member 已完成的工作)。

需要一种 **监督者模式(coordinator)** —— 限制 Lead 的写文件工具,只留
观察 + 协作能力,在多 team / 多 Lead 协作场景下保证「Lead 只负责指派,
不动文件;写动作由 member 完成」。

## What Changes

### 双锁开关(配置 + 环境变量)

```yaml
# config.yaml
teams:
  coordinator:
    enabled: true          # 配置层开关
    env_var: BAOZICODE_COORDINATOR  # 必同时设
    allowed_tools:          # 白名单(默认 Read/Grep/Glob/WebFetch + team_*)
      - Read
      - Grep
      - Glob
      - WebFetch
      - team_dispatch
      - team_send_message
      - team_cancel
      - team_merge
      - team_task_create
      - team_task_query
```

```bash
BAOZICODE_COORDINATOR=1 baozicode team use --coordinator devops
```

**双锁命中**: `teams.coordinator.enabled AND $env_var set AND team.json.coordinator=true` 三者都满足才生效。

### 激活路径

扩展 `baozicode team use <name>` 加 `--coordinator` flag:

```bash
baozicode team use devops               # 现有路径:role='lead'
baozicode team use --coordinator devops # 新增:role='coordinator' + 工具白名单
```

激活后 ChatScreen 重建 Agent:`role='coordinator'` + `available_tools = allowed_tools`。

### Team schema 扩展

```python
@dataclass(frozen=True)
class Team:
    name: str
    lead: str = "lead"
    members: dict[str, Member]
    coordinator: bool = False  # NEW:该 team 是否启用 coordinator 模式
    ...
```

`team.json` 加 `"coordinator": true/false` 字段,默认 False(向后兼容)。

### 工具白名单机制

`Agent(role='coordinator')` + `ToolRegistry.get_all_tools(role='coordinator')`
走现有 `role_visibility` 过滤机制(已在 v1-4-team-tools 落地),
**不引入新过滤层** —— Coordinator role 看到的工具 = `ToolDefinition.role_visibility is None OR 'coordinator' in role_visibility`。

7 个内置工具全部 `role_visibility=None`(全员可见)→ **必须修改**让它们变
`role_visibility=['subagent', 'lead']`(明确剔除 coordinator)。否则
Coordinator 还能 Write/Edit/Bash,白名单失效。

6 个 team_* 工具保持 `role_visibility=['lead']` → **也必须扩展**为
`['lead', 'coordinator']`(让 coordinator 也能调)。

### Read tool 看 member outbox

Coordinator 用现有 Read tool 读
`<teams_dir>/<team>/<member>/outbox.jsonl` — 不需要新工具。
v0.5 L2 PathSandbox 允许读 `<teams_dir>/<team>/<member>/` 路径(v1.3
已有 worktree 路径白名单机制可复用)。

### App 接线

`BaoZiCodeApp.use_team(name, *, coordinator=False)` 新增 keyword-only 参数:

- `coordinator=False`(默认)→ 现有行为,role='lead'
- `coordinator=True` → role='coordinator' + `available_tools` 走白名单

CLI `team use --coordinator <name>` 走 `app.use_team(name, coordinator=True)`。

## Out of Scope

- **Member 端工具白名单** —— member 已经在 v1.4-pane-backend 阶段用
  7 工具子集(role='member')收窄,coordinator 不再触动
- **审计日志** —— Coordinator 操作可审计但本 proposal 不做(留 v2.0)
- **多 coordinator 协作** —— 一个 active team 只有一个 coordinator
- **运行时动态切换** —— `team use --coordinator` 后,切回 `team use`
  才能退出 coordinator 模式

## Locked 决策(来自 explore)

| # | 决策 | 落地方式 |
|---|------|---------|
| 1 | 激活路径 | 扩展 `team use --coordinator <name>` |
| 2 | 工具白名单 | Read/Grep/Glob/WebFetch + 全部 6 个 team_* |
| 3 | Scope | team.json `coordinator: true` + 配置 + 环境变量三锁 |
| 4 | Member 进度可见性 | Coordinator 直接 Read outbox.jsonl |

## 验证

- 全量测试通过(预计 +20 个新测试,共 ~487 个)
- `openspec validate v1-4-team-coordinator --strict` 通过
- Lead 工具白名单隔离:写测试验证 Write/Edit/Bash 在
  `role='coordinator'` Agent 的 `available_tools` 里消失
- 7 个内置 + 6 个 team_* 的 role_visibility 调整需破坏测试做兜底

## 测试覆盖估算

- `tests/test_teams_v14_coordinator.py` ~15 个 — 双锁 / 三锁门 / 白名单
  / role 过滤 / coordinator enabled but env var missing / coordinator
  disabled but env var set / Coordinator Agent 看不到 Write/Edit/Bash
- `tests/test_teams_v14_team_use_coordinator.py` ~5 个 — `team use
  --coordinator` 解析 / 三锁命中 / 不命中报错
- 集成 `tests/integration/test_team_coordinator_e2e.py` ~5 个 — 真实
  filesystem 跑完整流程

预计 ~25 个新测试,~487 个 v1.4 测试。