# v1.2 SubAgent Delegation — Proposal

## Why

v1.0 Skills 提供了 `mode: independent` — 主 Agent 可以"开一个子对话跑 SOP"，**但
有两个根本限制**：

1. **触发入口只能由用户发起**（`/review --since=...`）。主 Agent 在跑 LLM 循环
   的时候不能动态起一个 sub-Agent 帮它干一个子任务——没有 `task` 工具暴露给 LLM。
2. **没有 fork / cache 优化**。`history-bubbles` 把主对话最后 N 条 message 拷进
   子对话，**结构上破坏父对话**（tool_use_id 重新生成、cache prefix 断裂），子
   Agent 第一次 LLM 调用基本是冷启。

更深层的问题是 **sub-Agent 应该是一等公民**，而不是 Skills 系统的某个执行
选项。v1.0 的 Skills 仍然依赖一个外部 dispatcher（用户或 `IndependentRunner`
stub）才能起来——这让 sub-Agent 成了"附属能力"，没法成为 LLM 决策路径上的标准
工具。

v1.2 引入 **`task` 工具 + SubAgent 体系**：

- 主 Agent 任何时候都能调 `task(type="definition", role="explorer", prompt="...")`
  派一个**干净上下文 + 角色化**的子 Agent 干子任务
- 或 `task(type="fork", prompt="...")` 派一个**继承自己上下文**的子 Agent 借
  prompt cache 省钱
- sub-Agent 跑完结果**异步**回流到主 Agent（`async=true` 默认）
- 完全用 markdown + YAML frontmatter 定义角色，跟 Skills 路径平级、解析器复用
- 4 层工具过滤 + 物理 ban `task` 工具双重防线，**sub-Agent 永远不能再 spawn**
- Skills 的 `mode: independent` **内部直接走 SubAgent 通道**——6 个 builtin
  skill 零修改，SubAgent 跟 Skills 正交共存

技术约束（已锁的 12 个决策）：

- **一个统一 `task` 工具**带 `type` 参数分流 definition / fork；工具列表稳定
- **角色 frontmatter 超集** Skill 解析器（少 `mode`、多 `tools-deny` /
  `max-iterations` / `permission-mode` / `nesting-depth`）
- **加载优先级** project > user > builtin > plugin；plugin = MCP 驱动动态拉取
- **运行时状态完全隔离**（messages / permission counter / file read cache /
  token counter），基础设施共享（LLMClient / HookDispatcher / ToolRegistry /
  FileSystem）
- **run-to-completion 模式**——本轮没 tool_call = 完成；不交互
- **结果回流**：主 Agent idle → 立即 user 消息注入；主 Agent 跑中 → 排到下个
  迭代顶部 inject `<system-reminder type="subagent_result">`；绝不以
  `ToolResultBlock` 配对（call_id 生命周期太乱、cache 破坏）
- **三种进入后台**：显式 `async=true`（默认）/ 同步任务超时自动切后台 / 用户
  TUI 手动切；**Fork 强制后台**（`async=false` 在 fork 模式被忽略）
- **嵌套防爆**：L1 全局 deny `task` + `nesting-depth` 字段默认 0 双重保险
- **4 层工具过滤 AND**（L1 全局 deny / L2 role.tools / L3 role.tools-deny /
  L4 background_whitelist）——空集 = spawn 失败并报清楚原因
- **角色定义对 LLM 半暴露**（name + description 进 system prompt「可用 Agent」
  段；body 仅激活时钉入 system-reminder，跟 Skills 两阶段加载一致）
- **取消级联**：主 Agent `cancel()` → 所有在跑 sub-Agent 同步 `cancel()`；
  sub-Agent 失败/取消**不向上传染**
- **Hooks 兼容**：复用 HookDispatcher；lifecycle 事件 payload 加 `subagent`
  字段；旧 hook 不读这个字段零修改

## What Changes

新增 1 个能力模块 + 改 2 处现有契约：

- **新增 `baozicode/agents/`** — 顶层包，跟 `skills/` 平级；7 个子模块：
  - `schema.py` — `AgentFrontmatter` Pydantic 模型（继承 `SkillFrontmatter` schema
    风格，新增 4 字段去掉 1 字段）+ `AgentDef` frozen dataclass + `parse_agent`
  - `registry.py` — `AgentRegistry.scan` 4 级扫描（project / user / builtin /
    plugin）+ `lookup` / `list_visible` / `reload` / `scan_errors`
  - `loader.py` — 占位符 `{var}` / `{var:default}` 替换（**复用 Skill 解析器**）
  - `runtime.py` — `SubAgentRuntime`：构造 sub-Agent 实例、状态隔离（自有
    ConversationManager / permission counter / file read cache / token counter）
  - `manager.py` — `SubAgentManager`：dispatch / 后台任务追踪 / 状态机
    (pending → running → done/failed/cancelled) / 结果回流 / 级联取消
  - `filter.py` — `ToolFilter` 4 层 AND 过滤
  - `plugin.py` — MCP 驱动 plugin 拉取（`agents/list` + `agents/get`，走 MCP
    `resources/read` 复用协议，不动 MCP spec）
  - `builtin/` — 2 个样板 agent（`explorer` / `summarizer`）
