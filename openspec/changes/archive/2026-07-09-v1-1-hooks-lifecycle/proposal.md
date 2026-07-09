## Why

BaoZiCode (v0.3 起) 已经有 5 层防御权限、4 层上下文压缩、模块化 system prompt、Skill 系统,
但 Agent 生命周期里的很多「重复但具体」工作还是要用户手动盯:每次 Bash 调用前想检查一下
参数是否安全、每次 Bash 调用后想把命令审计到 `.baozicode/audit.log`、session 开始时想
自动 fetch 一下仓库状态、想让 LLM 在每一轮拿到当前项目的代码风格摘要。这些都是
「触发条件明确、动作固定」的活,应该由机器做。

v1.1 在 Agent 生命周期的关键节点上挂通用 hook 系统,让用户用声明式 YAML 描述
「**事件 + 条件 + 动作**」三元规则,系统自动在合适时机执行。hook 失败只记日志、绝不
打断 Agent;硬墙(L1 黑名单)永远不可被 hook 绕过。

## What Changes

新增 1 个能力模块 + 改 3 处现有契约 + 加 3 处集成点 + 0 处破坏性改动:

- **新增 `baozicode/hooks/`** — 顶层包,持 7 个子模块:
  - `schema.py` — HookDef / EventName / ActionSpec / ConditionSpec Pydantic 模型 + 4 种 action 的子类型
  - `registry.py` — HookRegistry(YAML 加载 + 集中校验 + 启动期冻结)
  - `condition.py` — 4 种匹配(精确 / glob / regex / not_) + all / any 二选一求值
  - `dispatcher.py` — 事件分发 + 多 hook 串行短路 + 执行控制(run-once / async / timeout)
  - `executor.py` — 4 种 action 各自执行器(shell 走 exit_code, http/sub-agent 走 parse_expr, prompt 走 inject)
  - `audit.py` — hook 触发记录到 `.baozicode/audit.log`(JSONL,每行一条 HookInvocation 记录)
  - `bootstrap.py` — `load_hooks(config) → HookRegistry`,启动顺序接在 permissions 之后

- **改 `tools/base.py` ToolResult schema** — 加 3 个可选字段(向后兼容):
  - `execution_status: Literal["block_l1", "block_hook_pre", "block_permission", "executed_success", "executed_failed"] | None`
  - `denied_by: Literal["l1_blacklist", "hook_pre", "l2_l5_permission"] | None`
  - `denied_hook_id: str | None`
  - `is_error = (execution_status is not None and execution_status != "executed_success")` — **派生关系**,老代码读 `is_error` 仍正确

- **改 `agent/loop.py` Agent pipeline** — 在 4 个位置插入 hook 调用点:
  - `run()` 入口 → 触发 `session.start`
  - 每个 iteration 顶部 → 触发 `turn.start`;收尾 → `turn.end`
  - `_conversation.add_user` 前 → `message.received`;`add_turn` 后 → `message.sent`
  - `_v5_executor` 内 → 改 pipeline 为 `L1 → hook.pre → L2-L5 → execute → hook.post`
  - hook.pre 拒 → ToolResult(execution_status=block_hook_pre, denied_by=hook_pre, content=reason),不进 L2-L5
  - hook.post 每次 tool_call 尝试后必触发,不管谁拒

- **改 `config/schema.py` AppConfig** — 新增 `hooks: list[HookDefYaml] | None = None`,启动时若 None 跳过整层;
  config.yaml 顶层 `hooks:` 块,loader 解析时按 `event` 排序 + 集中校验(任何字段错 → SystemExit)

- **集成 3 处 reminder 通道**:
  - `prompt action` 默认注入 `<system-reminder type="hook_prompt" ttl="sticky">`,可 `slot: stable_system | reminder | temp` 覆盖
  - `async post hook` 默认只写日志,显式 `enqueue: true` 时调 `Agent.enqueue_reminder(...)`
  - system error / compaction 事件预留,本期仅实现不挂 action

- **配置层**:YAML 嵌 `config.yaml` 的 `hooks:` 块(跟 instructions / memory / sessions / commands / skills 同一布局);
  启动顺序:`permissions → hooks → instructions → memory → sessions → commands → skills`

## Capabilities

### New Capabilities

- `hooks-lifecycle` — HookDef(event + if + actions)三要素 + 4 层 + 4 action + 拦截语义 + 审计;
  整个 hook 系统的契约、能力、配置加载、失败策略都在这一个 capability 下

### Modified Capabilities

- `tool-calling` — ToolResult 加 3 个可选字段(execution_status / denied_by / denied_hook_id),
  is_error 派生关系锁定
- `agent-loop` — Agent.run pipeline 加 session.start / turn.start / turn.end / message.received /
  message.sent 事件触发点;_v5_executor 改 pipeline 顺序(L1 → hook.pre → L2-L5 → execute → hook.post)
- `permissions` — pipeline 显式拆出 L1(perms_check 之前 hook.pre 可拒),hook.pre 拒不进 L2-L5;
  L1 硬黑名单不可被 hook.allow 绕过
- `configuration` — AppConfig.hooks 字段 + config.yaml 顶层 `hooks:` 块解析;loader 在
  permissions 之后、instructions 之前 bootstrap hooks
- `prompt-modular` — `_inject_reminders` 接受 `<system-reminder type="hook_prompt">` 类型
  (由 prompt action 默认注入);stable_system slot 也通过 set_dynamic_section 通道支持

## Impact

- 新增代码:`baozicode/hooks/`(~800 行)+ 跨模块集成点 4 处(Agent / config / tools.base / prompt.builder)
- 影响测试:新增 `tests/hooks/` (~30 个测试,覆盖 schema / condition 4 种匹配 / 4 种 action /
  多 hook 短路 / 失败降级 / pipeline 顺序);改 `tests/tools/` + `tests/agent/` + `tests/permissions/` 增量
- 不影响:LLM 后端(无 SDK 变更)、MCP / Skills / Sessions / Memory(完全独立模块,集成通过 Agent pipeline 串)
- 兼容性:**完全向后兼容** — v1.0 config.yaml 不写 `hooks:` 块行为不变;ToolResult 新字段全是
  Optional,老代码读 `is_error` 仍正确
- 性能影响:pre hook 必须同步 → 每个 tool_call 增加 N 次同步 action 执行时间(典型场景 N=1-3,
  shell action 走 asyncio.create_subprocess_exec 默认 30s timeout);post hook 默认同步,
  显式 `async: true` 才走后台;审计日志写入用 aiofiles append,fsync 间隔 1s 不阻塞主线
- 安全影响:L1 硬黑名单(rm -rf / / sudo / chmod 777 / dd / mkfs / curl|sh / fork bomb /
  /etc/passwd / bash -c)永远先于 hook.pre,hook.allow 不可绕过;hook.pre 拒后不进 L2-L5,
  L2-L5 拒后不进 execute;hook 失败(超时 / 抛异常 / 配置错)只 log.warning,不 raise 到 Agent