# Tasks — v1.4 Pane Backend (Member Runtime)

## 1. Spec 文档落地(必须先做 — 后续按 spec 写)

- [x] 1.1 创建 `openspec/changes/v1-4-team-pane-backend/` 目录(骨架已就
      绪:proposal.md / design.md / specs/team-management/spec.md /
      tasks.md)
- [x] 1.2 写 `proposal.md`(Why / What Changes / Out of Scope /
      Capabilities / Impact / 8 Risks / Migration Plan)+ `design.md`
      (BackendHandle Protocol + 5 实现 + BackendManager + pane_info.json
      schema + MemberMainLoop long-lived polling + member_agent factory +
      CLI + spawn/kill 链 + 与 team-tools spawn 钩子)
- [x] 1.3 写 `specs/team-management/spec.md` delta(7 个新 Requirement:
      BackendHandle Protocol + 5 实现 + BackendManager + pane_info.json
      持久化 + member run CLI + MemberAgent + MemberMainLoop)
- [x] 1.4 写 `tasks.md`(本文档 — 9 阶段分解)
- [x] 1.5 `openspec validate v1-4-team-pane-backend --strict` 通过
      (`Change 'v1-4-team-pane-backend' is valid`)

## 2. BackendHandle Protocol + 5 BackendType 实现

- [x] 2.1 `baozicode/teams/pane.py` —
      `BackendHandle` Protocol(`@runtime_checkable`)+ 4 method
      `is_alive() / kill(grace_seconds=5.0) / title(new_title)` + 4 属性
      `member_name / team_name / backend_type / pid`
- [x] 2.2 `PaneTmuxBackend`:
      - `available()` 调 `subprocess.run(["tmux", "-V"],
        capture_output=True, text=True, timeout=2)`,exit=0 + stdout
        startswith "tmux " → True
      - `spawn()`:`tmux has-session` + `new-session -d -s
        baozicode-team-<team> -n <placeholder>` 占位 → `new-window -t
        baozicode-team-<team> -n <member> -d "<command>"` → `list-panes`
        捕 pane_id / pid → `select-window` 拿焦点
      - `kill(grace_seconds=5.0)`:SIGTERM → grace → SIGKILL + `kill-window`
      - `title(new_title)`:`select-pane -t <session>:<win>.<pane> -T`
- [x] 2.3 `PaneITerm2Backend`:
      - `available()` 调 osascript 探测 iTerm2 存在
      - `spawn()`:osascript `create window with default profile command
        "<command>"` + set name to `<member>`
      - `kill()`:osascript `close session id <window_id>`(grace 链
        advisory)
      - `title(new_title)`:osascript `set name of session of window id
        <window_id> to "<title>"`
- [x] 2.4 `PaneWindowsTerminalBackend`:
      - `available()` `where wt.exe` 探测
      - `spawn()`:`wt.exe -w 0 new-tab --title "<session>/<member>"
        <command>`,捕 tab uuid via `wt.exe ls` lookup
      - `kill()`:`wt.exe close-tab --tab <uuid>`
      - `title(new_title)`:WT 无 native rename,no-op + log
- [x] 2.5 `CoroutineBackend`:
      - `available()` 永远 True
      - `spawn()` (async):在 `asyncio.get_running_loop()` 上
        `create_task(self._run(), name=<task-name>)`
      - `kill()`:`self._task.cancel()`(即时,无 grace chain)
      - `title()`:no-op
- [x] 2.6 `WorktreeCoroutineBackend(CoroutineBackend)`:
      - `__init__` 记 `self.workdir = workdir` + `self.setup_dir`
      - `spawn()` (async):确保 workdir 存在(WorktreeManager.create
        或 mkdir fallback)→ `os.chdir` → super().spawn()
      - 用 `WorktreeManager.create(name)`,不是已删除的 `.initialize`
