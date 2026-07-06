## Context

v0.2 在 `ChatScreen._send_user_message`(`baozicode/tui/chat_screen.py:259-361`)里嵌入了完整的 agent 循环:LLM stream → 检测 tool_use → 显示卡片 → 权限 Modal → 执行工具 → 显示结果 → 喂回 conversation → 续流。这段逻辑约 100 行,与 TUI 渲染、Markdown widget、Modal 触发深度耦合。

v0.3 的核心动作是**把 Agent 逻辑抽出独立模块**,叠加 5 种停止条件、并发调度、Plan Mode 三组新能力。事件流是解耦的关键:TUI 不再"驱动"Agent,而是"订阅"Agent 推出来的事件。

约束:
- v0.3 不做多用户消息并发(串行输入锁保持不变,Agent 是 per-user-message 的)
- 不引入新依赖(`asyncio.gather` / `asyncio.Event` / stdlib 够用)
- `ToolDefinition` 字段扩展必须向后兼容(`side_effect` 默认 False,v0.2 调用方零修改)

## Goals / Non-Goals

**Goals:**
- Agent 逻辑独立于 TUI,可单元测试、可替换调度策略
- TUI 与 Agent 通过 `AgentEvent` 流解耦,TUI 只渲染不决策
- 5 种停止条件全部可单元测试,共用同一个 `_terminate(reason)` 出口
- `StreamCollector` 保证 TurnSnapshot 是 Agent 决策的唯一可信源,文本片段只用于 UI
- `side_effect=False` 工具在同一 LLM 响应内并发,`side_effect=True` 按 LLM 返回顺序串行
- Plan Mode 用 `side_effect` 字段做工具过滤,不另设白名单

**Non-Goals:**
- 多用户消息并发(明确推迟到未来版本)
- DAG 拓扑调度(方案 C)—— 预留扩展点但不实现
- 跨 session 的 Agent 状态恢复(plan/cancel 不持久化)
- 自动 retry / self-correction(LLM 自己决定改不改输入,Agent 不替它做)

## Decisions

### D1. Agent 是 async generator,事件推模式

`Agent.run(user_message) -> AsyncIterator[AgentEvent]`。理由:
- TUI 订阅流式事件无需 polling,内存中事件按时间顺序处理
- 同一接口可被 CLI / 测试 / 未来的 web 前端复用
- async generator 比 Channel/Queue 更轻量(没有 backpressure 复杂度,LLM 流本身有自然背压)

`AgentEvent` 是 frozen dataclass,`type` 字段做判别:

```python
@dataclass(frozen=True)
class AgentEvent:
    type: Literal["text", "tool_call", "tool_result", "usage", "progress", "done", "error"]
    # 各 type 携带不同 payload(text/str, tool_call/ToolCall, tool_result/ToolResult,
    # usage/UsageStats, progress/Progress, done/DoneReason, error/str)
```

### D2. 5 种停止条件共用 `_terminate(reason)` 出口

`_terminate` 是 Agent 内部的一个 coroutine,在五个判断点任一命中时被调用。它做四件事:
1. 不再 yield 任何 `tool_call` / `tool_result` 事件
2. 跳过剩余的并发批工具执行
3. 把当前轮的 assistant 消息入库(`TurnSnapshot.to_message()`)
4. yield `done` 事件带 `reason`,清理内部状态

判断顺序敏感:每轮 stream 结束、工具执行前、tool_result 入库前各调用一次。`STREAM_ERROR` 优先级最高(直接 yield done),其他按发现顺序处理。

### D3. `StreamCollector` 双路累加,D 决策落地

```
LLM delta ──▶ StreamCollector.absorb(delta)
                    │
                    ├─ 同步 emit("text", chunk)  ──▶ TUI 实时渲染
                    │
                    └─ 内部累加 TurnSnapshot:
                        ├─ text: str                (纯文本拼接)
                        ├─ tool_calls: list[ToolCall]
                        ├─ text_blocks: list[TextBlock]
                        └─ tool_use_blocks: list[ToolUseBlock]
```

`TurnSnapshot.to_message() -> Message` 是入库的**唯一方法**。Assistant 消息重建时:

```python
# 错误: messages.append({"role": "assistant", "content": self.full_text})
# 正确: messages.append(turn_snapshot.to_message())  # 重建为 [TextBlock?, ToolUseBlock?, ...]
```

这保证 tool_use 的 id/name/arguments 不丢失,下一轮 tool_result 才能正确关联。

### D4. 三层 stop guards 独立判断,共用 `_terminate`

`baozicode/agent/guards.py` 暴露三个独立函数:

