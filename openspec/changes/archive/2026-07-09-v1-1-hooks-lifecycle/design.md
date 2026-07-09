# v1.1 Hooks Lifecycle — Design

## Context

BaoZiCode 已迭代到 v1.0(7 个内置工具 + 4 个 LLM 后端 + MCP 客户端 + 上下文压缩 + 长期记忆 +
会话存档 + 命令注册中心 + Skills 系统),Agent Loop 在 `_v5_executor` 走 5 层防御权限。v1.0
之后用户开始提一类新需求:**「Agent 生命周期里那些触发条件明确、动作固定的重复工作」**:

- "每次 Bash 调用前检查参数是否安全"
- "每次 Bash 调用后审计到 `.baozicode/audit.log`"
- "session 开始时自动 git fetch"
- "Read 之后自动跑 lint"
- "LLM 每轮拿到当前项目的代码风格摘要"

这些不是权限(那是 v0.5)、不是 Skill(那是 v1.0 的人工操作封装)、不是 Context 压缩
(那是 v0.7 的 token 预算)。它们是**「在固定时机跑固定动作」**——hooks 系统该干的事。

v1.0 之前没有 hooks 的原因:Agent 事件契约(7 种 AgentEvent)、ToolResult schema(3 字段)
还没稳定;权限管线也没定型。v0.5 + v0.7 + v0.8 + v1.0 之后,这些基础设施都到位了,
是时候引入通用 hooks 了。

## Goals / Non-Goals

**Goals:**
- 在 Agent 生命周期 4 层事件(session / turn / message / tool)+ 少量系统事件上挂
  通用 hook,触发条件用声明式 YAML 描述
- 4 种动作类型覆盖绝大多数「自动化」用例:执行 shell 命令、注入 prompt、发 HTTP 请求、
  启动子 Agent
- 工具执行前的 hook 能拦截(tool.pre),把拒绝原因当 ToolResult 喂回 LLM 让它调整
- hook 失败只记日志、绝不打断 Agent 主流程
- L1 硬黑名单永远是硬墙,任何 hook 不能绕过
- hook.post 每次 tool_call 尝试必触发,完整审计,不管谁拒
- 完全向后兼容 v1.0 — 不写 `hooks:` 块行为完全不变

**Non-Goals:**
- 不替换 v0.5 权限系统(hook 是用户扩展,权限是核心安全)
- 不实现 Skill 系统级联动(独立模块,通过 Agent pipeline 串)
- 不引入文件监控 / watchdog(显式 reload:本版本不实现)
- 不实现 v1.0-style 的子 Agent 完整执行(`action: sub-agent` 仅作占位,
  parse_expr 读 res.output 标记 deny;子 Agent 实际执行留作 v1.2)
- 不实现 system.compaction / system.error 等系统事件的 action 挂载
  (本期预留事件类型,action 走 no-op)

## Decisions

### 决策 1:Pipeline 顺序 — 选方案 C(L1 → hook.pre → L2-L5 → execute → hook.post)

**为什么选 C**:L1 是硬墙、不可绕过;hook.pre 是用户扩展、可拒;L2-L5 是配置型策略。
三者各司其职。

```
Agent.tool_call(call)
├─ L1 hard blacklist (v0.5, bypass-proof)
│  └─ deny → ToolResult(execution_status=block_l1, denied_by=l1_blacklist, content=L1_reason)
├─ hook.pre (v1.1, 串行短路,首个 deny 赢)
│  └─ deny → ToolResult(execution_status=block_hook_pre, denied_by=hook_pre, content=hook_reason)
├─ L2 sandbox → L3 rules → L4 mode → L5 user modal (v0.5)
│  └─ deny → ToolResult(execution_status=block_permission, denied_by=l2_l5_permission, content=perm_reason)
├─ execute_tool_call (实际执行)
│  └─ 成功 → ToolResult(execution_status=executed_success, content=...)
│  └─ 失败 → ToolResult(execution_status=executed_failed, is_error=True, content=err_msg)
└─ hook.post (v1.1, 必触发,完整审计)
   └─ 异步可选(async: true),失败仅 log
```

