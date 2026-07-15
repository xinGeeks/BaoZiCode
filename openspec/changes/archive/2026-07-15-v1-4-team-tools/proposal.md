# v1.4 Team Lead — Collaboration Tools Proposal

## Why

v1.4-foundation 把"团队是什么 / 消息怎么落盘 / 锁怎么拿"这一层落地了 ——
Team / Member / Message dataclass、Mailbox 文件层、跨平台 lockfile、
`baozicode team <action>` 生命周期 CLI 全在了。但 Lead Agent 与 team 之间
**没有任何 first-class 协作工具**:Lead 没法派活、没法回成员消息、没法合
并各成员目录里的代码。

本 proposal 在 foundation 之上加 **6 个只对 Lead 可见的协作工具 + 1 个
共享任务清单 + 1 套基于 mailbox 的审批协议**,把 Lead 升级成"**真**的
Team Lead"——能把用户目标拆成带依赖关系的任务 → 派生成员 → 收回成果 →
合并代码。

**为什么不重新发明 SubAgent 或 Worktree**:v1.2 SubAgent、v1.3 Worktree
已经提供了完整的 Agent Loop 复用 + git worktree 隔离。本 proposal 的设计
目标是**在它们之上做组合**,不重复造轮子。

| Proposal               | 职责                                                  | 依赖                                       |
|------------------------|-------------------------------------------------------|--------------------------------------------|
| foundation             | Team / Member / Message 数据 + mailbox 文件 + 锁      | 无                                         |
| **team-tools (本段)**  | 6 协作工具 + 任务清单 + 审批协议 + Lead 角色过滤      | foundation                                 |
| team-pane-backend      | tmux / iTerm2 / Windows Terminal pane + watchdog wake | foundation                                 |
| team-coordinator       | 双锁开关 + 工具白名单收缩                              | foundation + team-tools                    |

## What Changes

新增 6 个 first-class 工具 + 扩展 1 处工具契约 + 扩展 2 处 spec:

- **新增 `baozicode/teams/tools.py`** — `team_dispatch` /
  `team_send_message` / `team_cancel` / `team_merge` /
  `team_task_create` / `team_task_query` 6 个 ToolDefinition +
  executor;Lead 启动时注册到 ToolRegistry,`role_visibility=['lead']`
- **新增 `baozicode/teams/tasks.py`** — `Task` frozen dataclass + `Tasks`
  文件层(类比 `Mailbox`)— 共享 `tasks.jsonl` 原子 append / list /
  update_status / cycle detection
- **新增 `baozicode/teams/approval.py`** — `parse_plan_message(body)` /
  `send_approval(inbox_dir, plan_id, action, reason)` helper;纯基于
  mailbox 协议,无新文件
- **新增 `baozicode/teams/merge.py`** — `run_team_merge(project_root,
  team, target='main')` helper;用 `git merge --no-ff wt/<member>` 顺序合,
  冲突 `git merge --abort` + 返回 partial result
- **MODIFIED `tools/base.py`** — `ToolDefinition` 新增 `role_visibility:
  list[str] | None = None` 字段(`None` = 全部角色可见)
- **MODIFIED `tools/registry.py`** — `ToolRegistry.get_all_tools(role=
  'lead' | 'member' | 'subagent') -> list[ToolDefinition]` 按
  `role_visibility` 过滤
- **MODIFIED `agent/loop.py`** — 给 `Agent` 加 `role: Literal['lead',
  'member', 'subagent']` 默认 `'subagent'`;Lead Agent 实例在
  `BaoZiCodeApp.on_mount` 时构造并显式 `role='lead'`;sub-Agent 走 v1.2
  默认(`role='subagent'`);成员 agent 走 `role='member'`
- **MODIFIED `app.py`** — `BaoZiCodeApp` 注入 `_lead_agent` + `_register_
  team_tools(registry)` 注册 6 个团队工具
- **MODIFIED `agent/loop.py`** — 加 `MailboxNotifier` hook:每轮 Agent
  决策前扫所有 member outbox,发现 `task_complete` / `plan` / `error` 消息
  → 追加 `<system-reminder type="team_mailbox">` 到 Lead conversation
- **MODIFIED `team-management` spec** — 加 4 个新 Requirement(Task 模型
  + tasks.jsonl schema + Approval 协议 + MailboxNotifier)
- **MODIFIED `tool-calling` spec** — 加 2 个新 Requirement
  (ToolDefinition.role_visibility + Agent.role 过滤)

## Out of Scope(后续 2 个独立 proposal)

- **team-pane-backend** — tmux / iTerm2 / Windows Terminal pane 实际
  spawn / watchdog 唤醒 / resume loop from disk state.json(`pane_backend`
  配置 + `Member.config` 里的 `session_name / window_id` 走 pane-backend
  proposal)
- **team-coordinator** — `teams.coordinator.enabled` 配置 +
  `BAOZICODE_COORDINATOR=1` env var 双锁;Lead 加 coordinator 模式时,
  用 `role='lead-coordinator'` 派生,工具白名单收缩到「只读 + shell +
  team_*」(本 proposal 加 `role` 字段正是为 coordinator proposal 铺路)

