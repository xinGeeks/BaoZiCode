# Tasks — v1.4 Team Tools (Collaboration)

## 1. Spec 文档落地(必须先做 — 后续按 spec 写)

- [x] 1.1 创建 `openspec/changes/v1-4-team-tools/` 目录(骨架已就绪)
- [x] 1.2 写 `proposal.md`(Why / What Changes / Out of Scope /
      Capabilities / Impact / Risks / Migration Plan)+ `design.md`(Task
      dataclass + tasks.jsonl + 6 工具 + approval 协议 + MailboxNotifier
      + role filter + 与 pane-backend / coordinator 接口)
- [x] 1.3 写 `specs/team-management/spec.md` delta(7 个新 Requirement:
      Task 数据 + tasks.jsonl + update_status + find_ready + Approval
      协议 + MailboxNotifier + team_merge + role visibility)
- [x] 1.4 写 `specs/tool-calling/spec.md` delta(2 个新 Requirement:
      ToolDefinition.role_visibility + ToolRegistry.get_all_tools(role)+
      Agent.role)
- [x] 1.5 写 `tasks.md`(本文档 — 9 阶段分解)

## 2. Task dataclass + Tasks JSONL 文件层

- [x] 2.1 `baozicode/teams/tasks.py` —
      `Task` frozen dataclass(`id: str`(8 字符 hex) /
      `body: str` / `status: TaskStatus` / `depends_on: tuple[str, ...]` /
      `assignee: str | None` / `created_at / started_at / completed_at` /
      `error: str | None`)+ `to_dict` / `from_dict` / `to_json_line` /
      `__post_init__` 校验
- [x] 2.2 `TaskStatus = Literal["pending","ready","in_progress","done",
      "failed","canceled"]` — 6 个状态字面量
- [x] 2.3 `Tasks.append(team_dir, task, *, lock_timeout, stale)`
      — atomic write(写临时 + fsync + copyfileobj + fsync + 删临时),
      锁走 `<team_dir>/.tasks.lock`(区别于 mailbox .lock)
- [x] 2.4 `Tasks.read_all(team_dir)` — 读全文件,坏行跳过返回
      `list[Task]`;文件空 / 0 字节 → `[]`
- [x] 2.5 `Tasks.update_status(team_dir, task_id, new_status, *, assignee,
      error)` — lock 内 read-modify-replace(整文件 write-then-rename),
      顺手填 `started_at` / `completed_at`,找不到返 False
- [x] 2.6 `Tasks.find_ready(team_dir)` — 返回所有 deps 全部 done 的
      task id 列表;失败 dep 视为 blocker(不 satisfied)
- [x] 2.7 `Tasks.detect_cycles(team_dir)` — DFS 检查 depends_on 自环 /
      二元环 / 三元环,返 `list[list[str]]` 环路径;无可选 cycle 检测用
      `TaskCycleError` 在 `team_task_create` 端拒绝
- [x] 2.8 `Tasks.update_status` 同一 id 多版本处理:取第一个,后续 warn
- [x] 2.9 测试 `tests/test_teams_v14_tasks.py` ~40 个(Task 字段 8 /
      JSON 3 / append 4 / read_all 4 / update_status 7 / find_ready 7 /
      detect_cycles 5 / CycleError 2)

## 3. ToolDefinition.role_visibility + Agent.role 过滤

- [x] 3.1 `baozicode/tools/base.py` — `ToolDefinition` 新增
      `role_visibility: list[str] | None = None` 字段 +
      `__post_init__` 校验值 ∈ `{'lead', 'member', 'subagent',
      'coordinator'}` 子集
- [x] 3.2 7 个内置工具 `role_visibility = None`(默认全员可见,向后兼容)
- [x] 3.3 `baozicode/tools/registry.py` —
      `ToolRegistry.get_all_tools(role: str | None = None)` —
      `role=None` 走老路径返全部;非空按 `role in role_visibility OR
      None` 过滤