**为什么 hook.pre 不能 allow 绕过 L1**:`L1 硬黑名单` 在 `perms_check` 内部独立调用,
hook.pre 在它之前但 hook.allow 不影响 L1 路径。理由:硬墙存在的意义就是
「不可被配置覆盖」(v0.5 设计原则),v1.1 hook 是配置扩展,不能让 hook 削弱安全基线。

**替代方案对比**:
- 方案 A(hook.pre 在 L1 前):hook.allow 可能误绕过 L1,安全基线被削弱 → 否决
- 方案 B(权限在前 hook.pre 在后):hook.pre 看不到注定被 L1 拒的调用,
  「自定义 deny 重于 L1」的语义不直观 → 否决
- 方案 C(选定):L1 永远先,hook.pre 在 L2-L5 之前,L1 不可被绕过 → 选定

### 决策 2:hook.post 触发条件 — 每次 tool_call 尝试必触发(全量审计)

**为什么必触发**:用户普遍需要「拦截操作落审计日志」,如果拦截后跳过 post,
审计能力残缺;触发规则统一化降低开发者理解成本。

**实现**:`_v5_executor` 改为 `try/finally` 结构,无论 execute 是否发生、是否被 L1/L2-L5 拒,
finally 块触发 hook.post。hook.post 自己不产生 ToolResult(只写日志 / 触发 reminder)。

**替代方案**:hook.post 只在 execute 之后跑(轻量,但审计残缺)→ 否决。

### 决策 3:ToolResult 新字段模型

| 字段 | 类型 | 取值 | 何时填 |
|---|---|---|---|
| `execution_status` | `Literal[...] | None` | `block_l1` / `block_hook_pre` / `block_permission` / `executed_success` / `executed_failed` | 每次 ToolResult 构造时必填(老代码不填默认为 None,新代码显式填) |
| `denied_by` | `Literal[...] | None` | `l1_blacklist` / `hook_pre` / `l2_l5_permission` | 仅 `execution_status` ∈ `block_*` 时填,否则 None |
| `denied_hook_id` | `str | None` | hook 唯一标识 | 仅 `execution_status == block_hook_pre` 时填,记录首个拒绝的 hook id |

**派生关系**:`is_error = (execution_status is not None and execution_status != "executed_success")`

老代码读 `is_error` 仍正确;新代码优先读 `execution_status` 拿全信息。

**为什么 execution_status 是主字段、denied_by 是补充**:`execution_status` 覆盖全
(包含 executed_success / executed_failed,不只是 deny);`denied_by` 只在 deny 时有意义,
LLM 想知道「是被哪一层拒的」才读它。两个字段语义正交,不冗余。

### 决策 4:多 hook.pre 串行短路

```
for hook in hooks_matching_event("tool.pre"):
    if not condition.match(call):
        continue  # 条件不满足,跳过这条 hook
    for action in hook.actions:
        result = execute(action)
        if result.deny:
            # 首个拒绝赢,记 id,跳出整个 hooks 循环
            return ToolResult(
                execution_status=block_hook_pre,
                denied_by=hook_pre,
                denied_hook_id=hook.id,
                content=result.reason,
            )
```

**配置顺序 = 执行顺序**:`config.yaml: hooks:` 数组按 YAML 出现顺序执行。

**为什么不用并行**:并行执行时多 hook 同时改状态(写日志、改 context)会乱;
hook.pre 必须串行才能保证 ToolResult 可预测。async 仅 post 允许(pre 不允许)。

### 决策 5:多 action 串行短路

同一 hook 内的多个 actions 也按数组顺序执行,任一 action 触发 deny → 停止后续 actions。