- **新增 ToolDefinition `task`** — `baozicode/agents/runtime.py` 定义，
  `tool_type="internal"`（跟 `load_skill` 一样豁免 Skill 白名单）；
  **注册时机**：`BaoZiCodeApp._register_task_tool` 在 `on_mount` worker 里
  注册到模块级 `ToolRegistry` 单例；`type` 参数 enum 暴露 `definition | fork`
- **`baozicode/skills/execution.py` 内部改造** — `SkillExecutor` 的
  `mode: independent` 分支**不再**调 `self._independent_runner` 闭包，改为
  调 `SubAgentManager.dispatch(role=skill.name, prompt=..., async=true)`；
  删 `IndependentRunner` type alias + `SkillExecutor.__init__` 的
  `independent_runner` 参数；`chat_screen` 那边的 stub 装配代码删掉
- **`baozicode/agent/loop.py`** — `Agent.cancel()` 加级联逻辑：递归调所有
  `SubAgentManager` 注册的子 Agent `cancel()`；`_fire_lifecycle_safe` payload
  加 `subagent` 字段（dict，含 `task_id` / `role` / `type` / `depth`）
- **`baozicode/app.py`** — 加 `self.subagents: SubAgentManager` 单例；on_mount
  注入到 `Agent.__init__` 的 `subagent_manager` 参数；wire 起来
- **`baozicode/config/schema.py`** — 新增 `SubAgentsConfig`：
  `enabled / max-concurrent / default-timeout-seconds / background-whitelist /
  builtin-dir / user-dir / project-dir / plugins-enabled`；启动时检测到无
  字段 → 默认全开

## Capabilities

### New Capabilities

- **`agent-registry`**:Agent 文件 frontmatter schema（4 字段继承 Skill、4 字段
  新增）+ 4 级扫描（project > user > builtin > plugin）+ MCP plugin 拉取
- **`agent-runtime`**:SubAgentRuntime 构造（definition / fork）+ 状态隔离 +
  4 层工具过滤 + L1 全局 deny `task` 防嵌套
- **`subagent-manager`**:SubAgentManager dispatch / 后台任务追踪 / 状态机 /
  结果回流（idle=立即 user 消息 / 跑中=下轮 reminder）/ `task` 工具定义 /
  级联取消

### Modified Capabilities

- **`skill-execution`**:`mode: independent` 内部从 `IndependentRunner` 改为
  调 `SubAgentManager.dispatch`；删除 `IndependentRunner` type alias + 装配
  路径；行为对外**完全等价**（用户视角：还是 `/review --since=...` 等
  summary 回来）
- **`hooks-lifecycle`**:lifecycle 事件 payload 新增 `subagent` 字段（dict），
  旧 hook 不读这个字段零修改；sub-Agent 自己的 session.start / turn.start /
  tool.pre / tool.post / session.end 照常 fire 但 payload 带 subagent 元数据

## Impact

**代码**（`baozicode/`）：

- `agents/schema.py` (新) — `AgentFrontmatter` + `AgentDef` + `parse_agent`
  (复用 `parse_frontmatter` 算法)
- `agents/registry.py` (新) — `AgentRegistry.scan` 4 级（builtin / user /
  project / plugin）；MCP plugin 在 `McpClientManager` 启动后异步注入
- `agents/loader.py` (新) — 占位符替换（与 Skill 同算法）
- `agents/runtime.py` (新) — `SubAgentRuntime` 构造 sub-Agent；状态隔离
  （per-task `ConversationManager` / `UsageStats` / per-task denial counter）；
  fork 模式：snapshot `conversation.to_list()` 整段拷给 sub-Agent，工具列表
  按 role 收窄；definition 模式：空 conversation + role.body 作 system prompt
- `agents/manager.py` (新) — `SubAgentManager`：dispatch / 后台 task
  registry / 状态机 / 结果回流路由（idle 立即 push user 消息 / 跑中排队
  enqueue_reminder）/ cascade cancel
- `agents/filter.py` (新) — `ToolFilter` 4 层 AND
- `agents/plugin.py` (新) — MCP `resources/read` 取 agent definition；type
  marker = `agent`
- `agents/builtin/explorer/AGENT.md` (新) — definition 模式样板
- `agents/builtin/summarizer/AGENT.md` (新) — definition 模式样板
- `skills/execution.py` (改) — `SkillExecutor.execute` 的 `independent` 分支
  改调 `SubAgentManager.dispatch`；删 `IndependentRunner` type alias + 入参
- `agent/loop.py` (改) — `Agent.cancel()` 级联 sub-Agent cancel；
  `_fire_lifecycle_safe` payload 加 `subagent` 字段（hooks 兼容）
- `app.py` (改) — `self.subagents: SubAgentManager`；`on_mount` 注册 `task`
  tool；wire 到 `Agent.__init__` 的 `subagent_manager` 参数
- `tui/chat_screen.py` (改) — status bar 加 `[agents: Nr/Nd]` 标记；sub-Agent
  折叠卡片（复用 ToolCard 组件 + 单独 agent stream 容器）；完成 toast