永远不做(超出 v1.4 范围):

- 跨机器分布式团队
- 成员间实时流式通信(继续走 mailbox 文件 + 200ms 轮询)
- 复杂任务依赖约束(条件依赖 / 资源约束 / 优先级)— 只支持简单
  `depends_on: list[str]` 拓扑门控
- 成员间 P2P 协作(只 Lead ↔ Member 直连,member↔member 通信必须经
  Lead 中转,防止隐藏耦合)

## Capabilities

### Modified Capabilities

- **`team-management`** —— 加 4 个 Requirement:`Task` dataclass +
  `tasks.jsonl` 文件层协议;`Tasks.append` / `list` / `update_status`
  / cycle detection 4 个新方法;Approval 协议(PLAN-/APPROVED:/
  REJECTED: mailbox 协议);MailboxNotifier hook(`task_complete` /
  `plan` / `error` → Lead sys-reminder)
- **`tool-calling`** —— 加 2 个 Requirement:`ToolDefinition.role_
  visibility` 字段语义;`Agent.role` 属性 + `ToolRegistry.get_all_tools
  (role)` 过滤契约(Lead / member / subagent 三个内置 role)

不新增独立 capability — 工具+任务+审批都是 team-management 的"操作层",
role 过滤是 tool-calling 的扩展。

## Impact

**代码**(`baozicode/`):

- `teams/tools.py`(新)—— 6 个 ToolDefinition + 6 个 executor + 共享 helper
- `teams/tasks.py`(新)—— `Task` frozen dataclass + `Tasks.append` /
  `read_all` / `update_status` / `find_ready` / `detect_cycles`(5 个
  静态方法,锁复用 `mailbox_lock`)
- `teams/approval.py`(新)—— 解析 plan、发送 approval/reject helper
- `teams/merge.py`(新)—— `run_team_merge(project_root, team, target)`
  走 `asyncio.create_subprocess_exec` 跑 `git merge`
- `teams/tasks.jsonl` 文件层 —— 与 mailbox.jsonl 同套 lockfile 抽象
- `tools/base.py`(改)—— `ToolDefinition` 加 `role_visibility` 字段 +
  `__post_init__` 校验值 in {None, 'lead', 'member', 'subagent'} 子集
- `tools/registry.py`(改)—— `get_all_tools(role: str | None = None)`
  过滤链
- `agent/loop.py`(改)—— `Agent.__init__` 加 `role: Literal['lead',
  'member', 'subagent']` 默认 `'subagent'`;每轮 Agent 决策前调
  `_inject_team_mailbox_reminders(self.teams, ...)` 把成员 outbox 摘要
  转 system-reminder 钉到顶部
- `app.py`(改)—— Lead Agent 构造时显式 `role='lead'` + 调
  `_register_team_tools(self.tool_registry)` 注册 6 个 team_* 工具
  (idempotent,on_unmount 反注册)

**依赖方向**:

```
agent/loop.py        →  baozicode.teams.{tools,tasks,approval,merge}
                      →  baozicode.teams.{mailbox,registry,store}
tool-calling spec    →  (扩展,无新依赖)
team-management spec →  (扩展,无新依赖)
```

`teams/tools.py` 依赖 `agent/loop.py`(拿 `Agent.role`)是**单向**,不
反向。`teams/` 不依赖 `permissions/` / `mcp/` / `skills/`。

**测试**(`tests/` 新文件):

- `tests/test_teams_v14_tools.py` ~30 个 — 6 工具各 happy + 参数错误 +
  approval 协议 happy / reject / parse error
- `tests/test_teams_v14_tasks.py` ~12 个 — JSONL append / list /
  update_status / cycle detection / multi-deps gating
- `tests/test_teams_v14_approval.py` ~8 个 — plan 解析 + approve / reject
  发往 inbox + 多 plan 并存 + plan_id 校验
- `tests/test_teams_v14_merge.py` ~6 个 — 顺序合 + 冲突 abort + 单成
  员 / 多成员 / 空 team 各路径(`git` 子进程被 monkeypatch 到 fake runner)
- `tests/test_teams_v14_role_visibility.py` ~5 个 — get_all_tools 过滤
 契约 + Lead / member / subagent 三角色
- `tests/test_teams_v14_agent_role.py` ~4 个 — Agent.__init__ default +
  Lead / member 显式构造
- `tests/integration/test_team_tools_e2e.py` ~6 个 — Lead Agent 实际跑
  全链路(team use + dispatch + member 回信 + 审批环 + merge)

合计 ~71 个新测试。

**文档**:

- `openspec/specs/team-management/spec.md`(改)—— 加 4 个新
  Requirement(Task + Approval + MailboxNotifier)
- `openspec/specs/tool-calling/spec.md`(改)—— 加 2 个新 Requirement
  (role_visibility + Agent.role)
