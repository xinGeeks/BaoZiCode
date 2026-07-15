# v1.4 Pane Backend — Member 派生 / 唤醒 / Resume Proposal

## Why

v1-4-team-foundation 落地 Team + Mailbox + 锁;v1-4-team-tools 在其上加
Lead 协作工具 + 任务 + Approval + MailboxNotifier。但 Member 还是磁
盘上描述符 —— **没有运行时进程**:`team_dispatch` 写完 inbox 没人消费;
`MemberState.backend_pid` 永远 None;5 个 BackendType 字面量没实现。

本 proposal 接通最后一块:**实际派生 Member 进程**(tmux / iTerm2 /
Windows Terminal pane 或 asyncio Task)。v0.3 Agent Loop、v0.5 五层
防御、v1.0 Skills 全复用。

| Proposal                | 职责                                            | 依赖                          |
|-------------------------|-------------------------------------------------|-------------------------------|
| foundation              | Team + Mailbox + 锁                              | 无                            |
| team-tools              | 6 协作工具 + 任务 + Approval + 角色过滤          | foundation                    |
| **pane-backend(本段)**  | 5 BackendType + spawn / kill / wake / resume     | foundation + team-tools       |
| team-coordinator        | 双锁开关 + 工具白名单收缩                       | foundation + team-tools + 本  |

## What Changes

- **新增 `baozicode/teams/pane.py`** —— `BackendHandle` Protocol +
  `PaneTmuxBackend` / `PaneITerm2Backend` / `PaneWindowsTerminalBackend`
  / `CoroutineBackend` / `WorktreeCoroutineBackend` 5 个实现;每个
  实现提供 `pid() / is_alive() / kill() / title()` 一致接口
- **新增 `baozicode/teams/backend_manager.py`** ——
  `BackendManager(teams_registry)` 居中调度:
  - `detect_available_backends()` 探 `tmux -V` / `mdfind iTerm` /
    `wt.exe exists`,Auto-upgrade `Member.backend="coroutine"` → pane
  - `spawn_if_offline(team, member)` —— 首 dispatch 触发;同 (team,
    member) spawn dedup,后续 await in-flight
  - `is_alive(team, member)` —— `os.kill(pid, 0)` POSIX /
    `tasklist` Win,失败 fallback stale 标记
  - `kill(team, member, *, grace_seconds=5.0)` —— SIGTERM → grace →
    SIGKILL chain;写 `state.json: status="offline"`
  - `pane_session_persist` —— `<teams_dir>/<team>/pane_info.json`
    存 tmux session 名 / iTerm2 window id / WT tab uuid,Lead 重启不重建
- **MODIFIED `baozicode/teams/schema.py`** —— `Member.config` 字段
  持久化 pane-specific 配置(tmux session name / iTerm2 cookie path /
  WT default profile);`MemberState.backend_pid` 由 pane-backend 实
  填(已有字段,foundation 留空)
- **MODIFIED `baozicode/teams/tools.py`** —— `team_dispatch` 在写
  inbox + wake 后调 `backend_manager.spawn_if_offline(team, member)`
  触发实际派生;`team_cancel(terminate=True)` 改走
  `backend_manager.kill`(纳 grace 链),不再裸 `os.kill(state.backend_pid,
  SIGTERM)`
- **新增 `baozicode/teams/member_loop.py`** —— `MemberMainLoop(team,
  member_dir)` 长生命周期 polling 循环:`wait_for_wake → read_inbox
  → build_member_agent → run_turn → write_state(idle) → loop`;只
  在 `terminate=True` / 进程崩溃 / `baozicode team destroy` 时退出
- **新增 `baozicode/teams/member_agent.py`** —— `build_member_agent
  (teams_registry, team, member) -> Agent` 构造,显式
  `role='member'`;7 builtin 工具通过 v1-4-team-tools role 过滤
- **新增 CLI 子命令 `baozicode member run --team=X --name=Y`** ——
  Headless 启动 `MemberMainLoop`(无 TUI / 无 ChatScreen);cwd =
  member.workdir(`os.chdir(member.workdir)`,默认 `.worktrees/<name>/`)
