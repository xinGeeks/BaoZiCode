## 1. 数据模型与事件契约

- [x] 1.1 创建 `baozicode/agent/` 包目录 + `__init__.py`
- [x] 1.2 在 `baozicode/agent/events.py` 定义 `AgentEvent` frozen dataclass（`type: Literal[...]`、`payload: dict`）+ `StopReason` 枚举（7 个值:COMPLETED / MAX_ITERATIONS_REACHED / USER_CANCELLED / UNKNOWN_TOOL_HALLUCINATION / DENIALS_EXCEEDED / FAILED_TOOL_LOOP / STREAM_ERROR）+ `UsageStats` dataclass（input_tokens / output_tokens / cache_read / cache_write）+ `Progress` dataclass（iteration / max / phase）
- [x] 1.3 在 `baozicode/agent/collector.py` 定义 `TurnSnapshot` dataclass（text / tool_calls / text_blocks / tool_use_blocks）+ `to_message() -> Message` 方法 + `StreamCollector` 类（`absorb(delta: ContentDelta)` 同步 emit text 块 + 累加 TurnSnapshot、`snapshot() -> TurnSnapshot` 产出完整结构）
- [x] 1.4 在 `baozicode/tools/base.py` 的 `ToolDefinition` dataclass 加 `side_effect: bool = False` 字段（保持向后兼容）

## 2. 工具 side_effect 字段硬编码

- [x] 2.1 `baozicode/tools/read.py` 的 `TOOL` 加 `side_effect=False`
- [x] 2.2 `baozicode/tools/grep.py` 的 `TOOL` 加 `side_effect=False`
- [x] 2.3 `baozicode/tools/glob.py` 的 `TOOL` 加 `side_effect=False`
- [x] 2.4 `baozicode/tools/webfetch.py` 的 `TOOL` 加 `side_effect=False`
- [x] 2.5 `baozicode/tools/write.py` 的 `TOOL` 加 `side_effect=True`
- [x] 2.6 `baozicode/tools/edit.py` 的 `TOOL` 加 `side_effect=True`
- [x] 2.7 `baozicode/tools/bash.py` 的 `TOOL` 加 `side_effect=True`

## 3. 三层 stop guards

- [x] 3.1 创建 `baozicode/agent/guards.py`：`GuardState` dataclass（deny_counts / recent_failures deque[(name, error_hash), maxlen=10] / recent_unknown list）+ `reset()` 方法
- [x] 3.2 实现 `check_unknown_tool(call, state) -> StopReason | None`：state.recent_unknown 里已有 → UNKNOWN_TOOL_HALLUCINATION；否则 append 后返回 None
- [x] 3.3 实现 `check_deny_threshold(call, state) -> StopReason | None`：state.deny_counts[call.name] >= 3 → DENIALS_EXCEEDED；否则返回 None
- [x] 3.4 实现 `check_failed_loop(call, state, error_msg) -> StopReason | None`：state.recent_failures 末尾 3 条全是 (call.name, sha256(error_msg)[:16]) → FAILED_TOOL_LOOP；否则返回 None
- [x] 3.5 三个 guard 函数都是纯函数（无副作用），状态更新由 Agent 主循环在调 guard 后做

## 4. 工具调度器（方案 B）

- [x] 4.1 创建 `baozicode/agent/scheduler.py`：`Batch` dataclass（parallel: bool / calls: list[ToolCall]）
- [x] 4.2 实现 `_split_batches(calls: list[ToolCall]) -> list[Batch]`：遍历 calls，按 side_effect 切段，连续 False 段合并为 parallel=True 一批，True 段每条独立一批（方案 C 扩展点:此函数可替换为 DAG 分析器）
- [x] 4.3 实现 `async def schedule(calls, executor) -> AsyncIterator[ToolResult]`：迭代 batches，parallel 批用 `asyncio.gather(*[executor(c) for c in batch.calls])` 并发执行，按 LLM 顺序排列结果后逐个 yield；sequential 批按 LLM 顺序 await executor(c) 逐个 yield
- [x] 4.4 单个 executor 异常处理：并行批中一个抛异常不阻断其它，捕获后 yield 合成 `ToolResult(is_error=True, content=<exception message>)`

## 5. Agent 主循环