```python
def check_unknown_tool(call, state) -> StopReason | None
    # (a) 不存在的工具名
    # 阈值:同未知名连续 2 次出现 → UNKNOWN_TOOL_HALLUCINATION
    # 第 1 次:把 error result 喂回 LLM,允许它改名重试

def check_deny_threshold(call, state) -> StopReason | None
    # (b) 多次被用户/策略拒绝
    # 阈值:同 tool_name 累计 deny 3 次 → DENIALS_EXCEEDED
    # 计数:同时记录 "用户按 N" 和 "permissions.deny 命中"

def check_failed_loop(call, state) -> StopReason | None
    # (c) 失败工具死循环
    # 阈值:同 (tool_name, sha256(error_msg)[:16]) 连续 3 次 → FAILED_TOOL_LOOP
    # 精确 hash 防止"改个参数重试"逃避检测
```

每个 guard 是纯函数,接受 `call` 和 `state`(滑动窗口),返回 `None` 或 `StopReason`。Agent 主循环在工具调度前依次调用三个 guard,任一返回非 None 即触发 `_terminate`。

### D5. 工具调度方案 B 实现 + 方案 C 扩展点

`baozicode/agent/scheduler.py` 暴露单一函数:

```python
async def schedule(calls: list[ToolCall], executor, permission_check) -> AsyncIterator[ToolResult]:
    """按 side_effect 分批,产生 ToolResult 流。"""
    batches = _split_batches(calls)  # 方案 B 的核心:同 side_effect 连续段合并为一批
    for batch in batches:
        if batch.is_parallel:  # side_effect=False
            results = await asyncio.gather(*[executor(c) for c in batch.calls])
        else:  # side_effect=True
            for c in batch.calls:
                yield await executor(c)  # 串行,逐个 yield 让 TUI 实时渲染
```

**方案 C 扩展点**:`_split_batches(calls)` 是唯一一处"决定哪些并发"的逻辑。将来要做 DAG 拓扑,只需替换这个函数为图分析器,`schedule` 接口和上层 Agent 不动。

### D6. `side_effect` 字段硬编码到每个 tool 模块

```python
# baozicode/tools/read.py
TOOL = ToolDefinition(..., risk="low", side_effect=False)

# baozicode/tools/write.py
TOOL = ToolDefinition(..., risk="high", side_effect=True)
```

不选 "从 risk 派生":虽然当前 7 个工具 risk 和 side_effect 完全等价,但 future-proofing 允许出现 `risk="low" + side_effect=True` 的反例(比如某种写入但可恢复的操作)。硬编码让每个 tool 显式声明自己的副作用边界。

### D7. Plan Mode 用 `side_effect` 过滤,不另设白名单

```python
class Agent:
    def __init__(self, ..., plan_mode: bool = False):
        self.plan_mode = plan_mode

    @property
    def available_tools(self) -> list[ToolDefinition]:
        if self.plan_mode:
            return [t for t in self._all_tools if not t.side_effect]
        return self._all_tools
```

`/plan <task>` → `Agent(plan_mode=True).run(<task>)` 跑一轮,只产 Read/Grep/Glob/WebFetch 结果。计划文本作为 assistant 消息入库,Agent 进入 `plan_ready` 空闲态。

**`/plan` 后用户可以继续输入补充约束**(E 决策):每条输入作为 user 消息入库,**不触发新 Agent run**(plan_mode 仍为 True)。`/do` 切回 `plan_mode=False`,后续 turn 的 LLM 调用拿到完整工具列表。

### D8. 用户取消用 `asyncio.Event`,Esc/Ctrl+C 双语义

```python
class Agent:
    def __init__(self, ...):
        self._cancel_event = asyncio.Event()

    async def run(self, user_message: str):
        # 主循环每轮检查一次
        if self._cancel_event.is_set():
            yield AgentEvent.done(reason=USER_CANCELLED)
            return
```

TUI 的 `ChatScreen` 在按 Esc / Ctrl+C 时调 `app.agent.cancel()`(`_cancel_event.set()`)。安全点:`_check_stop_conditions` 调用前后,工具 `executor(c)` 调用前后。**不抢占已经在执行的 tool**(避免 Write 文件半途被切断产生损坏)。

Ctrl+C 语义分层:
- Agent 运行中 + 按 Ctrl+C → 取消 run,保留 app
- Agent 未运行 + 按 Ctrl+C → 退出 app(v0.2 行为)

### D9. Token usage 事件 `usage`

扩展 `ContentDelta` 或新增 `UsageStats`(取决于 LLM SDK 协议差异):

```python
@dataclass(frozen=True)
class UsageStats:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0  # Anthropic prompt caching
    cache_write_tokens: int = 0
```