- **MODIFIED `baozicode/app.py`** —— `on_mount` 末尾构造
  `BackendManager(self.teams)` 单例 + 注入 6 个 team_* 工具的 spawn
  钩子;`on_unmount` 保留 BackendManager 引用(panes 持久,Lead 退出
  不清理)
- **MODIFIED `team-management` spec** —— 加 7 个新 Requirement
  (BackendHandle Protocol + 5 实现 + BackendManager + CLI +
  MemberMainLoop + MemberAgent + pane_info.json 持久化)

`tool-calling` spec **不变** —— Member 拿 7 builtin 工具的过滤契约
v1-4-team-tools 已经落地(`role='member'` 已经在 `AGENT_ROLES`,
`ToolRegistry.get_all_tools(role='member')` 返回 7 builtin),本 proposal
**复用**,不为 member 子集再加 spec。

## Out of Scope(后续 1 个独立 proposal)

- **team-coordinator** —— `BAOZICODE_COORDINATOR=1` env var +
  `teams.coordinator.enabled` 配置双锁;Lead 派生
  `role='coordinator'` 子 Agent,工具白名单收缩到只读 + shell +
  team_*(留在 coordinator proposal)
- 全功能 Member TUI(`baozicode --team=X --name=Y` 起完整 ChatScreen)
  —— coordinator proposal 末尾加 `--tui` flag 给 engineer debug 用
- 多 Lead 同 team 协同 —— 第一版拒绝,先保证单 Lead 单 team。

永远不做(超出 v1.4 范围):

- 跨机器分布式 team
- Member 间实时流式通信(继续 mailbox 文件 + 200ms 轮询)
- 复杂任务依赖约束(条件依赖 / 资源约束 / 优先级)— 只支持简单
  `depends_on: list[str]` 拓扑
- Member ↔ Member P2P(只 Lead ↔ Member 直连,member 间通信必须经
  Lead 中转,防隐藏耦合)

## Capabilities

### Modified Capabilities

- **`team-management`** —— 加 7 个 Requirement:BackendHandle Protocol;
  5 个 BackendType 实现(tmux / iTerm2 / Windows Terminal / coroutine
  / worktree-coroutine);BackendManager(env detect + spawn_if_offline
  + is_alive + kill + pane_session_persist);`baozicode member run`
  CLI;MemberAgent(role='member')+ MemberMainLoop 长生命周期 polling;
  pane session 跨 Lead 重启持久(pane_info.json)

不新增独立 capability —— spawn / kill / resume 都是 team-management 的
"运行时层",tool-calling 不变(member 工具子集 v1-4-team-tools 已铺
好路)。

## Impact

**代码**(`baozicode/teams/`):

- `pane.py`(新)—— `BackendHandle` Protocol + 5 个 backend 类,各 ~80-150
  行
- `backend_manager.py`(新)—— BackendManager ~250-350 行
- `member_loop.py`(新)—— MemberMainLoop ~150 行
- `member_agent.py`(新)—— `build_member_agent` ~50 行(复用
  `agent/loop.Agent.__init__`,只显式传 `role='member'`)
- `tools.py`(改)—— `team_dispatch` 末尾加 `await
  backend_manager.spawn_if_offline(...)`(~10 行);`team_cancel`
  `terminate=True` 分支改走 BackendManager.kill(~20 行)
- `schema.py`(改)—— `Member.config` 字段已有(无需扩);`MemberState`
  不变
- `mailbox.py`(改)—— `Mailbox.wait_for_wake` 不变(pane-backend 用
  同抽象);`Mailbox.read_inbox_for_member` helper 新加,~15 行
- `cli.py`(改)—— `baozicode team <action>` 加 `member run
  --team=X --name=Y` 子命令 ~30 行 argparse
- `app.py`(改)—— `on_mount` 末尾追加 `BackendManager(self.teams)`
  ~5 行;`_register_team_tools` 不变(spawn 钩子在 executor 里调
  backend_manager)
