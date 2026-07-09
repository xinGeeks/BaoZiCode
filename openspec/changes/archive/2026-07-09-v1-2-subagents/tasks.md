# Tasks — v1.2 SubAgent Delegation

## 1. Spec 文档落地（必须先做 — 后面所有实现按 spec 来）

- [ ] 1.1 创建 `openspec/changes/2026-07-09-v1-2-subagents/` 目录
- [ ] 1.2 写 `agent-registry/spec.md`（frontmatter + 4 级扫描 + MCP plugin）
- [ ] 1.3 写 `agent-runtime/spec.md`（runtime + 状态隔离 + 4 层 tool filter）
- [ ] 1.4 写 `subagent-manager/spec.md`（manager + 状态机 + 结果回流 + `task` 工具 + cascade cancel）
- [ ] 1.5 修改 `skill-execution/spec.md`（追加 independent 走 SubAgent 通道的 DELTA scenario）
- [ ] 1.6 修改 `hooks-lifecycle/spec.md`（追加 subagent payload 字段 requirement）
- [ ] 1.7 写 `proposal.md` + `design.md` + `tasks.md`(本文档)

## 2. Schema + Registry（`baozicode/agents/` 新包）

- [ ] 2.1 `baozicode/agents/__init__.py` — 公开 API re-export
- [ ] 2.2 `baozicode/agents/schema.py` — `AgentFrontmatter` Pydantic model + `AgentDef` frozen dataclass + `parse_agent(md_text, file_path)` + `ScanError` dataclass
- [ ] 2.3 `baozicode/agents/registry.py` — `AgentRegistry.scan(builtin_dir, user_dir, project_dir, plugin_agents=[], valid_tools=...)` + `lookup` / `list_visible` / `list_all` / `reload` / `scan_errors` 属性
- [ ] 2.4 `baozicode/agents/loader.py` — `substitute_placeholders(body, args)`（复用 Skill 解析算法）
- [ ] 2.5 `baozicode/agents/builtin/explorer/AGENT.md` — 样板（definition 模式,tools=[Read,Grep,Glob,WebFetch],permission-mode=permissive）
- [ ] 2.6 `baozicode/agents/builtin/summarizer/AGENT.md` — 样板（definition 模式,tools=[Read,Grep,Glob],model=haiku,permission-mode=permissive）

## 3. MCP Plugin 拉取

- [ ] 3.1 `baozicode/agents/plugin.py` — `fetch_plugin_agents(mcp_manager) -> list[AgentDef]`
  - 遍历 `mcp_manager.states` 找 `status=="ready"` 的 server
  - `read_resource("agents://list")` 拿目录
  - 逐个 `read_resource("mcp://<server>/agents/<name>")` 拿 detail
  - 解析成 `AgentDef(source="plugin", path=Path(f"<mcp://{server}/{name}>"))`
  - 任何异常 → 跳过 + 标 scan_errors,不阻断
- [ ] 3.2 修改 `McpClientManager` 状态机：state 加 `resources: dict[uri, content]` 缓存
- [ ] 3.3 测试: 启动 mock MCP server(暴露 2 个 agent),验证 fetch_plugin_agents 返回 2 个 AgentDef

## 4. Runtime + ToolFilter

- [ ] 4.1 `baozicode/agents/filter.py` — `ToolFilter` 类（4 层 AND + `ToolFilterEmptyError` + `visible_tools` cached_property + `GLOBAL_DENY = {"task"}`）
- [ ] 4.2 `baozicode/agents/runtime.py` — `SubAgentRuntime` 类
  - `__init__(llm, hooks, tool_registry, project_root, config)`
  - `spawn(*, task_id, type, role_def, prompt, parent_messages, parent_denied_counts, parent_agent=None) -> Agent`
  - 状态隔离 4 件套（新 ConversationManager / 新 UsageStats / 新 MergedPermissions / 新 RuleEngine）
  - fork 模式复用 `parent_agent._prompt`(共享 BuiltPrompt,byte-identical cache key)
  - definition 模式 rebuild 一份新 BuiltPrompt(role.body 替换 identity section)
  - `_subagent_meta` dict 注入到 Agent 实例