Agent 内部维护 `self._session_usage = UsageStats(...)`,每轮 stream 结束 yield:

```python
yield AgentEvent(type="usage", payload={
    "this_turn": this_turn_usage,
    "session_total": self._session_usage,  # 累计
})
```

TUI 把这个事件存起来,`/status` 斜杠命令时显示。

### D10. `progress` 事件最小集

```python
@dataclass(frozen=True)
class Progress:
    iteration: int      # 当前轮次(从 1 开始)
    max: int            # agent.max_iterations
    phase: Literal["streaming", "tool_exec", "checking"]
```

主循环关键节点 emit:
- 进入 `async for delta in stream` 前 → `phase="streaming"`
- 进入工具调度前 → `phase="tool_exec"`
- 调用 `_check_stop_conditions` 时 → `phase="checking"`

TUI 用 progress 事件更新界面底部的状态条(如 `● 3/20 · tool_exec`)。

## Risks / Trade-offs

**[Risk] ToolCallCard / ToolResultCard 渲染顺序在并发批下交错**
并发 `asyncio.gather` 后,4 个 Read 的结果可能在不同时间完成。如果 TUI 按结果到达顺序渲染,LLM 返回顺序被打破 → 用户困惑。
**Mitigation**:并发批的 `gather` 结果**按 LLM 调用顺序排列**(用 list comprehension `[await f for f in futures]`),TUI 看到的是稳定顺序。并发只节省时间,不改变渲染语义。

**[Risk] `_terminate` 在并发批中途触发时,部分工具已执行**
如果 `gather` 中 4 个 Read 已经执行了 2 个才命中 stop condition,剩下的 2 个不会执行。
**Mitigation**:`_terminate` 调用 `asyncio.gather` 返回的 `_ incomplete` 处理是把已完成结果入库,跳过未完成。Read 类无副作用,丢弃安全;未来若 side_effect=True 进入并发批,此方案不适用,届时改用 DAG 的精细取消。

**[Risk] `asyncio.Event` 在已完成的 Agent 上 set 失效**
如果 Agent.run 已返回,后续 `cancel()` 是 no-op,不会影响下一次 run。
**Mitigation**:`Agent.__init__` 每次实例化新的 `Event`,或者在 `run` 开始时 `event.clear()`。v0.3 选择 per-instance event,简化生命周期。

**[Risk] Plan Mode 跑出来的"计划"可能被 LLM 写得很长**
LLM 在 plan_mode 下没有强约束产出长度,可能写出 5KB 计划文本,下次全工具 run 时占大量 context。
**Mitigation**:由 LLM 自己控制(system prompt 里提示);Agent 不主动截断。将来可加 `plan_max_tokens` 配置。

**[Risk] `usage` 事件依赖每个 backend SDK 都暴露 token 计数**
Anthropic 流式 `message_delta` 末尾有 `usage` 字段;OpenAI 流式 `stream_options={"include_usage": true}` 才能拿到。
**Mitigation**:`LLMClient.stream` 抽象不变,usage 是可选 payload;backend 在拿不到时 yield `UsageStats(0, 0)` 表示"未上报",Agent 不报错。

**[Risk] 三层 guard 的滑动窗口状态在 cancel 后不清理**
如果 cancel 时 `_deny_counts` / `_recent_failures` 还有残留,下一次 run 会污染计数。
**Mitigation**:`Agent.__init__` 初始化空状态,`run` 开始时 `state.reset()`,确保每次 run 独立。

## Migration Plan

**Step 1**:实现 `baozicode/agent/` 全部模块,加单元测试,不动现有代码。
**Step 2**:在 `ChatScreen._send_user_message` 切换到 `Agent.run()` 事件订阅。**保留** v0.2 的 `_send_user_message` 作为 fallback,通过配置开关切换。
**Step 3**:smoke test + 端到端验证(TUI 跑一次完整 plan → do 流程)。
**Step 4**:若 v0.3 稳定,**删除** v0.2 的 fallback,清理 dead code。

Rollback:Step 2 的配置开关让 v0.3 实施过程中随时切回 v0.2 内联循环,不需要回滚代码。

## Open Questions

无。所有 5 个开放问题(A-H)在前序对话中已逐项拍板:
- A: max_iterations 默认 20,可配置
- B: /plan /do 无缝切换,不弹确认
- C: usage 事件含本轮 + 累计
- D: StreamCollector 双路,TurnSnapshot 是唯一可信源
- E: /plan 后用户可补充约束
- F: 未知工具给 1 次 retry,第 2 次同未知名终止
- G: 失败循环用 (name, sha256(error_msg)[:16])
- H: progress 最小集 {iteration, max, phase}