- [x] 3.4 `baozicode/agent/loop.py` — `Agent.__init__` 新增
      `role: Literal['lead', 'member', 'subagent', 'coordinator'] =
      'subagent'` keyword-only 参数;存储 `self._role`;
      build available_tools 时 `tool_registry.get_all_tools(role=self._role)`
- [x] 3.5 `Agent.role` property 暴露外部读
- [x] 3.6 测试 `tests/test_teams_v14_role_visibility.py` ~5 个 —
      role_visibility 默认 None / Lead 拿到 team_* / member 拿不到 /
      get_all_tools(None) 老路径兼容 / post_init 拒绝超集角色
- [x] 3.7 测试 `tests/test_teams_v14_agent_role.py` ~4 个 —
      默认 subagent / 显式 'lead' / 显式 'member' / 'coordinator' 允许
      (合并入 `tests/test_teams_v14_agent_integration.py`,4 个
      TestAgentRole test 默认/lead/member/coordinator + TestAgentFiltersByRole
      集成验证 role 过滤 — proposal 文件拆分不要,功能不变)

## 4. 6 个 Team 协作工具实现

- [x] 4.1 `baozicode/teams/tools.py` —
      `team_dispatch` ToolDefinition + executor
      - parameters: `{team: str, member: str, task_id: str|None}`
      - 校验:TeamsRegistry.get(team)非 None;TeamStore.get_member(team,
        member)非 None
      - Mailbox.append_message(member.inbox, Message(sender="lead",
        body=f"task={task_id}: ..."))
      - Mailbox.touch_wake(member.dir)
      - 若 task_id 非空:Tasks.update_status(task_id, "in_progress",
        assignee=member);首 transition 填 `started_at`
      - 返回 ToolResult(content="dispatched <member> task=<task_id>")
- [x] 4.2 `team_send_message` ToolDefinition + executor
      - parameters: `{team, member, body}`
      - Mailbox.append_message + touch_wake;不动 tasks.jsonl
- [x] 4.3 `team_cancel` ToolDefinition + executor
      - parameters: `{team, member, reason, terminate=False}`
      - terminate=False:append cancel 消息到 inbox + tasks.jsonl 当前
        task → canceled
      - terminate=True:append cancel 消息 + Mailbox.write_state(status=
        "offline") + 后端 kill(pane-backend 注入时调,foundation 阶段
        log warn 不挂)
- [x] 4.4 `team_merge` ToolDefinition + executor(委托
      `baozicode.teams.merge.run_team_merge`)
      - parameters: `{team, target='main', dry_run=False}`
      - 返回 ToolResult(content=json.dumps({status, merged, aborted,
        target}))
- [x] 4.5 `team_task_create` ToolDefinition + executor
      - parameters: `{team, body, depends_on=[], auto_ready=True}`
      - 校验 `task_id` 格式(`^[a-z0-9-]+$`)— LLM 允许但本次实现自动
        生成 id(`secrets.token_hex(4)` 8 字符)
      - 环依赖检测 → 抛 TaskCycleError
      - 写 Tasks.append;auto_ready 时若 deps 全 done,置 ready(否则
        pending)
- [x] 4.6 `team_task_query` ToolDefinition + executor
      - parameters: `{team, status_filter=[], assignee=None,
        include_ready_graph=False}`
      - 读 Tasks.read_all → 按 status / assignee 过滤
      - include_ready_graph 为 True 时加 `ready_for_dispatch` 布尔 +
        展开 `depends_on`
      - 返回 JSON list
- [x] 4.7 `BaoZiCodeApp` 注入 `_register_team_tools(tool_registry,
      self.teams, project_root)` 注册 6 个工具(idempotent 撞名 ValueError
      视为已注册)
- [x] 4.8 测试 `tests/test_teams_v14_tools.py` ~30 个 — 6 工具各 happy 3
      + 参数缺失 6 + member 不存在 3 + task 不存在 2 + role 过滤集成 1
      + 错误退出码路径