- [x] 5.1 创建 `baozicode/agent/loop.py`：`Agent` 类构造函数（`llm_client / tools / conversation / permissions / config / plan_mode: bool = False`）
- [x] 5.2 实现 `available_tools` property：plan_mode=True 时过滤 `side_effect=False`
- [x] 5.3 实现 `cancel()` 方法：set `_cancel_event`
- [x] 5.4 实现 `run(user_message: str) -> AsyncIterator[AgentEvent]` 主循环：
  - 初始化 GuardState、session_usage accumulator、iteration counter
  - 检查 `_cancel_event.is_set()`（每轮开头）
  - emit progress{phase=streaming}
  - `StreamCollector` 吸收 LLM deltas，emit text 事件
  - 流结束:emit usage{this_turn, session_total}（累加）
  - 检查 5 种 stop conditions → 任一命中调 `_terminate(reason)`
  - 无 stop:把 TurnSnapshot 重建为 assistant message 入库
  - emit progress{phase=tool_exec}
  - 调 `schedule(turn.tool_calls, executor)` 执行工具
  - 每个 tool_result:emit tool_result + 入库 + 检查三层 guards
  - 任一 guard 命中 → emit 合成 deny/fail 后的 ToolResult（不进 conversation）+ `_terminate(reason)`
  - iteration += 1，循环
- [x] 5.5 `_terminate(reason)` 方法：跳过剩余 batch、emit done{reason}、清理状态
- [x] 5.6 工具 executor 内部:权限检查（Modal 触发）、执行（via registry）、返回 ToolResult
- [x] 5.7 Plan mode 下 `side_effect=True` 的工具调用按 unknown_tool 处理(进 guards)

## 6. 后端 usage 数据扩展

- [x] 6.1 `baozicode/llm/anthropic.py` 的 stream 末尾捕获 `message_delta.usage`，yield `ContentDelta(type="usage", payload=UsageStats(...))`；拿不到时 yield `UsageStats(0, 0, 0, 0)`
- [x] 6.2 `baozicode/llm/openai.py` 的 OpenAICompatibleBackend stream 末尾（choices 为空那条）提取 usage 字段，yield usage delta；非 Anthropic SDK 默认开启 `stream_options={"include_usage": True}`
- [x] 6.3 扩展 `ContentDelta` 类型:加 `"usage"` 到 Literal 联合（baozicode/llm/base.py）
- [x] 6.4 验证 4 个 backend 都通过单元测试 yield usage event（即使 0 也不抛异常）

## 7. 配置层扩展

- [x] 7.1 `baozicode/config/schema.py` 新增 `AgentConfig` Pydantic model：`max_iterations: int = 20`、`plan_mode_default: bool = False`（保留扩展空间）
- [x] 7.2 `AppConfig` 加 `agent: AgentConfig = AgentConfig()` 字段
- [x] 7.3 更新 `config.example.yaml` 加 `agent:` 块示例（注释说明 max_iterations 默认 20）
- [x] 7.4 验证 `loader.py` 对未知 agent key 不报错（已有 extra="ignore" 兜底）

## 8. Conversation 扩展

- [x] 8.1 `baozicode/conversation/manager.py` 新增 `add_turn(snapshot: TurnSnapshot)` 方法：从完整 TurnSnapshot 重建 assistant Message（text_blocks + tool_use_blocks）
- [x] 8.2 `add_tool_result(result: ToolResult)` 已有，确认 v0.3 仍按 LLM 调用顺序调用

## 9. TUI 集成