- `config/schema.py` (改) — `SubAgentsConfig` Pydantic model；`AppConfig`
  加 `subagents: SubAgentsConfig` 字段
- `tools/registry.py` (不改，**复用** 现有 `register_tool` / `execute_tool_call`)

**测试**（`tests/test_subagents_v12.py` 全新文件 + 散落追加到
`tests/test_skills_v10.py` / `tests/test_hooks_v11.py`）：

- `agent-registry`:~8 个 — frontmatter 校验（4 字段必填/可选）、4 级扫描优先级、
  MCP plugin 注入、reload、scan_errors 累积
- `agent-runtime`:~10 个 — definition 构造（空 conversation + role body）、
  fork 构造（snapshot + 同 system + 同 tools + cache 关键字段一致）、状态隔离
  （4 个独立计数器）、4 层 tool filter（5+ 个 case）、L1 全局 deny `task`
- `subagent-manager`:~12 个 — dispatch 5 种场景（def sync / def async / fork
  async / fork sync 被忽略 / tool_filter 空集拒绝）、后台任务状态机（pending
  → running → done/failed/cancelled）、结果回流（idle vs 跑中）、级联取消
  （主 cancel → 子 cancel）、3 种进入后台方式（async=true 显式 / 超时自动
  / 手动切 stub）
- `skill-execution`（追加）:~3 个 — independent 模式 1) summary 仍回主对话
  2) 用户视角等价 3) `IndependentRunner` type alias 已删
- `hooks-lifecycle`（追加）:~2 个 — subagent 字段出现在 lifecycle payload /
  旧 hook 不读 subagent 字段零修改

**文档**：

- `openspec/specs/agent-registry/spec.md`、`openspec/specs/agent-runtime/spec.md`、
  `openspec/specs/subagent-manager/spec.md` — 3 个新 capability spec
- `openspec/specs/skill-execution/spec.md` — 追加 3 个 DELTA scenario
- `openspec/specs/hooks-lifecycle/spec.md` — 追加 1 个 requirement（subagent
  payload 字段）
- `docs/migrations/v1.1-to-v1.2.md` — 迁移指南
- `README.md` 加一段「SubAgent」介绍 + 2 个样板 agent
- `config.example.yaml` 加 `subagents:` 配置块示例

**零 breaking change**：

- Skills 用户视角零变化（`/review` 行为完全等价；`/test` 行为完全等价）
- 旧 hook 不读 `subagent` 字段零影响
- 旧 `IndependentRunner` type alias 删除**只影响 chat_screen 装配代码**——
  chat_screen 改成 wire `SubAgentManager` 到 `SkillExecutor`，外部用户不
  可见
- 工具列表 `task` 是新增；不替换任何已有工具

## Risks

[R1] **SubAgentManager 持有所有 task 状态，无界增长** → 限制 `max-concurrent`
（默认 5），超出 → spawn 失败 + 提示用户等任务完成；同时后台 task 完成时
保留 N 分钟供 TUI 显示，过期清理

[R2] **Fork 模式 snapshot 父对话可能非常大**（几万 tokens）→ LLM API 拒收；
Mitigation:frontmatter 字段 `fork-max-history-tokens` 默认 50K，snapshot 时
按 token 估算截断（保留最近 N 条）；超限在 spawn 时 fail-fast 报清楚

[R3] **sub-Agent 跟主 Agent 共享 LLMClient**——并发调用可能撞 rate limit
→ Mitigation:SubAgentManager 调度器串行化 + 在 `_run_subagent` 内对 LLMClient
调用加 token-bucket 限速（v1.2 不实现，留 v1.3 hook）

[R4] **结果回流是 inject 消息，不是配对 tool_result**——主 Agent 拿到的
user/system-reminder 不会被 `StreamCollector` 算进 turn 计数 → 但**这正是
想要的**，sub-Agent 结果**不应该**污染主 Agent 的 turn decision 路径

[R5] **`task` 工具的 `type` 参数如果 LLM 拼错**（如 `type="deff"`）→ spawn
失败 + 提示有效 enum；不静默接受

[R6] **MCP plugin 拉取失败**（server crash）→ `AgentRegistry.scan` 在 plugin
阶段捕获异常 + 跳过该 server + 标 `scan_errors`；不阻断其他 plugin / builtin
加载；TUI 启动 banner 一行 WARN

## Migration Plan

无用户可见 breaking change。**`IndependentRunner` 删除**仅影响 chat_screen
装配代码（v1.2 同步改）。

部署：
1. 合并 `v1-2-subagents` → main
2. bump version 1.2.0 → 1.3.0（如项目有版本号）
3. release notes：
   - "新增 `task` 工具：主 Agent 可委派子任务给 sub-Agent"
   - "新增 2 个样板 agent（`explorer` / `summarizer`）"
   - "Skills `mode: independent` 内部改用 SubAgent 通道（行为完全等价）"

回滚：
- 单 commit revert
- Skills `mode: independent` 旧实现 = stub（runner 未注入时直接返回失败），
  revert 后 v1.1 行为即恢复
- 配置文件不动（`subagents:` 块可省略，默认全开）
