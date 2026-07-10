# Tasks — v1.3 Worktree Isolation

## 1. Spec 文档落地(必须先做 — 后面所有实现按 spec 来)

- [x] 1.1 创建 `openspec/changes/v1-3-worktree-isolation/` 目录(骨架已就绪)
- [x] 1.2 写 `worktree-isolation/spec.md`(路径校验 + 4 步生命周期 + 退出保护 + 三层清理)
- [x] 1.3 写 `subagent-manager/spec.md` delta(追加 `worktree_state` + `force` 决策 2 个 requirement)
- [x] 1.4 写 `agent-runtime/spec.md` delta(追加 `isolation` 字段 + fork 互斥 + `effective_project_root` 3 个 requirement)
- [x] 1.5 写 `path-sandbox/spec.md` delta(追加 sub-Agent 沙箱回退 1 个 requirement)
- [x] 1.6 写 `tool-calling/spec.md` delta(追加 Bash `cwd` 1 个 requirement)
- [x] 1.7 写 `configuration/spec.md` delta(追加 `subagents.worktree:` 1 个 requirement)
- [x] 1.8 写 `proposal.md`(已完成)+ `design.md`(已完成)+ `tasks.md`(本文档)

## 2. Schema + 路径校验(`baozicode/worktree/` 新包)

- [x] 2.1 `baozicode/worktree/__init__.py` — 公开 API re-export
- [x] 2.2 `baozicode/worktree/schema.py` — `WorktreePathValidator.validate(name)` +
      `WorktreeSpec` frozen dataclass(`name` / `path` / `branch` / `state`)+
      `WorktreeState` Literal(`creating` / `active` / `detached` / `stale` / `removed`) +
      错误枚举(`PathValidationError` / `WorktreeNotInRepoError` /
      `WorktreeCreationFailed` / `WorktreeExistsDirtyError`)
- [x] 2.3 测试`tests/test_worktree_v13.py` ~38 个 case
      (合法/嵌套/太长/`.`/`..`/`/../`/特殊字符/`/` 起始/`/` 结尾/`./name`/Spec
      immutable/reexport 验证)

## 3. WorktreeManager 核心(生命周期)

- [x] 3.1 (part) `baozicode/worktree/manager.py` — `WorktreeManager` 骨架 + `__init__` git repo 校验 + `_git_subprocess` 私有助手 + `_try_fast_path`
      - `__init__(setup_dir: Path, hook_executor: HookDispatcher | None)`
      - `async create(name: str) -> WorktreeSpec` — 主路径
        (`git worktree add -b wt/<name> ...` + fallback recovery +
        fast-path 检查 3 步走)
      - `async enter(name: str) -> WorktreePath` — 读已存在 fast-path
      - `async exit(name: str, *, force: bool = False) -> WorktreeExitResult` —
        决策树(D8)
      - `async remove(name: str) -> None` — `git worktree remove` +
        `git branch -D`
      - 私有方法 `_try_fast_path(name)` / `_spawn_subprocess(...)`
- [x] 3.2 测试`tests/test_worktree_v13.py` 60 个 case
      (路径校验 28 / Spec 6 / Manager construction 5 / create 5 /
      enter 2 / exit 5 / queries 4 / public API 4 / integration 1)

## 4. 环境初始化(`WorktreeInitializer`)

- [x] 4.1 `baozicode/worktree/initializer.py` — `WorktreeInitializer.run(worktree_path, setup_dir, config)`
      - 4 步串联:link → copy → hooks → gitignore
      - link:配置 `link_paths`(`os.symlink`,Windows
        fallback `mklink /J`,跨盘符 warn + 跳过)
      - copy:`shutil.copytree(dirs_exist_ok=True)` 失败 warn + skip
      - hooks:`git config core.hooksPath <relative ../_hooks/>` +
        建共享 `_hooks/` 目录空
      - gitignore:读缺 `.worktrees/` 插,vim `\.worktrees/?$` 正则
- [x] 4.2 测试`tests/test_worktree_initializer.py` 15 个 case
      (config 3 / link 3 / copy 4 / hooks 1 / gitignore 4)

## 5. 后台清理(`WorktreeCleanupDaemon`)

- [x] 5.1 `baozicode/worktree/cleanup.py` — `WorktreeCleanupDaemon` 类
      - `__init__(manager, task_tracker_getter, retention_minutes, interval_seconds)`
      - `start() / stop()` —— start 创建 asyncio.task,stop 设置 stop
        event 等 task 自然 exit
      - `run_once() -> list[CleanupAction]` —— 三层过滤(D9),
        返回 `CleanupAction(SKIPPED/BLOCKED/CLEANED)`
      - 私有方法 `_list_candidates()` /
        `_is_task_active(task_id)` / `_filter_three_layers(candidate)`
- [ ] 5.2 `BaoZiCodeApp._on_mount` 末尾 `await daemon.start()`;
      `_on_unmount` `daemon.stop()` *(推迟到 task 7)*
- [x] 5.3 测试`tests/test_worktree_cleanup.py` 12 个 case
      (三层过滤 5 / 行为 5 / Protocol 2)

## 6. SubAgent Frontmatter 加 `isolation` 字段

- [ ] 6.1 `baozicode/agents/schema.py` —— `AgentFrontmatter` 加
      ```python
      isolation: Literal["worktree"] | None = None
      ```
      Pydantic 强校验 enum
- [ ] 6.2 测试`test_agent_isolation_field.py` ~3 个(合法值 / 拼错 Pydantic 报 /
      默认 None)

## 7. SubAgentRuntime.spawn 分支