- [x] 2.7 测试 `tests/test_teams_v14_pane_backends.py` ~30 个
      (**实际 52 个通过** — Protocol runtime_checkable + 5 backend 各
      ~10 个 + helpers + absolute 路径 + GUID 花括号兼容)

## 3. BackendManager 居中调度

- [ ] 3.1 `baozicode/teams/backend_manager.py` —
      `BackendManager(teams_registry, *, backend_detection_timeout=2.0,
      member_run_command=None)`,
      `__init__` 默认 `member_run_command = [sys.argv[0], "member",
      "run", "--team", "{team}", "--name", "{name}"]`
- [ ] 3.2 `detect_available_backends()` 缓存结果(`self._detected_
      available: dict | None`),同时跑 4 个 `available()`
      probe(`asyncio.to_thread` 异步并行),coroutine 永远 True
- [x] 3.3 `effective_backend(member: Member) -> BackendType`:
      - `member.backend in {"coroutine", "worktree-coroutine"}` 且 pane
        健康 → upgrade 按 sys.platform 优先级
      - `member.backend` 显式 pane-* 字符串 → 强用(不健康降级
        coroutine + log warn)
      - `member.backend == "coroutine"` 且 pane 全部不可用 → 返
        `"coroutine"`
      - `worktree-coroutine` 默认不主动 upgrade(留用户显式)
- [x] 3.4 `spawn_if_offline(team, member) -> BackendHandle` 异步:
      - 快路径:`self._handles[(team, member)].is_alive()` → 返现有
      - `asyncio.Lock` per `(team, member)` 去重;锁内二次检查
      - `effective_backend(member)` 选 backend 类型,构造实例
      - `handle.spawn()`(pane 类型是同步 subprocess 1-3s)
      - `Mailbox.write_state(idle, backend_pid=handle.pid)`
      - `_persist_pane_info(team, member, backend, handle)`
      - 缓存到 `self._handles[(team, member)]`
- [x] 3.5 `is_alive(team, member) -> bool`:从 `_handles` 拿 handle 调
      `handle.is_alive()`(失败 log warn 返 False)
- [x] 3.6 `kill(team, member, *, reason="", grace_seconds=5.0)` 异步:
      - 写 `state.json: status="offline"`
      - `_handles.pop(...)` 拿 handle;`asyncio.to_thread(
        handle.kill, grace_seconds=grace_seconds)` 异步调 sync kill
      - 更新 `pane_info.json: members[name].backend_type = None`
- [x] 3.7 `restore_panes(team)`:`on_mount` 时调,扫 pane_info.json,
      对每成员 `os.kill(pid, 0)` 验证 pane 还活;活 → hydrate
      wrapper handle;死 → 移除 entry + log info
- [x] 3.8 测试 `tests/test_teams_v14_backend_manager.py` ~25 个
      (detect probe cache / effective_backend sys.platform /
      auto-upgrade / dedup race / is_alive stale pid / kill grace
      chain / restore_panes hydrate or clear / pane_info 加载保
      存)(**实际 33 个通过**)

## 4. pane_info.json 持久化

- [x] 4.1 `baozicode/teams/pane_info.py` —
      `PaneInfo` frozen dataclass(`schema_version="1.0"` / `team` /
      `tmux_session_name` / `iterm2_window_id` / `wt_tab_uuid` /
      `members: dict[str, PaneMemberInfo]`)
- [x] 4.2 `PaneMemberInfo` frozen dataclass(`backend_type` /
      `pane_identifier` / `pid` / `last_active_ts`)
- [x] 4.3 `PaneInfo.save(path)` 原子写(write-then-rename)
- [x] 4.4 `PaneInfo.load(path)`:`FileNotFoundError` 返 None
- [x] 4.5 测试 `tests/test_teams_v14_pane_info.py` ~10 个(scheme_v
      field / 缺字段走默认 / 原子写 / load None 兜底 / 多 member)
      (**实际 25 个通过**)

## 5. MemberAgent 工厂 + MemberMainLoop 长生命周期 polling