```
actions:
  - action: shell
    command: |
      # 第一步:审计到日志
      echo "$(date) - tool pre: $TOOL_NAME" >> .baozicode/audit.log
      exit 0
  - action: http
    url: "http://risk-api/check"
    deny_on_risk: true
    deny_reason: "风控检测到高危工具参数"
  - action: shell
    command: |
      # 第二步:风控检查通过后,执行真实操作
      ...
```

第一条 audit shell 跑完后,跑 http 风险检查,若 deny 则第二条 shell 不跑。

### 决策 6:Action 拒绝语义 — 混合模式(方案 4)

| action 类型 | 拒绝判定 | 拒绝原因 | 字段约束 |
|---|---|---|---|
| `shell` | `exit_code ≠ 0` | `stdout` 第一行;空 stdout 兜底 `hook shell 拦截高危工具调用` | **不允许**配置 `deny` / `deny_reason`,解析器识别到直接抛配置校验错误 |
| `http` | `parse_expr` 赋值 `res.deny = true` | `res.deny_reason` 必填配套字段 | HTTP 4xx/5xx、连接异常**不**自动触发工具拦截(视为接口调用失败) |
| `sub-agent` | `parse_expr` 赋值 `res.deny = true` | `res.deny_reason` 必填配套字段 | 子 Agent `execution_status=executed_failed` 不自动拦截 |
| `prompt` | **无拒绝能力** | — | **不允许**配置 `deny` / `deny_reason`;如需拦截必须搭配 shell / http / sub-agent |

**为什么混合**(为什么 shell 走 exit_code 而 http/sub-agent 用显式 deny 字段):

| 方案 | 短板 |
|---|---|
| 全动作统一 `deny: bool` | shell 场景样板代码冗余(必须额外输出标记+配 deny),违背 shell 原生习惯 |
| 全动作原生语义(shell exit_code, HTTP 4xx/5xx, sub-agent failed) | HTTP / sub-agent 区分不出「接口报错」和「主动拦截」,LLM / 审计需要大量分支判断 |
| 加第 5 种 `deny` action | 不能实现「先查后拒」一体化逻辑,只能拆多条 action;原 4 种 action 彻底丧失主动拦截能力 |
| **混合(选定)** | shell 复用原生退出码,HTTP / sub-agent 用显式 deny 字段隔离「异常」和「拦截」语义 |

文档一句话说明设计取舍:
> shell 复用操作系统原生退出码语义简化配置;http / sub-agent 存在执行异常与业务拦截两种
> 失败场景,因此使用显式 deny 字段做隔离,避免歧义。

**parse_expr 实现**:v1.1 选 `simpleeval`(轻量、AST 安全、无 exec),传入 `res` 对象
(HTTP response 或 sub-agent output)让用户写表达式赋值 `res.deny` 和 `res.deny_reason`。
simpleeval 不允许 import / lambda / 函数调用,纯字面量 + 属性访问 + 比较。

### 决策 7:Condition 语法 — 4 种匹配 + all / any 二选一

**匹配器**:
- `tool: "Bash"` — 精确(字符串相等)
- `arg.path: glob "src/**/*.py"` — fnmatch glob
- `arg.command: regex "rm\\s+-rf"` — 正则
- `arg.tool_input: not_glob "**/.env"` — 反向 glob
- `arg.x: not_regex "..."` — 反向正则
- `arg.x: not_match "..."` — 反向精确(不常用,但对称)

`not_` 前缀对所有匹配器生效,语义「条件不成立时命中」。

**逻辑组合**:`all:` 或 `any:` 二选一,不混用(混用 → 配置校验错)。

```yaml
- event: tool.pre
  if:
    all:
      - tool: "Bash"
      - arg.command: not_regex "^(ls|cat|echo)\\b"
  actions:
    - action: http
      url: "http://risk-api/check"
      ...
```

省略 `if` 等价于 `if: {}` (无条件触发)。