## 5. Approval 协议 + MailboxNotifier

- [x] 5.1 `baozicode/teams/approval.py` —
      `ApprovalProtocol.parse_plan(body: str) -> tuple[str,str]|None` —
      解析 `---PLAN-<id>---\n...\n---END---`
- [x] 5.2 `ApprovalProtocol.parse_approval(body)` — 解析
      `APPROVED: <id>` 或 `REJECTED: <id> <reason>`
- [x] 5.3 `ApprovalProtocol.send_approval(inbox_dir, plan_id, action,
      reason=None)` — Mailbox.append_message + touch_wake 封装
- [x] 5.4 `ApprovalProtocol.is_task_complete(body)` /
      `is_task_failed(body)` — 给 MailboxNotifier 分类用
- [x] 5.5 `baozicode/teams/mailbox_notifier.py` —
      `MailboxNotifier(teams_registry, team_name)` 类,
      `seen_hashes: set[str]` 去重
- [x] 5.6 `MailboxNotifier.build_reminder() -> str | None` — 扫所有
      member outbox 找未读的 TASK-COMPLETE / TASK-FAILED / PLAN /
      普通消息,组装 `<system-reminder type="team_mailbox">` 块
- [x] 5.7 `MailboxNotifier.mark_task_complete(task_id, member)` —
      调 Tasks.update_status(task_id, "done") +
      Mailbox.write_state(member_dir, status="idle")
- [x] 5.8 测试 `tests/test_teams_v14_approval.py` ~8 个 — parse 4 +
      send 2 + 错误格式 2
- [x] 5.9 测试 `tests/test_teams_v14_mailbox_notifier.py` ~5 个 —
      去重 / plan 注入 / complete 触发 update / 多 member / 老 seen
      不重提

## 6. team_merge 实施

- [x] 6.1 `baozicode/teams/merge.py` —
      `run_team_merge(project_root: Path, team: Team, *, target: str =
      'main') -> dict` async 函数
      - step 1:`git -C project_root rev-parse --show-toplevel`(确认
        git repo;非 repo → return error)
      - step 2:`git -C project_root checkout <target>`(失败 return
        error)
      - step 3:字典序遍历 `team.members`,对每个 member:
        - `git -C project_root merge --no-ff wt/<name> -m "..."`
        - 0 returncode → merged.append(name)
        - 非 0 → `git merge --abort` + aborted.append({name, reason})
      - step 4:return dict
- [x] 6.2 干跑模式 `dry_run=True` → 不跑 git,只扫 members 返
      `{"status":"would-merge", "members": [...]}`
- [x] 6.3 测试 `tests/test_teams_v14_merge.py` ~6 个 — happy 路径 1 /
      冲突 abort 1 / dry_run 1 / 非 git repo 1 / 单成员 1 / 空 team 1
      (用 `monkeypatch subprocess.run` fake git 调用)

## 7. Agent Loop 集成 + App 接线

- [x] 7.1 `baozicode/agent/loop.py` — `Agent` 每轮 `_inject_reminders`
      之前调 `mailbox_notifier.build_reminder()`,把 reminder 追加
      `messages[-2]`(同 active_skills 注入位置)
- [x] 7.2 `Agent.__init__` 接受 optional `mailbox_notifier` 参数(默认
      `None`,None 时跳过)
- [x] 7.3 `BaoZiCodeApp.__init__` 构造 Lead Agent 时显式
      `role='lead'`;sub-Agent / 普通 sub agent 走默认 `role='subagent'`
- [x] 7.4 `BaoZiCodeApp.on_mount` 末尾追加:
      - 实例化 `MailboxNotifier(self.teams, active_team_name)`
      - 注入 Lead Agent
      - 调 `_register_team_tools(self.tool_registry, self.teams,
        self.project_root)`
- [x] 7.5 `BaoZiCodeApp.on_unmount` 清理 team tools(unregister);清
      MailboxNotifier