- [x] 9.1 重构 `baozicode/tui/chat_screen.py` 的 `_send_user_message`：移除内联 while 循环，改为 `async for event in app.agent.run(text):` 消费事件
- [x] 9.2 事件渲染映射:text → Markdown stream.write;tool_call → mount ToolCallCard;tool_result → mount ToolResultCard;progress → 更新状态条;usage → 存 session_usage;done → 解锁 input;error → mount error card
- [x] 9.3 新增 `/plan <task>` 命令:构造 `Agent(plan_mode=True)` 跑一次，结果入库后进入 plan_ready 状态
- [x] 9.4 新增 `/do` 命令:plan_ready 状态下构造 `Agent(plan_mode=False)` 跑一次（用完整 conversation 历史）
- [x] 9.5 新增 `/auto` 命令:清 plan_ready 标志，无副作用
- [x] 9.6 新增 `/stop` 命令:调 `agent.cancel()`
- [x] 9.7 新增 `/status` 命令:显示 session 累计 token 用量
- [x] 9.8 Esc/Ctrl+C 绑定:`agent.cancel()`（运行中）；Ctrl+C 在 idle 时退出 app
- [x] 9.9 plan_ready 状态：用户输入只入库不触发 Agent.run
- [x] 9.10 进度状态条:监听 progress 事件，更新底部 label（`3/20 · tool_exec`）
- [x] 9.11 状态字段:`ChatScreen.__init__` 加 `plan_ready: bool = False`、`session_usage: UsageStats = UsageStats(0,0,0,0)`

## 10. 测试

- [x] 10.1 新建 `tests/test_agent_loop.py`:用 mock LLMClient 跑 Agent,验证 5 种 stop condition 各一 case(COMPLETED / MAX_ITERATIONS / USER_CANCELLED / UNKNOWN_TOOL / DENIALS_EXCEEDED / FAILED_TOOL_LOOP)
- [x] 10.2 新建 `tests/test_stream_collector.py`:验证 text + 多个 tool_use 的 turn 通过 TurnSnapshot.to_message() 重建为完整 ContentBlock list,text 字节级一致、tool_use 字段全保留
- [x] 10.3 新建 `tests/test_scheduler.py`:验证 `_split_batches` 分组正确(all-parallel / all-sequential / mixed 边界)、并发批结果按 LLM 顺序、串行批逐个 yield
- [x] 10.4 新建 `tests/test_guards.py`:三层 guard 独立判断(纯函数 unit test)、共用 exit、共用 session_state、连续 3 次 (name, hash) 命中
- [x] 10.5 新建 `tests/test_plan_mode.py`:`Agent(plan_mode=True)` 只暴露 4 个 tool、plan_mode 下 Write 请求按 unknown 处理、`/plan` → user 输入入库不触发 run → `/do` 全工具执行
- [x] 10.6 扩展 `tests/test_backends.py`:Anthropic 和 OpenAI backend 都在 mock SDK 下 yield 一个 usage ContentDelta;拿不到 usage 时 yield UsageStats(0,0,0,0)
- [x] 10.7 新建 `tests/test_usage_event.py`:Agent 累加 usage 正确,this_turn 等于本轮, session_total 累加
- [x] 10.8 扩展 `tests/test_tools.py`:每个 tool 加 `side_effect` 字段断言(Read/Write 抽样)
- [x] 10.9 跑 v0.2 的 49 个测试确认全部 PASS(side_effect 默认 False 必须保留向后兼容)
- [x] 10.10 跑 v0.3 新增测试:目标 25+ 个测试,6 个测试文件,总计 70+ tests 全 PASS

## 11. 文档

- [x] 11.1 更新 `README.md`:v0.3 章节 — Agent Loop 架构图、5 种停止条件、`/plan /do /auto` 用法示例、`side_effect` 并发策略说明
- [x] 11.2 更新 `CLAUDE.md`:模块结构加 `baozicode/agent/`;依赖方向更新;增加 v0.3 范围段落
- [x] 11.3 更新 `config.example.yaml`:`agent:` 块上方加注释说明 max_iterations 默认 20 是安全网
- [x] 11.4 跑 `python -m baozicode --help` 确认 CLI 不变

## 12. 端到端验证

- [x] 12.1 用 mock LLM 跑端到端:用户发"读 README.md 然后用 Edit 加一行" → 验证 Agent 跑 Read → 进 conversation → 续流 → Edit → 完成(done=COMPLETED)
- [x] 12.2 用 mock LLM 跑 5 种停止条件各一 case,验证 TUI 渲染 done event 正确(显示 reason)
- [x] 12.3 用 mock LLM 跑 plan_mode:`/plan <task>` 只用 Read/Grep → 计划文本入库 → 用户追加约束 → `/do` 全工具执行
- [x] 12.4 跑 `python smoke_test.py` 全过(7 个测试文件,70+ tests)
- [x] 12.5 跑 `openspec validate --change v0-3-agent-loop --strict` 确认所有 spec 结构合法