- `tui/chat_screen.py`(改)—— 不变(panes 跑 headless 不进 TUI)
- `__main__.py`(改)—— 顶级 CLI dispatch `member run` 子命令 ~5 行

**数据层**:

- `<teams_dir>/<team>/pane_info.json` —— 新增,per-team pane session
  元数据(tmux session name / iTerm2 window id / WT tab uuid)+ 每
  member pane id/handle;BackendManager 启动时 check-or-recreate
- `<teams_dir>/<team>/<member>/state.json` —— `backend_pid` 由
  pane-backend 实填(字段已有);新增 `backend_type: str`(记录实际派生
  的 backend 字面量,供 Lead 重启后 is_alive 检测用)

**CLI 新增**:

- `baozicode member run --team=X --name=Y` —— headless 启动
  MemberMainLoop(也是 pane backend 的目标命令)

**LLM 行为变化**:

- `team_dispatch(member=alice)` —— 首次触发派生(可能是同进程
  coroutine,也可能是 pane 子进程),可能阻塞最多 ~3s(等 pane
  spawn);后续 dispatch 不重新 spawn
- `team_cancel(terminate=True)` —— 走 grace chain:SIGTERM →
  5s → SIGKILL + `state.json: status=offline`
- Lead 不需要新工具 —— 6 个 team_* 工具不变(spawn / kill 钩子在
  executor 内透明)

**App 层 / ChatScreen**:
- `BaoZiCodeApp.on_mount` 末尾追加 `BackendManager(self.teams)`
  单例构造(~5 行)
- `team_dispatch` / `team_cancel(terminate=True)` executor 调
  backend_manager 接口(替换现有 fallback log warn)
- panes 跨 Lead 进程持久 —— `on_unmount` **不**调 cleanup

**依赖方向**:

```
tools.py (executor)
   └─→ backend_manager
         └─→ pane.py (BackendHandle Protocol + 5 implementations)
         └─→ mailbox (read_state / write_state / wake / inbox read)
member_loop.py
   └─→ member_agent.py (构造 Agent role='member')
   └─→ mailbox.wait_for_wake + read_inbox
   └─→ agent/loop.Agent.run 复用 v0.3 Agent Loop
app.py (BaoZiCodeApp)
   └─→ BackendManager 注入到 tools.py executor
cli.py
   └─→ member_loop.MemberMainLoop
```

`teams/pane.py` 不依赖 `agent/loop.py`(纯 subprocess + tmux 协议);`
teams/backend_manager.py` 不依赖 `tui/`;`teams/member_loop.py` 依赖
`agent/loop.py` 是 v0.3 已立的单向依赖。`teams/` 不依赖 `permissions/`
/ `mcp/` / `skills/`,与 team-tools 阶段一致。

**测试**(`tests/` 新文件):

- `tests/test_teams_v14_pane_backends.py` ~40 个
  - PaneTmuxBackend ~8(tmux 命令构造 + new-session + kill-session +
    select-pane -T + graceful chain)
  - PaneITerm2Backend ~6(osascript 构造 + escape + session uuid)
  - PaneWindowsTerminalBackend ~6(wt.exe new-tab 构造 + session.title)
  - CoroutineBackend ~6(in-process spawn + os.kill)
  - WorktreeCoroutineBackend ~6(os.chdir 到 .worktrees/<name>/ 验证
    + 子目录自动生成)
- `tests/test_teams_v14_backend_manager.py` ~25 个
  - detect_available_backends ~6(tmux-only / iterm-only / wt-only /
    none / mixed 优先级)
  - spawn_if_offline ~5(已活跳过 / 首 dispatch spawn / 同 (team,
    member) dedup / 失败回退 coroutine)
  - is_alive ~4(os.kill pid=0 / stale pid / pane-info 校验 /
    Windows tasklist)
  - kill ~5(SIGTERM → grace → SIGKILL / 单次就退出 / state.json 写
    offline)
  - pane_info.json 持久化 ~3(load / save / 不存在走默认)