- [ ] 7.1 `baozicode/agents/runtime.py` —— 在 `spawn` 的早期加互斥
      断言(D4)+ 加 `isolation` 处理分支:
      - 检测 `role_def.frontmatter.isolation == "worktree"`
      - 是 → 调 `WorktreeManager.create(name)` 拿
        `WorktreeSpec.path` 作为 `effective_project_root`
      - 构造 sub-Agent 实例时 `project_root=effective_project_root`
      - 把 `WorktreeState` 注入 `sub_agent._worktree_state`
- [ ] 7.2 `_build_definition_prompt` 在 fork / definition 之外加
      一档:`_build_isolated_prompt(role_def, effective_root, ...)`
      把 `env_info` 段的 cwd 渲染成 effective_root 路径
- [ ] 7.3 测试`test_subagent_v13_isolation.py` ~5 个(definition + isolation
      happy / fork + isolation 报错 / isolation 缺省 None /
      `effective_project_root` 传到 Agent / BuiltPrompt env_info 段含
      worktree 路径)

## 8. SubAgentManager 集成

- [ ] 8.1 `baozicode/agents/manager.py` —— `_run_subagent` 的 finally 块
      - 终态(`done/failed/canceled`)后读 `task.agent._worktree_state`
      - `active` → 调 `WorktreeManager.exit(name)`;`policy` 决定保留(
        dirty/未推送)/删除(干净)
      - `force=True` 选项(TUI cancel 时手动传)
- [ ] 8.2 新增 `TaskInfo._worktree_state: WorktreeState | None = None`
- [ ] 8.3 测试 `tests/test_subagent_v13_lifecycle.py` ~4 个
      (无变更删 / 未提交改保留 / 未推送 commit 保留 / force=True 强
      删)

## 9. Bash 工具加 `cwd` 参数

- [ ] 9.1 `baozicode/tools/bash.py` —— `Bash.execute` 加
      `cwd_override = arguments.get("cwd")` 路径:
      - None → `plan_cd` + 更 `_sessions.cwd`(现有 v1.2 行为,零变化)
      - 非 None → `subprocess` 直接 `cwd=...`,不调 plan_cd 不更
        `_sessions.cwd`
      - 加 `set_cwd_validator(callable)` module-level API;D11 的
        closure 注入由 `SubAgentManager._run_subagent` 调用
- [ ] 9.2 Bash `ToolDefinition.parameters` schema 加 `cwd` optional
      字段
- [ ] 9.3 测试`test_bash_cwd_override.py` ~3 个(happy / 不传走旧
      / cwd 越界拒)

## 10. 配置 schema 扩展

- [ ] 10.1 `baozicode/config/schema.py` —— `WorktreeConfig` Pydantic
      + `SubAgentsConfig.worktree: WorktreeConfig | None = None` 字段
- [ ] 10.2 `config.example.yaml` 加完整 `subagents.worktree:` 示例
      含 `link_paths` / `copy_paths` / `retention_minutes` /
      `daemon_interval_seconds` / `max_concurrent_worktrees`
- [ ] 10.3 测试`test_config_worktree_block.py` ~2 个(缺省默认 /
      字段校验)

## 11. App 装配

- [ ] 11.1 `baozicode/app.py` ——
      - 新增 `_build_worktree_manager() -> WorktreeManager`
      - `on_mount`:在 `_build_subagent_manager` 之后调
        `_build_worktree_manager`,传给 `SubAgentManager.__init__`
        ;启动 `WorktreeCleanupDaemon`
      - `on_unmount`:`daemon.stop()` + `await
        manager.remove_all(force=True)` 清掉所有 active worktree
- [ ] 11.2 `baozicode/tui/chat_screen.py` ——
      sub-Agent 卡片渲染时:`task.effective_project_root` 行(显
      示 `cwd: <repo>/.worktrees/<name>/`)
- [ ] 11.3 测试`test_app_v13_wire.py` ~3 个(worker 启动顺序 /
      daemon 开停 / on_unmount 清场)

## 12. 文档 + README

- [x] 12.1 `docs/migrations/v1.2-to-v1.3.md` —— 迁移指南 +
      cache 行为变化 + Bash `cwd` 兼容(50 行内)
- [x] 12.2 `README.md` "SubAgent Delegation" 段加 `isolation: worktree`
      示例 + 一个 `phase1/api-designer` 嵌套命名样例
- [x] 12.3 更新 `CHANGELOG.md` 增 v1.3.0 段
- [x] 12.4 更新项目根 `CLAUDE.md` "v1.X 范围"段加 v1.3

## 13. 集成测试

- [x] 13.1 `tests/integration/test_worktree_e2e.py` ——
      端到端:在 fixture git repo 里跑一个 isolation: worktree
      的 definition sub-Agent,验证:
      - worktree 目录在 `.worktrees/<name>/` 创建
      - sub-Agent 写的文件**不**出现在主 repo
      - sub-Agent 跑完无变更自动清掉
      - dirty 时保 `.worktrees/` + TUI 可看到 detached 状态
- [x] 13.2 `tests/integration/test_concurrent_worktrees.py` ——
      两个并行 isolation sub-Agent 写不同文件,互不冲突;merge
      到主 repo 时由 git 决策

## 14. Review + Release

- [x] 14.1 全量 `pytest tests/ -v` 通过
- [x] 14.2 全量 OpenSpec 校验通过(`openspec validate v1-3-worktree-isolation`)
- [x] 14.3 CHANGELOG + CLAUDE.md + README 同步
- [x] 14.4 `git commit -m "feat(v1.3): worktree isolation for sub-agents"`
