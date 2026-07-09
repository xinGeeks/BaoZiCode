# Design — v1.2 SubAgent Delegation

## Context

v1.0 Skills 提供了 `mode: independent`（`baozicode/skills/execution.py`），可以
"开一个子对话跑完 SOP"，但有以下结构性限制：

- **触发入口是用户**（`/review --since=...`），LLM 没法自己派 sub-Agent
- **`history-bubbles` 是结构破坏**——把主对话后 N 条 message 拷给子 Agent，
  tool_use_id 重新生成、cache prefix 断裂
- **没有后台 / 状态追踪**——同步等子 Agent 跑完才返回，主 Agent 整个阻塞
- **没有 tool 过滤**——子 Agent 跑得跟主 Agent 一样野，没有强制沙箱
- **没有嵌套控制**——理论上子 Agent 能再 spawn 子 Agent（无防御）

更关键的是 v1.1 已经留下 hooks lifecycle 基础设施 + `BaoZiCodeApp` 上的
wire 模式（`hook_dispatcher` / `skill_filter` / `skill_activation` 都是
`Agent.__init__` 的 optional kwarg），v1.2 只需要照葫芦画瓢再加一个
`subagent_manager` kwarg + 一组新 spec 即可。

完整设计见本文件各 Decision 段。

## Goals / Non-Goals

**Goals:**

- 主 Agent 任何时候能调 `task(type, role, prompt, **opts)` 派 sub-Agent
- sub-Agent 状态完全隔离（messages / permission / file read cache / token 计数）
- 基础设施共享（LLMClient / HookDispatcher / ToolRegistry / FileSystem）
- `async=true` 默认后台跑；`async=false` 同步等（Fork 强制后台）
- 4 层工具过滤 AND + L1 全局 ban `task` 防嵌套
- Skills `mode: independent` 内部走 SubAgent 通道，零行为变化（用户视角）
- 角色 markdown + frontmatter，跟 Skills 平级不混
- MCP-driven plugin 拉取（不动 MCP spec，走 `resources/read` 复用）
- sub-Agent 跑完结果异步回流主 Agent（idle=立即 user 消息 / 跑中=下轮 reminder）
- 主 Agent cancel 级联到所有 sub-Agent；sub-Agent 失败不向上传染

**Non-Goals:**

- 嵌套 sub-Agent 调 sub-Agent（v1.2 物理禁止；future 加 `nesting-depth` 字段）
- cross-session sub-Agent 状态持久化（sub-Agent 跟主 session 绑定）
- sub-Agent 自己的会话存档到独立 JSONL（sub-Agent 内部消息存内存，**不**
  写 `sessions/<sid>.jsonl`——避免污染主 session 存档）
- 跨 sub-Agent 的状态共享（每个 sub-Agent 完全独立，不共享 token 计数 / 权限
  counter）
- sub-Agent 的 sub-skill 加载（sub-Agent 也能激活 Skill，但**不再**能激活
  自己的 sub-Skill——v1.2 直接禁止，v1.3 再考虑）
- watchdog 文件监控 agent 热更新（v1.2 走显式 `AgentRegistry.reload`）
- sub-Agent 调 LLM 的 rate limit 限速（v1.2 留给 hooks / v1.3）
- sub-Agent 调 LLM 失败重试（v1.2 fail-fast；v1.3 评估）

## Decisions

### D1: 一个 `task` 工具带 `type` 参数

**决策**:ToolDefinition `task` 注册到模块级 `ToolRegistry` 单例,参数:

```python
{
  "name": "task",
  "description": "派一个子 Agent 干子任务。type=definition 用干净上下文+角色化;type=fork 继承主对话借 cache。async=true(默认)后台跑;async=false 同步等。",
  "input_schema": {
    "type": "object",
    "properties": {
      "type": {"type": "string", "enum": ["definition", "fork"]},
      "role": {"type": "string", "description": "角色名(仅 definition 模式需要)"},
      "prompt": {"type": "string", "description": "派给子 Agent 的任务描述"},
      "async": {"type": "boolean", "default": True},
      "timeout_seconds": {"type": "integer", "default": null, "description": "async=false 时,超时自动切后台(默认=SubAgentsConfig.default_timeout_seconds)"}
    },
    "required": ["type", "prompt"]
  },
  "tool_type": "internal"  # 跟 load_skill 一样,不受 Skill 白名单约束
}
```