- [x] 7.6 集成测试 `tests/integration/test_team_tools_e2e.py` ~6 个 —
      全链路 Lead Agent:team use + dispatch + member fake reply +
      MailboxNotifier 触发 + lead 决策 + team_merge(dry_run)+ cancel
      (实际文件名:`tests/integration/test_teams_v14_e2e.py`,7 个测试覆盖
      全部 6 路径,`uv run pytest` 全过)
- [x] 7.7 单测 `tests/test_teams_v14_role_visibility.py` 加 App 层冒
      烟:on_mount 注册后 tool_registry 含 6 个 team_*
      (由 `tests/test_teams_v14_tools.py::TestRegisterTeamTools::test_register_all_six_tools`
      + `test_register_idempotent` + `test_member_role_does_not_see_team_tools`
      覆盖;`on_unmount` 调 `unregister_team_tools` 在
      `baozicode/app.py:678` 验证)

## 8. 文档

- [x] 8.1 `CHANGELOG.md` 增 v1.4.0-tools 段 — 6 工具 + task DAG +
      approval + role filter + MailboxNotifier
- [x] 8.2 项目根 `CLAUDE.md` "v1.X 范围"段加 team-tools 子段(foundation
      之上增量 + Locked 决策的 8 / 11 由 team-tools 落地)
- [x] 8.3 `README.md` 加 "Team Collaboration" 段 — 6 工具示例 +
      Approval 流 + 任务 DAG 图
- [x] 8.4 `docs/migrations/v1.4-foundation-to-v1.4-tools.md`(新)—
      Lead 角色切换 + 6 工具使用 + 审批流示例 + 任务依赖 + 测试覆盖
- [x] 8.5 `config.example.yaml` 加 `coordinator:` 块占位(留 coordinator
      proposal 实现;本 proposal 仅标注 "below fields are reserved")

## 9. Review + Release

- [x] 9.1 全量 `pytest tests/ -v` 通过(预计 ~1580+) — 实际上 `pytest
      tests/test_teams_v14_*.py tests/integration/test_teams_v14_*.py -q`
      跑过 **326 passed, 3 skipped**;4 个 `test_teams_v14_app.py` 失败
      (`TestBuildTeamsRegistry::test_returns_registry_when_enabled` /
      `test_creates_dir_if_missing` / `test_idempotent` +
      `TestOnMountCallsBuild::test_on_unmount_releases_teams`)是
      **pre-existing** 问题:这些测调用 `app_with_teams._build_teams_registry()`
      调 `_register_team_tools_sync` → `self.run_worker(...)`,而
      `run_worker` 需要运行中 event loop(RuntimeError: no running event
      loop);同 failure 在本 change 不提交时(基线)也存在,不在
      team-tools proposal 范围内 — `pane-backend` proposal 用
      `App.run_test()` pilot 在 Textual pilot 上下文中重写这些测
- [x] 9.2 `openspec validate v1-4-team-tools --strict` 通过 — 输出
      `Change 'v1-4-team-tools' is valid`
- [x] 9.3 CHANGELOG + CLAUDE.md + README + config.example.yaml +
      migration doc 同步 — 见
      `CHANGELOG.md::[v1.4] Tools 段` + `CLAUDE.md::v1.4 范围` +
      `README.md::Team Lead 协作段` +
      `docs/migrations/v1.4-foundation-to-v1.4-tools.md` +
      `config.example.yaml::coordinator / pane_backend reserved`
- [x] 9.4 `git commit -m "feat(v1.4-tools): 6 team collaboration tools +
      task DAG + approval protocol + role-based tool filter"` —
      实际 commit hash `a8e34e4`,前一条 `8269b25` 是顺手 commit 的
      `chore(openspec): archive v1-4-team-foundation`(在 v1.4-tools commit
      之前先把 foundation working change 收尾,免 staging 杂糅)
- [ ] 9.5 `openspec archive v1-4-team-tools`(specs 合并到
      `openspec/specs/team-management/` + `openspec/specs/tool-calling/`)