- `tests/test_teams_v14_member_loop.py` ~15 个
  - 主循环 happy ~5(等 wake → 读 inbox → 启 Agent → 写 outbox → 写
    state idle → 继续 wait)
  - 长生命周期 ~3(多轮 dispatch 同 Agent? fresh agent per wake?)
  - 异常恢复 ~4(pane 后端崩 → 重连 → state 重新 idle)
  - team_cancel(terminate) ~3(grace → SIGTERM → run loop exit)
- `tests/test_teams_v14_member_cli.py` ~6 个
  - 解析 `--team` / `--name` 必填 / 缺参数报错 / member 不存在 /
    team 不存在 / 工作目录不存在
- `tests/test_teams_v14_app_integration.py` ~6 个
  - `BaoZiCodeApp.on_mount` 后 BackendManager 单例已就绪
  - 6 个 team_* tool executor 在 spawn 钩子可用时不报错
  - on_unmount 不清理 panes(BackendManager 引用保留)

合计 ~98 个新测试。Pane backend 子进程由 `monkeypatch subprocess.run`
fake 替换(已在 `test_teams_v14_merge.py` 用过,同模式)。

**文档**:

- `openspec/specs/team-management/spec.md`(改)—— 加 7 个新 Requirement
- `README.md` 加 "Member Runtime" 段介绍 5 BackendType + spawn /
  kill / resume 链路 + pane session 拓扑
- `CHANGELOG.md` 增 `v1.4.0-pane-backend` 段
- 项目根 `CLAUDE.md` "v1.4 范围"段加 `pane-backend` 子段 + 在
  team-tools 之上的增量说明
- `docs/migrations/v1.4-tools-to-v1.4-pane-backend.md`(新)—— Member
  进程启动 + pane 拓扑 + spawn / kill 链 + 跨平台兼容说明
- `config.example.yaml` 加 `teams.pane_backend:` 块 + 字段注释
  (tmux_session_prefix 等)

**无 breaking change**:

- v1-4-team-tools 阶段所有配置默认 `Member.backend="coroutine"` —
  本 proposal auto-upgrade 到 pane(只多不收);没装 tmux/iTerm2/WT
  退到 coroutine,行为**完全等同** team-tools 阶段
- `MemberState.backend_pid` 字段已有,本 proposal 第一次填真值;
  老 `state.json` 全 `null` 也兼容(coroutine backend 本来就 None)
- `Member.config` 字段已有(`dict[str, Any]`),本 proposal 引入
  非空 schema 建议(tmux session name prefix / iTerm2 cookie path);
  LLM 拼错的 `Member.config` 在 `_load_member` 走 dataclass 反序
  列化,V1.4 留空兼容
- 不引入新 CLI flag —— 只在 `baozicode team <action>` 之上加
  `baozicode member run` 子命令,顶层 argv 分发
- 不引入新权限层 / 新 MCP server / 新 LLM client

## Risks

[R1] **Pane 进程泄漏** —— 用户开 Lead → 派生 pane → Lead 退出, tmux
session 没清 → 累积。Mitigation:`BackendManager` 启动时扫同一
`tmux_session_prefix`(默认 `baozicode`)名下 orphan session,自动
kill 无人关联的;`pane_info.json` 持久化做权威 ground truth,
orphan pane 才回收。

[R2] **Graceful kill 超时** —— `team_cancel(terminate=True)` 走
SIGTERM → 5s → SIGKILL chain;tmux pane 子进程若 hung(LLM 死循环)在
5s 内不退 → 强杀。Mitigation:5s 默认 + log warn 表明强杀,kill 完
总写 `state.json: status="offline"`。

[R3] **Pane backend 探测误报** —— `tmux -V` 命令返回 0 但 `tmux new-session`
权限拒绝 / server socket 不在。Mitigation:`detect_available_backends`
返回 `(name, healthy: bool)` 元组,health 检验实际派生是否成功(尝试
dry-run `tmux new-session -d -s <test-name> \; kill-session -t <test-name>`);失败 deprioritize coroutine。