**实现要点**:
- `baozicode/agents/runtime.py:TASK_TOOL` 定义 + `task_executor` 函数
  (同步 wrapper → 内部 `await SubAgentManager.dispatch(...)`)
- `BaoZiCodeApp._register_task_tool` 在 `on_mount` worker 里注册
  (跟 `_register_load_skill_tool` 同 pattern)
- 撞名 ValueError 视为已注册(幂等)

**为什么 type 是参数不是拆工具**:你 brief 明文要求"工具列表始终稳定",且
`type` 是 enum 2 个值,LLM 学一次就够,比学 2 个动词省 prompt 空间。

**为什么 type 参数值名是 `definition`/`fork` 不缩写**:LLM 拼错会 silent
accept 走 default,降低可观测性;**全词拼错马上 enum validation 失败**。

### D2: AgentFrontmatter 复用 Skill 解析器风格,新 Pydantic model

**决策**:

```python
# baozicode/agents/schema.py
class AgentFrontmatter(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str                         # 必填,^[a-z][a-z0-9-]*$
    description: str                  # 必填
    tools: list[str] | None = None    # 可选,默认 None = 不收窄
    tools_deny: list[str] | None = Field(default=None, alias="tools-deny")
    model: Literal["inherit", "haiku", "sonnet", "opus"] | None = None  # 默认 inherit
    max_iterations: int = Field(default=20, alias="max-iterations", ge=1, le=100)
    permission_mode: Literal["strict", "default", "permissive"] | None = None
    nesting_depth: int = Field(default=0, alias="nesting-depth", ge=0, le=3)
    hidden: bool = False
    # 不需要 mode 字段——Agent 永远是 independent
```

**实现要点**:
- `parse_agent(md_text, file_path)` 函数 = `parse_frontmatter` 同样的行扫描
  + YAML 解析,只是 Pydantic model 换成 `AgentFrontmatter`
- 不**继承** `SkillFrontmatter`(Pydantic v2 BaseModel 继承会让
  `extra="ignore"` 行为微妙),改成**复制** frontmatter 解析逻辑 + 复用
  `yaml.safe_load` 路径
- `AgentDef` frozen dataclass = frontmatter + body + source + path

**为什么不**用单 Pydantic model 区分 Skill vs Agent**:frontmatter 字段差异
虽然小,但"nesting-depth 在 Skill 里没意义"会让校验变难表达;独立 model 让
两个 spec 的 Pydantic schema 各自干净,文件级 vs 共享 model 选独立 model。

### D3: 4 级加载优先级 + plugin = MCP 驱动

**决策**:

```
加载顺序(后扫覆盖先扫):
  1. builtin    <pkg>/baozicode/agents/builtin/<name>/AGENT.md
  2. user       ~/.config/baozicode/agents/<name>/AGENT.md
  3. project    <root>/.baozicode/agents/<name>/AGENT.md
  4. plugin     MCP server 在 <mcp-resource-uri> 暴露的 agent

同名后者覆盖前者(整段覆盖,跟 Skills 一致)。
```

**plugin 拉取实现**:

```python
# baozicode/agents/plugin.py
async def fetch_plugin_agents(mcp_manager: McpClientManager) -> list[AgentDef]:
    """对每个连接的 MCP server 拉取 agents/list 资源,转成 AgentDef。

    复用 MCP `resources/read` 协议——agent 作为 type=agent 的 resource:
    - URI 形如 `mcp://<server>/agents/<name>`
    - resource contents 返回 { frontmatter: {...}, body: "..." }
    - 任何 server 拉取失败 → 跳过该 server + 标 scan_errors
    """
    agents = []
    for server_name, state in mcp_manager.states.items():
        if state.status != "ready":
            continue
        try:
            list_result = await state.session.read_resource("agents://list")
            for entry in list_result.contents:
                uri = entry["uri"]  # mcp://<server>/agents/<name>
                name = entry["name"]
                desc = entry["description"]
                # 再 read_resource(name) 拿 frontmatter + body
                detail = await state.session.read_resource(uri)
                fm = AgentFrontmatter.model_validate(detail["frontmatter"])
                agents.append(AgentDef(
                    frontmatter=fm, body=detail["body"],
                    source="plugin", path=Path(f"<mcp://{server_name}/{name}>"),
                ))
        except Exception as exc:
            log.warning("MCP plugin %s agent 拉取失败: %s", server_name, exc)
            scan_errors.append(ScanError(path=f"<mcp://{server_name}>", reason=str(exc)))
    return agents
```

**为什么不动 MCP spec**:MCP `resources/read` 已经是通用资源拉取协议;我们
把 agent 当作 `type=agent` 的 resource,server 端实现"agent resource provider"
即可,client 端无需新方法。**对 v1.0 MCP server 完全兼容**(没暴露 agent
resource 的 server,`read_resource("agents://list")` 返回空列表)。

**boot 顺序**:`McpClientManager` bootstrap 完成 → `AgentRegistry.scan` 调
`fetch_plugin_agents(mcp_manager)` 拿到 plugin agents → 合入 registry。
**注意**:MCP 连接是异步且可能失败,AgentRegistry.scan 要在 MCP bootstrap
**之后**跑;v1.2 改 `BaoZiCodeApp.on_mount` 的 worker 顺序,或者把
AgentRegistry.scan 也推到 on_mount worker 里跑(跟 `_bootstrap_mcp` 并行,
依赖 `asyncio.gather`)。

### D4: SubAgentRuntime 状态隔离

**决策**:每个 sub-Agent 实例化时,`SubAgentRuntime.spawn` 构造**全新**的
per-task 状态:

```python
# baozicode/agents/runtime.py
class SubAgentRuntime:
    def __init__(
        self,
        llm_client: LLMClient,
        hook_dispatcher: HookDispatcher | None,
        tool_registry: ToolRegistry,
        project_root: Path,
        config: AppConfig,
    ):
        self._llm = llm_client            # 共享
        self._hooks = hook_dispatcher     # 共享(但 fire 时 payload 带 subagent 字段)
        self._tools = tool_registry        # 共享(但 sub-Agent 看到的视图被 filter 收窄)
        self._project_root = project_root
        self._config = config

    def spawn(
        self,
        *,
        task_id: str,
        type: Literal["definition", "fork"],
        role_def: AgentDef | None,        # definition 模式填,fork 模式 None
        prompt: str,
        parent_messages: list[Message],   # fork 模式填(完整 snapshot)
        tool_filter: ToolFilter,          # 已应用 4 层过滤
        parent_denied_counts: dict[str, int],  # fork 模式继承父 denied_counts
    ) -> Agent:
        """构造 sub-Agent 实例,返回 headless Agent(没 TUI 订阅)。"""
        # 新 ConversationManager(无 archiver——sub-Agent 消息不写主 JSONL)
        conversation = ConversationManager(archiver=None)
        if type == "fork":
            for msg in parent_messages:
                conversation.add_message(msg)  # 完整 snapshot
            # fork 不增加额外 user 消息——prompt 由 task tool 的 executor
            # 调 agent.run(prompt) 触发,append 到 conversation 末尾
        # definition 模式:空 conversation,prompt 进来时由 add_user 加

        # 新 permission counter
        # 构造 per-task MergedPermissions(继承父 session_rules,deny/allow 跟主同步)
        merged = MergedPermissions(
            rules=self._config.permissions_v5.rules,
            mode=role_def.frontmatter.permission_mode or self._config.permissions_v5.mode,
            real_root=self._project_root,
            path_sandbox_enabled=True,
            session_rules=parent_denied_counts,  # 共享 — 父 SESSION 按钮放行子也放行
        )
        engine = RuleEngine(merged=merged)

        # 新 token 计数
        usage = UsageStats()

        # 构造 Agent 实例
        agent = Agent(
            llm_client=self._llm,
            tools=tool_filter.visible_tools,  # 收窄后的视图
            conversation=conversation,
            permissions=None,
            config=self._config,
            max_iterations=role_def.frontmatter.max_iterations,
            plan_mode=False,                  # sub-Agent 不进入 plan mode
            permission_callback=None,         # sub-Agent 没 L5 user 弹窗
            session_mode=merged.mode,
            merged_permissions=merged,
            permissions_engine=engine,
            compact_ctx=None,                 # sub-Agent 不做 v0.7 自动压缩
            instructions_text="",             # sub-Agent 不继承父 instructions
            project_root=self._project_root,
            skill_filter=None,                # sub-Agent 不再激活 skill
            skill_activation=None,
            skill_registry=None,
            hook_dispatcher=self._hooks,
            subagent_meta={                   # 注入 metadata
                "task_id": task_id,
                "role": role_def.name if role_def else None,
                "type": type,
                "depth": 1,                   # v1.2 永远 1(L1 deny 物理禁嵌套)
            },
        )
        return agent
```

**为什么 fork 模式不"新建" role body 作 system prompt**:fork 的本意是
**复用父对话 + 借 cache**;system prompt 跟主 Agent 保持**byte-identical**,
LLM API 自动 cache 命中(system + 前 N 条 message 都是 cache key)。

**为什么 `compact_ctx=None`**:sub-Agent 跑得短(默认 max_iterations=20),
不需要自动压缩;主 Agent 的压缩决策不会传递。**用户视角**:sub-Agent
一次跑完不持久化,无需压缩档。

**为什么 `permission_callback=None`**:sub-Agent 没有 TUI Modal 弹窗能力
(headless);L4 mode 兜底(permissive → fallthrough allow;default →
fallthrough → L4 mode allow;strict → fallthrough deny)。**用户配置
permission_mode=permissive 实际上是让 sub-Agent 跳过所有 L5 user 决策**。

### D5: 4 层 ToolFilter (AND)

**决策**:`baozicode/agents/filter.py:ToolFilter` 一次性算出 sub-Agent
可见工具集:

```python
class ToolFilter:
    GLOBAL_DENY = frozenset({"task"})  # L1 永远 ban

    def __init__(
        self,
        tool_registry: ToolRegistry,
        role_def: AgentDef | None,
        is_background: bool,            # async=true 时 True
        background_whitelist: list[str], # AppConfig.subagents.background_whitelist
    ):
        self._registry = tool_registry
        self._role_allow = set(role_def.frontmatter.tools) if role_def and role_def.frontmatter.tools else None
        self._role_deny = set(role_def.frontmatter.tools_deny) if role_def and role_def.frontmatter.tools_deny else set()
        self._is_background = is_background
        self._bg_whitelist = set(background_whitelist)

    @cached_property
    def visible_tools(self) -> list[ToolDefinition]:
        all_tools = self._registry.get_all_tools()
        visible = []
        for t in all_tools:
            # L1: 全局 deny
            if t.name in self.GLOBAL_DENY:
                continue
            # L2: role allow(None = 不收窄,跳过)
            if self._role_allow is not None and t.name not in self._role_allow:
                continue
            # L3: role deny
            if t.name in self._role_deny:
                continue
            # L4: background whitelist(async=true 时启用)
            if self._is_background and t.name not in self._bg_whitelist:
                continue
            visible.append(t)
        if not visible:
            raise ToolFilterEmptyError(
                f"sub-Agent 工具过滤后为空集:role={role_def.name if role_def else 'fork'},"
                f"background={self._is_background},"
                f"role_allow={self._role_allow}, role_deny={self._role_deny},"
                f"bg_whitelist={self._bg_whitelist}"
            )
        return visible
```

**空集处理**:`ToolFilterEmptyError` 在 `SubAgentManager.dispatch` 入口
catch,返回 `ToolResult(is_error=True, content="<详细原因>")` 给 LLM,让
它自己调整参数 / 选别的 role。

**为什么 background_whitelist 是配置项不是 frontmatter**:运营灵活——用户
可以在不改 agent 文件的情况下收紧/放宽后台工具集;默认
`[Read, Grep, Glob, WebFetch, notify_complete]`(notify_complete 是
sub-Agent 用来主动通知主 Agent 完成的内部工具)。

### D6: SubAgentManager 状态机

**决策**:sub-Agent 任务有 5 种状态:

```
            ┌──────────┐
            │ pending  │ (dispatch 后立即)
            └────┬─────┘
                 │ runtime.spawn() 完成,manager 调度
                 ▼
            ┌──────────┐
            │ running  │ (Agent.run 进行中)
            └────┬─────┘
                 │
       ┌─────────┼─────────┬──────────┐
       ▼         ▼         ▼          ▼
   ┌───────┐ ┌────────┐ ┌─────────┐ ┌────────┐
   │ done  │ │ failed │ │canceled │ │timeout │ (超时自动切后台只更新状态机;见 D8)
   └───────┘ └────────┘ └─────────┘ └────────┘
```

**TaskInfo dataclass**:

```python
@dataclass
class TaskInfo:
    task_id: str                          # 形如 "task-2026-07-09-abc123"
    type: Literal["definition", "fork"]
    role: str | None                      # definition 模式的 role 名
    prompt: str
    state: Literal["pending", "running", "done", "failed", "canceled", "timeout"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    agent: Agent | None                   # running 状态时填
    result: str | None                    # 完成的 summary
    error: str | None                     # 失败 / 取消的错误
    usage: UsageStats                     # sub-Agent 自己累计的 token
    cancel_event: asyncio.Event           # 主 Agent 取消时 set 这个
    notification_pending: bool            # 跑中完成 → True,等主 Agent 下一个迭代顶部 inject
```

**Manager API**:

```python
class SubAgentManager:
    def __init__(self, *, max_concurrent=5, ...):
        self._tasks: dict[str, TaskInfo] = {}
        self._lock = asyncio.Lock()
        self._main_message_queue: asyncio.Queue  # 给主 Agent 的回流通道

    async def dispatch(
        self,
        *,
        type: Literal["definition", "fork"],
        role: str | None,
        prompt: str,
        async_: bool,                      # 'async' 是 Python keyword
        timeout_seconds: int | None,
        parent_conversation: ConversationManager,
        parent_denied_counts: dict[str, int],
        main_agent_ref: "Agent | None" = None,  # 用于 enqueue_reminder 通道
    ) -> str:                              # 同步模式:返回 summary 文本;异步模式:返回 task_id
        """dispatch 一个 sub-Agent。同步阻塞/异步后台按 async_ 走。"""

    def cancel_all(self) -> None:          # 主 Agent cancel 时调
        for t in self._tasks.values():
            if t.state == "running":
                t.cancel_event.set()
                t.state = "canceled"

    def get_task(self, task_id: str) -> TaskInfo | None: ...
    def list_tasks(self) -> list[TaskInfo]: ...
    def drain_pending_notifications(self) -> list[TaskInfo]: ...
        # 主 Agent 主循环顶部调,拿出所有 notification_pending=True 的 task
        # 走 _inject_reminders 注入 system-reminder
```

**最大并发**:`max_concurrent=5` 是软上限——超了 dispatch 直接返回失败,
LLM 收到错误可重试(等几秒后),不会"卡住"主 Agent 死循环重试。

### D7: 结果回流路由(方案 D 简化)

**决策**:

| 主 Agent 状态 | sub-Agent 完成时 | 形式 | 注入点 |
|---|---|---|---|
| idle | 立即 | `Message(role="user", content=f"[<role> 子对话结果]\n{summary}")` | 推给主 conversation 顶部 |
| 跑中(流式) | 排队 | `<system-reminder type="subagent_result" ttl="once">{summary}</system-reminder>` | 主 Agent 下一个迭代顶部 inject |

**实现要点**:

```python
# agents/manager.py:_on_subagent_done(task: TaskInfo)
async def _on_subagent_done(self, task: TaskInfo) -> None:
    """sub-Agent 跑完的回调(无论 done/failed/canceled 都进这里)。"""
    task.finished_at = datetime.now()
    summary = self._format_summary(task)  # "[<role>] <summary text>"

    if self._is_main_agent_idle():
        # idle: 直接 push 到主 conversation
        self._main_conversation.add_user(summary)
    else:
        # 跑中: 入队,主 Agent 下一轮顶部 drain
        task.notification_pending = True
        self._main_message_queue.put_nowait(task)
        # 也 enqueue reminder(主 Agent 用 enqueue_reminder API 拿)
        if self._main_agent_ref is not None:
            reminder_body = self._format_reminder(task)
            self._main_agent_ref.enqueue_reminder("subagent_result", reminder_body)
```

**为什么 reminder kind 是新的 `"subagent_result"`**:复用 v0.8 `enqueue_reminder`
机制,语义独立。`Agent._inject_reminders` 在 §4 段加一段:
"`subagent_result` reminder 走 enqueue_reminder 通道,ttl=once"。

**为什么不是 tool_result 配对**:task 工具的 call_id 是 task_id 配对
sub-Agent 完成的 tool_result 看起来"完整",但 call_id lifecycle 复杂——
主 Agent 后续迭代拿不到(它在 completed 时已经吃掉了 tool_result block),
且 cache 也会因为新 tool_result block 插入而失配前缀。**不**走这条路。

### D8: 三种进入后台的方式

**决策**:

| 触发 | 适用 | 行为 |
|---|---|---|
| 显式 `async=true`(默认) | 全部 sub-Agent | dispatch 后立即返回 task_id;sub-Agent 跑后台 |
| 超时自动 | 仅 `async=false` 同步任务 | `timeout_seconds` 到时 sub-Agent 还没完成 → 设 `state=timeout` + 标记 notification_pending + 返回 task_id(替代原 summary 路径) |
| 手动切 | TUI 在 status bar 按键(留 v1.2 polish) | 用户主动把跑中的 sync 任务切到后台 |
| **Fork 强制后台** | fork 模式 | `async=false` 在 fork 模式被忽略,manager 内部强制 `async_=true` |

**超时自动实现**:

```python
# agents/manager.py:dispatch sync 路径
async def dispatch_sync(self, task, timeout_seconds):
    try:
        summary = await asyncio.wait_for(
            self._run_subagent(task), timeout=timeout_seconds
        )
        task.state = "done"
        task.result = summary
        return summary
    except asyncio.TimeoutError:
        task.state = "timeout"
        task.notification_pending = True
        self._main_message_queue.put_nowait(task)
        # 返回 task_id,主 Agent 看到"task started, will notify when done"
        return f"[sub-Agent 超时自动切后台,task_id={task.task_id}]"
```

**为什么 timeout 默认 = `AppConfig.subagents.default_timeout_seconds`(300s)**:
5 分钟够大多数 sub-Agent 跑完(20 轮迭代、每轮 1-2 个 tool call);真跑超
5 分钟基本是死循环,切后台让主 Agent 继续。

### D9: cascade cancellation + 不向上传染

**决策**:

```python
# agent/loop.py:Agent.cancel
def cancel(self) -> None:
    self._cancel_event.set()
    # v1.2: 级联到所有 sub-Agent
    if self._subagent_manager is not None:
        self._subagent_manager.cancel_all()

# agents/manager.py:SubAgentManager.cancel_all
def cancel_all(self) -> None:
    for task in self._tasks.values():
        if task.state == "running":
            task.cancel_event.set()           # Agent.run 主循环在安全点检查
            task.state = "canceled"
            task.finished_at = datetime.now()
            # 走 _on_subagent_done 走通知路径
            asyncio.create_task(self._on_subagent_done(task))
```

**不向上传染**:sub-Agent 失败 / 取消**不**触发主 Agent `cancel()`——
主 Agent 继续跑自己的循环。失败 / 取消通过 D7 的 reminder 通道通知主
Agent,主 Agent 自己决定下一步(重试 / 调别的 role / 询问用户)。

### D10: Skills `mode: independent` 走 SubAgent 通道

**决策**:`SkillExecutor.execute` 改造:

```python
# skills/execution.py:SkillExecutor.execute
async def execute(self, name, args=None) -> SkillExecutionResult:
    sd = self._loader._registry.lookup(name)
    if sd is None:
        return SkillExecutionResult(ok=False, name=name, mode="shared", summary="未找到")
    load_result = self._loader.load_skill(name, args)
    if not load_result.ok:
        return SkillExecutionResult(ok=False, name=name, mode=sd.frontmatter.mode, summary=load_result.summary)

    mode = sd.frontmatter.mode
    if mode == "shared":
        return SkillExecutionResult(ok=True, name=name, mode="shared", summary=load_result.summary)

    # mode == "independent" — v1.2: 改走 SubAgentManager
    if self._subagent_manager is None:
        return SkillExecutionResult(ok=False, name=name, mode="independent", summary="...")

    # 把 Skill body 渲染 + 调 SubAgentManager.dispatch(type="definition", role=skill.name, ...)
    # SubAgent 用 skill.body 作为 system prompt 的"角色描述"
    try:
        output = await self._subagent_manager.dispatch(
            type="definition",
            role=sd.name,                      # 角色 = skill 名
            prompt=load_result.summary,         # 占位符替换后的内容
            async_=True,                        # Skill 走后台
            timeout_seconds=None,
            parent_conversation=None,            # sub-Agent 跑独立,不带主历史
            parent_denied_counts={},
        )
        # output = task_id(异步)或 summary(同步)— Skill 走异步, output 是 task_id
        # 但 Skill 的旧语义是"等 summary 回来"——需要 wait 完成
        task_info = self._subagent_manager.get_task(output)
        # ... 等待 task_info.state ∈ {done, failed, canceled}
    except Exception as exc:
        return SkillExecutionResult(ok=False, name=name, mode="independent", summary=str(exc))
```

**等等**——这里有个分歧需要再想:

**选项 A**:Skill 的 independent 模式走 SubAgent 通道,但**同步等** summary
回来(保持 v1.0 的「等结果再回流」行为)
**选项 B**:Skill 的 independent 模式走 SubAgent 通道,完全异步——Skill
自身不等待,主 Agent 继续,sub-Agent 完成时 reminder 注入(等于让 Skill 跟
普通 task 调用完全等价)

**v1.2 决定选 A**——保持 v1.0 `/review` 的"等 summary 回来"行为,降低
breaking change 风险。`SkillExecutionResult.summary` 还是同步填好。

**`IndependentRunner` 删除影响**:
- `baozicode/skills/execution.py:IndependentRunner` type alias 删
- `SkillExecutor.__init__` 的 `independent_runner` 参数删
- `BaoZiCodeApp` 的 `_setup_skill_runner` 方法删
- `BaoZiCodeApp.__init__` 改成 `SkillExecutor(loader, activation, subagent_manager=self.subagents)`
- `chat_screen` 的 stub 装配代码删

### D11: Hooks 兼容 — lifecycle event payload 加 `subagent` 字段

**决策**:`Agent._fire_lifecycle_safe(event, payload)` 在 payload 加
`subagent` 字段(主 Agent 自己的事件 `subagent=None`):

```python
def _fire_lifecycle_safe(self, event: str, payload: Any) -> None:
    if self._hook_dispatcher is None:
        return
    # v1.2: 加 subagent 字段标识事件来源
    if isinstance(payload, dict):
        payload = {**payload, "subagent": self._subagent_meta}
    else:
        payload = {"value": payload, "subagent": self._subagent_meta}
    try:
        self._hook_dispatcher.run(event, payload)
    except Exception as exc:
        log.warning(...)
```

**`_subagent_meta` 形态**:
- 主 Agent:`None` → payload 不变(向后兼容)
- sub-Agent:`{"task_id": "task-2026-07-09-abc", "role": "explorer", "type": "definition", "depth": 1}`

**老 hook 行为**:不读 `subagent` 字段零影响;新 hook 可读 `payload["subagent"]`
区分主 vs 子。

**audit log**:合文件(同一个 `hooks/<session>.audit.jsonl`);按 `subagent.task_id`
过滤即可。

### D12: 缓存策略

**决策**:

| 模式 | cache 命中 | 实现 |
|---|---|---|
| definition | 冷启 | sub-Agent 用全新 system prompt(角色 body),不继承父 system |
| fork | **几乎全命中** | sub-Agent 跟主 Agent 共享 system prompt(同一 self._prompt.stable_system)+ 完整 conversation snapshot;LLM API 看到 byte-identical prefix,Anthropic cache 自动命中 |

**fork 模式 cache 关键点**:
- `Agent._prompt.stable_system` 必须是**同一对象**——v1.2 改成 sub-Agent
  构造时**直接引用**主 Agent 的 `self._prompt`,不 rebuild
- conversation snapshot 用 `to_list()` 拷一份,**不**走
  `set_messages`(那个会清空 archiver 引用,这里无需)
- `cache_breakpoints` 也直接继承(`BuiltPrompt.cache_breakpoints` 列表)

**实现要点**:`SubAgentRuntime.spawn` 接收 `parent_agent: Agent`,fork
模式直接 `parent_agent._prompt` 引用,definition 模式才 rebuild 一份
new BuiltPrompt(角色 body 替换 identity section)。

## Risks / Trade-offs

[R1] **sub-Agent 持有 per-task 状态无界增长** → `max_concurrent=5` 软上限 +
完成 N 分钟后清理(`SubAgentsConfig.task_retention_minutes=5` 默认)。

[R2] **fork snapshot 父对话可能数十万 tokens,超 LLM 上下文** → frontmatter
字段 `fork_max_history_tokens` 默认 50K;snapshot 时按 token 估算截断
(`context.estimator.estimate_messages`),保留最近 N 条直到 ≤ 50K;
超限 spawn fail-fast 报清楚。

[R3] **主 Agent 跟 sub-Agent 共享 LLMClient**——并发 LLM 调用可能撞 rate
limit / token budget → v1.2 不实现限速;v1.3 评估在 `_run_subagent` 加
semaphore 串行化或 token-bucket;短期风险 = 偶发 429 错误,主 Agent
拿到 `STREAM_ERROR` 自动重试(v0.7 STREAM_ERROR 路径已接住)。

[R4] **结果回流 reminder 路径不配对 call_id**——LLM 看到的是 reminder 而
不是 tool_result,可能误判"工具还没返回" → spec 里加一段:
"LLM 应该把 subagent_result reminder 当作普通上下文更新;task 工具调用
已经 COMPLETED(同步)或 task_id 已知(异步),tool 状态正常。"

[R5] **`task` 工具 type 参数拼错**(`"deff"` / `"forkk"`)→ enum
validation 失败,ToolResult `is_error=True`;LLM 看到错误后自我修正。
**不**做 typo 容忍(避免 silent fallback)。

[R6] **MCP plugin 拉取失败** → `AgentRegistry.scan` 在 plugin 阶段捕获
异常 + 跳过该 server + 标 `scan_errors`;不阻断其他 plugin / builtin 加载;
TUI 启动 banner 一行 WARN。

[R7] **SkillExecutor 改同步等 sub-Agent summary**——`/review` 现在要走
sub-Agent 完整生命周期,延迟比 v1.0 直接 stub runner 高 → 实际延迟**应该
差不多**(sub-Agent 跑得快,且 shared mode 还是立即返回);风险 = sub-Agent
跑超时(300s)时 `/review` 也会卡 300s;v1.2 接受这个 trade-off(用户
async=true 显式异步可绕过,但 Skill 不暴露 async 参数)。

[R8] **`task` 工具的 description 字符串长** (~200 tokens) → system
prompt 的"可用工具"段会多个 ~200 token;**v1.0 Skills 的 load_skill
description 也有类似体积**,可接受。

## Migration Plan

无 breaking change(用户视角)。

**`IndependentRunner` 删除**只影响内部装配代码,v1.2 同步改:
1. `skills/execution.py` — 删 type alias + 参数
2. `app.py` — `SkillExecutor` 装配改成传 `subagent_manager`
3. `chat_screen.py` — 删 `_setup_skill_runner` stub

部署:
1. 合并 `v1-2-subagents` → main
2. bump 1.2.0 → 1.3.0
3. release notes:
   - "新增 `task` 工具(主 Agent 可委派 sub-Agent)"
   - "新增 2 个样板 agent(explorer / summarizer)"
   - "Skills `mode: independent` 内部改用 SubAgent 通道(行为完全等价)"

回滚:
- 单 commit revert
- `SkillExecutor.execute` 的 independent 分支 revert 到 stub runner 路径
  (旧代码还在 git history)
- 配置文件 `subagents:` 块可省略,默认全开 → 回滚时也无配置变化

## Open Questions

- [ ] **sub-Agent 调 LLM 失败重试策略**——v1.2 fail-fast,要不要做 1 次
  exponential backoff 重试?实现时看是不是真有频繁 transient 错误再定
- [ ] **TUI sub-Agent 折叠卡片的具体 widget 形态**——v1.2 先用最简的
  `Static` widget + placeholder,等真用起来再 polish(避免 YAGNI)
- [ ] **sub-Agent 调 LLM 的 token 计费归属**——sub-Agent 自己累加
  `UsageStats`,不归主 Agent;`/status` 命令要不要合并显示?v1.2 不合并,
  留 v1.3
- [ ] **fork mode 下 sub-Agent 再 fork 是否物理可能**——L1 deny `task`
  物理禁嵌套,但理论上 sub-Agent 可以**手动**注册新 tool(走 v1.0
  `register_tool` API);v1.2 不防御,留 v1.3 加 audit 路径