**为什么不全用 jq-style**:jq-style 表达力强但学习成本高、测试矩阵大,v1.1 一次吃不下;
4 种匹配 + all/any 已覆盖 95% 用例,剩余走 v1.2 评估。

### 决策 8:配置布局 — 嵌 config.yaml 的 `hooks:` 块

```yaml
# config.yaml
backend: ...
permissions_v5: ...
hooks:
  - id: audit-bash-pre
    event: tool.pre
    if:
      all:
        - tool: "Bash"
    actions:
      - action: shell
        command: |
          echo "$(date -Iseconds) tool.pre Bash $ARG_COMMAND" \
            >> .baozicode/audit.log
        timeout: 5
  - id: inject-style
    event: turn.start
    actions:
      - action: prompt
        content: |
          本项目代码风格:PEP 8 + 100 字符行宽 + 类型注解必须
        slot: sticky_reminder
```

**为什么嵌 config.yaml**:
- 跟其他所有配置块对齐(instructions / memory / sessions / commands / skills)
- 启动顺序统一(permissions → hooks → instructions → memory → sessions → commands → skills)
- loader 不加新文件扫描
- 规则多到需要拆分时再独立 hooks.yaml(v1.1.1 评估)

**替代方案**:
- 独立 hooks.yaml + 层级合并:多 1 个文件扫描 + 跟 permissions.yaml 重复同样的加载逻辑 → 否决
- 嵌 permissions_v5: 块的 hooks_layer: hook 跟 permission 规则类型完全不同,混一起可读性差 → 否决

### 决策 9:Prompt action 注入 slot

```yaml
- action: prompt
  content: "..."
  slot: sticky_reminder     # 默认
  # slot: stable_system     # 启动一次,命中 LLM 缓存
  # slot: temp              # 当前轮可见,下一轮消失
```

| slot | 注入位置 | 持久性 | 典型场景 |
|---|---|---|---|
| `stable_system`(默认禁用,需显式开) | PromptBuilder.build() 的 stable_system 段 | 启动一次,byte-identical,LLM 缓存命中 | 项目级规则(代码风格 / 测试规范) |
| `sticky_reminder`(默认) | `_inject_reminders` 拼到 messages[-2] | 每轮 user-role 消息,直到 `/clear` | 任务级提醒(安全要求 / 当前任务上下文) |
| `temp`(默认禁用,需显式开) | 当轮 assistant 消息 | 当前轮可见,下一轮消失 | 临时说明(刚才那次工具调用的额外说明) |

**为什么默认 sticky_reminder**:90% 用途是「持续提醒 LLM 某件事」,sticky_reminder 命中
最广;stable_system 太重(影响缓存),temp 太轻(下轮看不到),都不是默认合理选。

**slot 验证**:stable_system 不允许在 `event: tool.pre/post` 用(会破坏缓存 byte-identical),
配置校验时拦截。

### 决策 10:Async post hook 结果处理

```yaml
- event: tool.post
  async: true                # 允许异步;pre 不允许,会配置校验错
  if:
    tool: "Bash"
  actions:
    - action: shell
      command: |
        # 后台跑审计写入,不等结果
        echo "$(date) - $ARG_COMMAND" >> .baozicode/audit.log
        exit 0
      enqueue: false         # 默认:仅写日志,不进 LLM 上下文
      # enqueue: true        # 显式:把 stdout 灌入下一轮 reminder
```

**默认行为**:async post hook 只走 `asyncio.create_task`,不阻塞下一轮;输出仅 log.warning 记录。

**enqueue: true 行为**:hook 跑完后调 `Agent.enqueue_reminder(kind="hook_prompt", body=stdout, ttl="once")`,
下一轮 LLM 请求的 messages[-2] 位置注入 `<system-reminder type="hook_prompt">`。

**为什么默认不 enqueue**:大多数异步 hook 是审计 / 持久化场景,结果不进 LLM 上下文;
强制 enqueue 会污染 prompt 缓存命中率。显式 opt-in 更安全。