- [x] 5.1 `baozicode/teams/member_agent.py` —
      `build_member_agent(teams_registry, team, member) -> Agent`:
      - `Agent(role="member", ...)` 走全套 init(同 Lead 但
        role='member')
      - 工具 = `tool_registry.get_all_tools(role="member")` → 7 builtin
      - `MailboxLayer(teams_registry, team, member)` 注入 framework 层
        hook(`read_inbox_unread` / `write_outbox` / `mark_inbox_read`)
      - `conversation=ConversationManager()` fresh(无 resume 从 disk)
- [x] 5.2 `MailboxLayer` 内部用 `Mailbox` staticmethod 读写;不暴露为
      `ToolDefinition`(LLM 不可调)
- [x] 5.3 `baozicode/teams/member_loop.py` —
      `MemberMainLoop(teams_registry, team, member)` 类:
      - `__init__`:存 `_registry / _team / _member / _member_dir /
        _member_obj` + `_terminate_flag = asyncio.Event()` +
        `_active_turn_task: asyncio.Task | None`
      - `async def run()`:`os.chdir(member.workdir)` + wake_initialized
        + while loop:`wait_for_wake` → `read_messages(unread=True)`
        → `write_state(running)` → `build_member_agent().run(...)`
        → `write_state(idle)` + `mark_read`;catch per-iter exception
      - `request_terminate()`:set flag + `self._active_turn_task.cancel(
        )` if not done
      - `async def _run_turn(agent, msgs)`:订阅 agent events,把 tool 侧
        发出的 mailbox write 自动转 outbox.append_message
- [x] 5.4 测试 `tests/test_teams_v14_member_loop.py` ~15 个:
      wait_for_wake unblocks / empty inbox 不 spawn Agent / fresh
      Agent per turn / 异常不挂 turn / `request_terminate` cancel
      running turn / state.json 写回(23 个测试全通过)
- [x] 5.5 测试 `tests/test_teams_v14_member_loop.py` (与 5.4 同文件)
      `TestBuildMemberAgent` 6 个:role filter 命中 / 7 工具子集 /
      MailboxLayer 注入但不暴露为 Tool / conversation fresh /
      no resume snapshot

## 6. CLI `baozicode member run`

- [x] 6.1 `baozicode/teams/cli.py` 新增 `add_member_subcommand
      (subparsers)`:`member` 主 + `run` 子命令(`--team` 必填 +
      `--name` 必填 + `--cwd` optional),argparse 标准 UsageError
      (code 2)
- [x] 6.2 `baozicode/teams/cli.py` 新增 `async def main_member_run(
      args) -> int`:
      - `load_config(...)` + `TeamsRegistry.bootstrap(config)`
      - `team = registry.get(args.team)`(`TeamNotFound` → exit 3)
      - `member = team.members[args.name]`(`MemberNotFound` → exit 6)
      - `os.chdir(args.cwd or member.workdir)`
      - `loop = MemberMainLoop(registry, args.team, args.name, ...)`
      - `await loop.run()`
      - 退出:`loop.request_terminate()` on KeyboardInterrupt / SIGTERM
        → state=offline + return 0
- [x] 6.3 `baozicode/cli.py` 加 dispatcher:`member` → 
      `run` 子命令走 `asyncio.run(main_member_run(args))`;原有
      `team` + TUI 路径不变
- [x] 6.4 测试 `tests/test_teams_v14_member_cli.py` 11 个:
      `--team` `--name` 必填校验 / `member_action` 必填 /
      team 不存在 exit 3 / member 不存在 exit 6 / `--cwd` 覆盖 /
      `os.chdir` 验证 / graceful exit 0 / KeyboardInterrupt 0

## 7. team-tools spawn 钩子

- [x] 7.1 `baozicode/teams/tools.py` — `execute_team_dispatch` 末尾
      追加 `await backend_manager.spawn_if_offline(team, member)`
      钩子;返回结果含 `backend=<type>` 文本片段