[R4] **Member Agent LLM 死循环** —— Member 跑飞(denial 太多 / Agent
loop 跑飞)。Mitigation:Member Agent 跑 v0.5 `_v5_executor` + v0.9
`DENIALS_EXCEEDED` 终止;`MailboxNotifier` 把 `status="unknown" /
last_active_ts` 注入 Lead sys-reminder;Lead LLM 看到后 5min 无活动
主动 `team_cancel` 收尾。

[R5] **同 (team, member) spawn race** —— Lead 同时两条 dispatch 触发两
次 spawn(罕见但理论可能)。Mitigation:`BackendManager` 内部
`asyncio.Lock` per (team, member);首条 dispatch 拿锁 + spawn,后续
await 同 in-flight task 完成。同 (team, member) 同一时刻只有一个
进程在跑。

[R6] **Worktree 与 pane cwd 冲突** —— Member 跑 `Bash`
默认 cwd 是 project_root(由 v1.3 L2 sandbox 决定),但 pane 后端
`os.chdir(member.workdir)` 后 Member Agent 自己 PWD 是
`.worktrees/<name>/`,与 v1.3 Bash tool 期望的 cwd 不一定一致。
Mitigation:`os.chdir` 在 member 进程启动早期完成;v1.3 Bash tool
用相对 cwd,Member 在 `.worktrees/<name>/` 跑所有 git 命令本就是
工作区根(由 v1.3 worktree 初始化保证),跨 worktree git 引用走
Lead 的 `team_merge` 工具。

[R7] **`Member.config` schema 漂移** —— tmux session name prefix /
iTerm2 cookie / WT profile 都是 backend-specific 配置,LLM 拼错也
得 lenient。Mitigation:`Member.config` 字段保持
`dict[str, Any]`,backend 实现内部 `_extract_config(
member.config, expected_keys=...)` 取子集,缺字段走默认值;LLM 错
传不挂,仅 log warn。

[R8] **Pane session 销毁 = Team 销毁** —— `baozicode team destroy
devops` 应该同时 kill all panes。Mitigation:`TeamStore.destroy`
在删 `<team>/team.json` 前调 `BackendManager.cleanup_team(team)`;
CLI `destroy` 子命令加 `--keep-panes` flag 跳清理(deprecated debug
用)。

## Migration Plan

无用户可见 breaking change。

升级前:Lead 用 `Member.backend="coroutine"` 派生 asyncio 任务(本
proposal 引入后才开始填 `backend_pid`);`MemberState.backend_pid`
永远 None。

升级后:

1. Lead 不改任何 tool 调用 —— `team_dispatch` 自动派生;
   `Member.backend` 默认 `"coroutine"`,装 tmux 自动升级
2. 已存在的 members 不主动派生,等首次 `team_dispatch` 触发
3. `MemberState.backend_pid` 字段已存在,fresh member 是 None;
   派生后填实际 PID
4. 老 CLI `baozicode team <action>` 不变;新增 `baozicode member run`
   子命令独立

部署:

1. 合并 `v1-4-team-pane-backend` → main
2. bump version 1.4.1 → 1.4.2
3. release notes:
   - "Member 后端派生落地:`coroutine` 默认,装 tmux/iTerm2/Windows
     Terminal 自动升级"
   - "`pane_info.json` 持久化让 Member panes 跨 Lead 重启不重建"
   - "`team_cancel(terminate=True)` 走 SIGTERM → 5s → SIGKILL chain"

回滚:

- 单 commit revert
- `teams/{pane,backend_manager,member_loop,member_agent}.py` 删掉
  + 还原 `teams/tools.py` executor 的 fallback log warn 分支
- `MemberState.backend_pid` 字段保留(基础字段,有 None 值)
- `pane_info.json` 不删 —— 留着孤儿,下次启用 pane-backend 自动
  接管
- v1-4-team-tools 数据层完全保留,zero 残留副作用