- [ ] 4.3 `TASK_TOOL` ToolDefinition 定义 + `task_executor` 同步函数(wrapper `await SubAgentManager.dispatch(...)`)
- [ ] 4.4 测试: 4 层 tool filter 5+ 个 case / definition vs fork 构造差异 / fork 共享 _prompt 引用同一对象

## 5. SubAgentManager

- [ ] 5.1 `baozicode/agents/manager.py` — `TaskInfo` dataclass
  - `task_id / type / role / prompt / state / created_at / started_at / finished_at / agent / result / error / usage / cancel_event / notification_pending`
- [ ] 5.2 `SubAgentManager` 类
  - `__init__(*, max_concurrent=5, runtime, main_conversation, main_agent_ref=None)`
  - `dispatch(*, type, role, prompt, async_, timeout_seconds, parent_conversation, parent_denied_counts) -> str`
    - 同步路径: `await self._run_subagent(task)` + 超时切后台(D8)
    - 异步路径: `asyncio.create_task(self._run_subagent(task))` + 返回 task_id
    - **Fork 强制后台**:`if type == "fork" and not async_` → warning log + 强制 async
  - `cancel_all()` — 主 Agent cancel 时调,所有 running task 设 cancel_event
  - `get_task(task_id) -> TaskInfo | None`
  - `list_tasks() -> list[TaskInfo]`
  - `drain_pending_notifications() -> list[TaskInfo]`
  - `_run_subagent(task)` — 实际跑:`agent = self._runtime.spawn(...)` + `async for event in agent.run(task.prompt)` + 收集最后一条 text 作为 summary + 调 `_on_subagent_done(task)`
  - `_on_subagent_done(task)` — 状态机收尾 + 结果回流路由(D7)
  - `_format_summary(task) -> str` — `f"[{role or 'fork'}] {summary}\n(usage: {in}/{out})"`
  - `_format_reminder(task) -> str` — `<system-reminder type="subagent_result" ttl="once">...</system-reminder>`
  - 主 Agent idle 检测: `main_agent_ref._cancel_event.is_set() == False AND not main_agent_ref._conversation.add_user in last 1s`(简单实现: 检查 main_agent_ref 是否有 `_current_iteration_running` 标志)
- [ ] 5.3 测试:
  - 5 种 dispatch 场景(def sync / def async / fork async / fork sync 强制后台 / tool_filter 空集拒绝)
  - 状态机 5 个状态转换
  - 结果回流 idle vs 跑中
  - cascade cancel
  - 3 种进入后台(显式 / 超时 / 手动 stub)

## 6. 集成到 Agent + App

- [ ] 6.1 `baozicode/agent/loop.py:Agent.__init__` 加 `subagent_manager: SubAgentManager | None = None` kwarg
- [ ] 6.2 `Agent.cancel()` 加级联:`if self._subagent_manager: self._subagent_manager.cancel_all()`
- [ ] 6.3 `Agent._fire_lifecycle_safe` payload 加 `subagent` 字段(主 Agent `subagent=None` → payload 不变)
- [ ] 6.4 `Agent._inject_reminders` 加 subagent_result reminder kind(走 enqueue_reminder 通道,ttl=once)
- [ ] 6.5 `baozicode/app.py:BaoZiCodeApp.__init__` 加 `self.subagents: SubAgentManager = SubAgentManager(...)` 装配(llm/hooks/tool_registry/project_root 来自 self 各字段)
- [ ] 6.6 `BaoZiCodeApp._register_task_tool` on_mount worker — 注册 `TASK_TOOL` 到模块级 ToolRegistry(幂等)
- [ ] 6.7 `BaoZiCodeApp._build_agent` 或类似 hook 注入 `subagent_manager=self.subagents` 到 Agent 构造
- [ ] 6.8 `BaoZiCodeApp.start_new_session` 清空 `self.subagents._tasks`(新 session 不带旧 sub-Agent)
- [ ] 6.9 测试: App 启动后 subagents 字段非 None / task 工具在 `get_all_tools()` 里

## 7. Skills 改造（独立模式走 SubAgent 通道）