- [x] 7.2 `baozicode/teams/tools.py` — `team_cancel(terminate=True)`
      分支改走 `await backend_manager.kill(team, member,
      grace_seconds=5.0)`,替代 v1-4-team-tools 阶段的裸
      `os.kill(state.backend_pid, SIGTERM)`
- [x] 7.3 `baozicode/teams/tools.py` — `register_team_tools` 接
      `backend_manager` 参数;由 `BaoZiCodeApp.on_mount` 注入 Lead
      Agent 构造处
- [x] 7.4 测试 `tests/test_teams_v14_spawn_hook.py` 10 个:team_
      dispatch 末尾 spawn / spawn 失败回退 coroutine / team_
      cancel terminate 走 BackendManager.kill / grace 链 / 同
      (team, member) 二次 dispatch 不重复 spawn

## 8. App + TUI 接线

- [x] 8.1 `baozicode/app.py` — `on_mount` 末尾追加 `self.backend_
      manager = BackendManager(self.teams)`,把 `self.backend_manager`
      注入 `_register_team_tools` 闭包 → 6 个 team_* tool 的 executor
      自动拿到引用
- [x] 8.2 `baozicode/app.py` — `use_team(name)` 不重建 backend_
      manager(跨 Lead restart 持久)— 只设 `active_team_name` 与构
      造 MailboxNotifier
- [x] 8.3 `baozicode/app.py` — `on_unmount` 不清理
      `backend_manager`(panes 跨 Lead 持久)— panes 保留运行,下次
      Lead 启动 `restore_panes()` 自动 recover
- [x] 8.4 测试 `tests/integration/test_team_pane_backend_e2e.py` 12 个:
      on_mount 后 BackendManager 单例就绪 / team_dispatch 触发派生
      / Member 真实跑起 fresh Agent 处理 inbox / MailboxNotifier
      收到 TASK-COMPLETE / cancel terminate 走 BackendManager.
      kill / Lead 重启 restore_panes 命中 / 同 (team, member)
      dispatch dedup / panes 跨 on_unmount 存活

## 9. 文档 + Release

- [x] 9.1 `CHANGELOG.md` 增 `v1.4.0-pane-backend` 段 —— BackendHandle
      Protocol + 5 backend 实现 + BackendManager + pane_info.json +
      `member run` CLI + MemberMainLoop + MemberAgent
- [x] 9.2 项目根 `CLAUDE.md` "v1.X 范围"段加 `pane-backend` 子段
      (foundation + tools 之上增量 + Locked 决策的 12 个由
      pane-backend 落地 8 个)
- [x] 9.3 `README.md` 加 "Member Runtime" 段 —— 5 BackendType 表 +
      pane session 拓扑图 + spawn / kill 链 + `member run` 用法
- [x] 9.4 `docs/migrations/v1.4-tools-to-v1.4-pane-backend.md`(新)——
      Member 进程启动 + pane 拓扑 + spawn / kill / resume + 跨平台
      兼容
- [x] 9.5 `config.example.yaml` 更新 `teams:` 块注释 —
      pane_backend 配置走 `Member.backend` 字段,不需要新配置块
- [x] 9.6 全量 `pytest tests/ -v` 通过(467 v1.4 测试通过,5 个
      pre-existing failure:Textual app context / test pollution,
      与本次 pane-backend 工作无关)
- [x] 9.7 `openspec validate v1-4-team-pane-backend --strict` 通过
- [x] 9.8 CHANGELOG + CLAUDE.md + README + config.example.yaml +
      migration doc 同步
- [ ] 9.9 `git commit -m "feat(v1.4-pane-backend): 5 BackendType 实现
      + BackendManager + pane_info.json 持久化 + baozicode member
      run CLI + MemberMainLoop long-lived polling"`
- [ ] 9.10 `openspec archive v1-4-team-pane-backend`(specs 合并到
      `openspec/specs/team-management/`)