- `README.md` 加 "Team Collaboration" 段介绍 6 工具 + Approval 协议 +
  任务 DAG 示例
- `CHANGELOG.md` 增 `v1.4.0-tools` 段
- 项目根 `CLAUDE.md` "v1.4 范围"段加 `tools` 子段 + 在 Foundation
  之上的增量说明
- `docs/migrations/v1.4-foundation-to-v1.4-tools.md`(新)—— Lead 角色
 切换 + 6 工具使用 + Approval 流程示例

**可能的 breaking change**(评估):

- **`ToolDefinition` 新字段**:`role_visibility` 默认 `None`,v1.3 老
  代码 / 测试不传也对,不破坏 dataclass 字段构造
- **`ToolRegistry.get_all_tools(role=None)` 新参数**:默认 `None` 走
  老路径返回全部,显式传 `role` 才过滤;v1.3 调用点零修改
- **`Agent.role` 新字段**:默认 `'subagent'`,v1.3 老 Agent 构造不传也
  对;`role='subagent'` 拿不到 team_* 工具,行为**较 v1.3 更受限**(sub
  Agent 本来就不该有 team 工具,此变化是**收紧**而非破坏)
- **6 个 team_* 工具**:纯粹新增;不影响 v1.0–v1.3 任何已有工具

## Risks

[R1] **cycle 检测 N² 复杂度** —— `Tasks.find_ready` 每次扫所有 task
判定 deps。当 tasks 超过 ~200 个时 N² 扫 + lockfile IO 可能变慢。
Mitigation:`find_ready` 走增量拓扑序;当 tasks > 200 时切到 `dict
[task_id] -> set[dep_id]` 邻接表 + Kahn 一次性算法;leader-only 调,
不频繁。

[R2] **Lead concurrency = 1**(单 Lead Agent) —— Team Lead 是单人,
不能多个 Lead 同时派活到同一 team(共享 tasks.jsonl + mailbox)。
Mitigation:`TeamsRegistry.acquire_lock(team_name, timeout=2.0)` —
同一时间只一个 Lead 写某 team 的 tasks.jsonl。失败返回 `Concurrent
LeadOperation` 错误。foundation 不引入此锁(team 单用户用),本 proposal
加,但只在 Lead Agent 调的 6 个工具里使用。

[R3] **Approval cycle 死锁** —— Member 等 APPROVED,Lead 等 Member
plan,任何一端崩溃对方永远等。Mitigation:`MailboxNotifier` 在 Lead
端注入 `<system-reminder>` 时含 member 状态 + last_active_ts;
Lead LLM 看到 member 超过 5 分钟无活动可主动 `team_cancel(reason=
'no_response')` 或 `team_send_message(asking 'still there?')`。

[R4] **team_merge 默认 target = main 与 worktree 名冲突** —— 成员
工作目录已是 `wt/<member>` 分支(由 v1.3 worktree 启动),`git merge
wt/alice` 又用别名,容易混淆。Mitigation:`run_team_merge` 内部
`git -C project_root merge wt/<name> --no-ff`,target 必须在 worktree
外(project_root 的 main 分支);若 Lead 在 worktree 内跑,有默认值 +
强制校验。

[R5] **role_visibility 与 tool_type 概念重叠** —— `tool_type='internal'`
(v1.0) + `role_visibility=['lead']`(本 proposal)可同时存在于同一工具。
Mitigation:`get_all_tools(role)` 用 AND 逻辑:**必须** role 命中 AND
type 命中(若有 type 限制)— L1 Skill 白名单 + L2 role 过滤互不冲突;
`load_skill` 是 `tool_type='internal'` + `role_visibility=None`,
任何 role 都可见

[R6] **Lead Agent 拿到 team_* 后循环派活** —— LLM 把 team_dispatch 当
万能工具用,所有问题都"开个新 team 来做"。Mitigation:`role_visibility
` 自带 grep-able `team_*` 前缀,L1 Skill 白名单不显式覆盖 team_*;
team_dispatch 工具 description 写明"只在用户目标需要多人协作时用,
单人任务直接用 7 内置工具"。

## Migration Plan

无用户可见 breaking change。本 proposal 默认行为 = 「对一般 sub-Agent
不可见 team_* 工具」(收紧而非放开)。

部署:

1. 合并 `v1-4-team-tools` → main
2. bump version 1.4.0 → 1.4.1
3. release notes:
   - "Lead Agent 获得 6 个 team 协作工具 + 共享任务清单 + Approval 协议"
   - "角色过滤:`team_*` 工具仅 Lead Agent 可见,sub-Agent / 普通 sub
     agent 看不到"
   - "MailboxNotifier:成员 task_complete 自动注入 Lead 系统提醒"

回滚:

- 单 commit revert
- `teams/{tools,tasks,approval,merge}.py` 删掉 + 回滚 `agent/loop.py` /
  `tools/base.py` / `app.py` 3 处改动
- 已存在的 teams / tasks / inboxes 都保留,只是 Lead 拿不到工具
- v1.4-foundation 数据层完全保留,zero 残留副作用