### 决策 11:Hook 失败策略 — 仅记日志,绝不打断 Agent

```python
try:
    result = await hook_dispatcher.run(event, call)
except asyncio.TimeoutError:
    log.warning("hook %s timed out on %s after %ds", hook.id, event, timeout)
    # 不 raise,继续主流程
except Exception as exc:
    log.warning("hook %s failed on %s: %s: %s",
                hook.id, event, type(exc).__name__, exc)
    # 不 raise
```

**为什么 fail-open(失败不阻断)**:hook 是用户扩展,代码质量不可控;若 hook 抛异常打断
Agent 主流程,用户的核心任务会被无关 hook 阻塞。这跟权限系统(fail-closed,L1 不可被覆盖)
形成对比。

**但**:hook.pre 的「deny」是正常路径(不是异常),触发 deny 该拦就拦,不视为失败。

### 决策 12:配置校验 — 启动期集中冻结

`HookRegistry.freeze()` 在 bootstrap 时一次跑完所有校验:

- 字段类型错(Pydantic 强校验)
- `event` 不在允许列表 → SystemExit
- `action.shell` 配了 `deny` / `deny_reason` → SystemExit
- `action.http/sub-agent` 没 `parse_expr` → SystemExit
- `action.prompt` 配了 `deny` / `deny_reason` → SystemExit
- `event: tool.pre` 配了 `async: true` → SystemExit(拦截不允许异步)
- `if.all` 和 `if.any` 同时存在 → SystemExit
- `slot: stable_system` 配在 `event: tool.pre/post` → SystemExit
- `id` 重复 → SystemExit

任何校验失败 → SystemExit,启动阻塞。理由:hook 配置错应早暴露,不要等运行时报错。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| hook.pre 同步执行阻塞 tool_call | action 默认 timeout 30s;shell 走 asyncio.subprocess;超时 → log + 继续;典型 N=1-3 hook 不影响交互 |
| async post hook 的输出不写入审计导致审计丢失 | `audit.py` 在 `finally` 块独立写一条 HookInvocation 记录(独立于 hook action);async / sync 都覆盖 |
| parse_expr 用 simpleeval 仍有 RCE 风险(simpleeval 默认安全,但用户可能传 list / dict 触发 bug) | v1.1 用 simpleeval 默认 sandbox;v1.2 评估替换为 asteval;parse_expr 只读 `res` 和字面量,不能写本地变量 |
| ToolResult 新字段让老测试构造 ToolResult 时漏填 | 在 `ToolResult.__post_init__` 默认 `execution_status=None` → 视为 executed_success(`is_error` 派生);老测试构造时不填,is_error 仍按 `bool` 字段工作 |
| L1 黑名单被 hook.allow 绕过(误用) | L1 在 hook.pre 之前的代码路径独立调用 `permissions.check` 的 L1 子集;hook.allow 不修改 perms_check 任何状态;新增测试 `test_l1_unbypassable_by_hook_allow` |
| hook 配置错导致 SystemExit 阻止启动 | 启动日志清晰列出错的 hook id 和原因;用户可临时 `mv config.yaml config.yaml.bak` 重启绕过 |
| 审计日志 `.baozicode/audit.log` 无限增长 | 启动期按 size 截断(默认 100MB);截断前 rotate 到 `audit.log.YYYYMMDD-HHMMSS` |
| prompt action slot=stable_system 影响 LLM 缓存 | 配置校验禁止 `event: tool.pre/post` 用 stable_system;其他事件允许但需用户显式声明 |
| 多 hook.pre 串行执行在 fail-fast 场景耗时累积 | 默认 timeout 30s × N;hook 总数启动期上限 50 条(配置校验);实际典型 N=1-3 |
| 12 个决策中 C1/C2/D 是「我锁定」默认,用户可能反对 | proposal + design 阶段都明确写出;变更前 `git blame` 可追溯;反对走 RFC |