- [ ] 7.1 `baozicode/skills/execution.py`:
  - 删 `IndependentRunner` type alias
  - 删 `SkillExecutor.__init__` 的 `independent_runner` 参数
  - 改 `SkillExecutor.execute` 的 `independent` 分支:`self._subagent_manager.dispatch(...)` + wait for done + 把 task.result 填到 SkillExecutionResult.summary
  - `SkillExecutionResult` 加 `raw_output: str | None = None` 字段(子对话完整输出,v1.0 已有,确认不动)
- [ ] 7.2 `baozicode/app.py:BaoZiCodeApp.__init__` 改 `SkillExecutor(loader, activation, subagent_manager=self.subagents)`
- [ ] 7.3 `baozicode/tui/chat_screen.py` 删 `_setup_skill_runner` 相关代码
- [ ] 7.4 测试:
  - 旧 test_skills_v10.py 跑通(用户视角行为不变)
  - 新 test: `mode: independent` 走 SubAgentManager(inspect subagent_manager._tasks)
  - 新 test: `/review` 跑完 summary 仍回主对话

## 8. Config + Schema

- [ ] 8.1 `baozicode/config/schema.py` 加 `SubAgentsConfig` Pydantic model
  ```python
  class SubAgentsConfig(BaseModel):
      enabled: bool = True
      max_concurrent: int = Field(default=5, ge=1, le=20)
      default_timeout_seconds: int = Field(default=300, ge=10, le=3600)
      background_whitelist: list[str] = Field(
          default_factory=lambda: ["Read", "Grep", "Glob", "WebFetch", "notify_complete"]
      )
      builtin_dir: Path | None = None  # 走默认 <pkg>/baozicode/agents/builtin
      user_dir: Path | None = None     # 走默认 ~/.config/baozicode/agents
      project_dir: Path | None = None  # 走默认 <root>/.baozicode/agents
      plugins_enabled: bool = True
      task_retention_minutes: int = Field(default=5, ge=1, le=60)
  ```
- [ ] 8.2 `AppConfig` 加 `subagents: SubAgentsConfig` 字段
- [ ] 8.3 `config.example.yaml` 加 `subagents:` 块示例
- [ ] 8.4 测试: AppConfig 加载 `subagents:` 块 / 默认值正确 / 旧配置(无 subagents 块) 默认全开

## 9. TUI（status bar + 折叠卡片）

- [ ] 9.1 `baozicode/tui/chat_screen.py` status bar 加 `[agents: Nr/Nd]` 标记(从 `app.subagents.list_tasks()` 实时统计)
- [ ] 9.2 `baozicode/tui/subagent_card.py`(新文件)— `SubAgentCard` widget(展开看 sub-Agent streaming text,折叠只看 task_id + role + state)
- [ ] 9.3 `chat_screen` 订阅 sub-Agent event 流(`SubAgentManager` 暴露 `subscribe(task_id) -> AsyncIterator[AgentEvent]`),把 event 转发到对应 SubAgentCard
- [ ] 9.4 折叠卡片放在主对话历史下方(独立 scrollable container),点击展开 / 折叠
- [ ] 9.5 完成 toast:`App.notify` 调一次,fade in/out 0.5s
- [ ] 9.6 测试: 5 个 sub-Agent 跑时 status bar 显示 `5r/0d` / 卡片展开看 streaming / 完成 toast 出现

## 10. 集成测试 + 文档

- [ ] 10.1 端到端测试:`tests/test_subagents_e2e.py` — 真 LLM API(可选 skip),主 Agent 调 `task` 工具派 explorer,等结果
- [ ] 10.2 性能测试: 验证 fork 模式 cache 命中(看 UsageStats.cache_read_tokens > 0)
- [ ] 10.3 `README.md` 加「SubAgent」段 + 2 个样板 agent 介绍
- [ ] 10.4 `docs/migrations/v1.1-to-v1.2.md` — 迁移指南
- [ ] 10.5 `config.example.yaml` 完整示例
- [ ] 10.6 `openspec validate v1-2-subagents --strict` 通过

## 11. 收尾

- [ ] 11.1 `openspec archive v1-2-subagents`
- [ ] 11.2 bump version 1.2.0 → 1.3.0
- [ ] 11.3 release notes 草稿
- [ ] 11.4 全量回归 + 新测试覆盖 ≥ 80%
