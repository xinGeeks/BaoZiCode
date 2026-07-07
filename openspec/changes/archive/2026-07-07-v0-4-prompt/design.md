# Design — v0.4 Modular System Prompt

## Context

BaoZiCode v0.3 引入了 Agent Loop + Plan Mode，事件契约和 5 种 stop guards 都已稳定。但 system prompt 仍是单字符串占位符，没有行为约束、模块化、或缓存意识。在 v0.3 跑 70+ 测试时观察到：

1. mock LLM 在 Edit 前不调用 Read 的概率 ~40%（应 <10%）
2. 4 个后端的 prompt cache 字段都恒为 0，因为 system 串每次都全量重发
3. Plan mode 下 LLM 偶尔会尝试 Write，被 `unknown_tool` guard 拦下后下一轮又忘，需要更多上下文约束

v0.4 把 system prompt 拆成稳定段（可缓存）+ 动态段（走 user-role 消息），并在 system prompt 和工具 description 双重强化关键规则。

完整设计见 `docs/superpowers/specs/2026-07-07-v0-4-prompt-design.md`（11 节）。本文档聚焦关键决策和风险。

## Goals / Non-Goals

**Goals:**
- 把 system prompt 拆成 11 个独立 sections，按优先级拼装，便于以后插新模块
- 稳定指令（身份 / 规则 / 工具描述）走 system 通道，环境信息和补充指令走 user-role `<system-reminder>` 通道
- 关键规则在 system prompt 和工具 description 双重强化（同一规则两份互补内容）
- `LLMClient.stream` 加 `cache_breakpoints` keyword-only 参数，4 后端 v0.4 接受但忽略
- v0.4 阶段行为验证用 mock LLM，真实 API 行为在 v0.5

**Non-Goals:**
- 4 后端的具体 `cache_control` 实现（v0.5 落地）
- Skill 系统本体（v0.4 只扫空目录，v0.5+ 实现激活机制）
- 长期记忆的写入（v0.4 只读 `memory.md`）
- 真实 API 的 prompt A/B 测试（v0.5）
- 跨会话的 prompt 复用（v0.4 单 Agent 实例内）

## Decisions

### D1: 7 固定 + env + 3 可选 = 11 个 sections

每个 section 一个文件 `sections/*.py`，导出 `render(ctx: BuildContext) -> str`。Builder 按固定顺序拼装，空内容 section 自动跳过（不留空标题）。

**为什么 11 而不是更少或更多**：7 个固定 section 对应「身份 / 约束 / 模式 / 执行 / 工具 / 风格 / 输出」七个职责，是 LLM 行为契约的最小完整集合。env_info 必发但动态所以单独。3 个可选（custom / skills / memory）覆盖三个未来扩展点，v0.4 已有 stub。

**考虑过的方案 A：单大字符串**：token 少但任何修改破坏 cache。
**考虑过的方案 B：按"角色"分块（系统 / 任务 / 风格）**：粒度太粗，未来插新模块困难。

### D2: 动态内容走 user-role `<system-reminder>` 而不是 system message

**为什么**：当前两个后端 (`anthropic.py:42`, `openai.py:37`) 都**默默 drop** `role="system"` 的 message。Anthropic 的 system 是单 string、OpenAI 也是单 string、MMS/DeepSeek 不确定。user-role 是 4 后端都最稳的通道。

**风险**：LLM 理论上可能回复 reminder 块。缓解：在 system prompt 的 identity 段明确写"用户消息可能含 `<system-reminder>` 标签，这是系统级补充指令，不要针对它回复"。

**为什么不改 backend 加 system message 支持**：v0.4 abstract-first，4 后端不实现。改后端契约 = 4 个文件 + 4 套测试。`<system-reminder>` 方案 0 行后端代码。

**reminder 放 `messages[-1]` 之前**（user message 紧前），不放在 `messages[0]`：放在头部会破坏整个 cache prefix（因为 prefix 变了），放尾部只破坏最近 user 那段（反正快过期）。

### D3: PlanModeReminder 节奏 = iteration 1 + 每 5 轮

**为什么 5**：经验值，太短浪费 token，太长 LLM 会忘。Mock 测试覆盖 iteration 1, 5, 6, 10, 11 各种情况。

**为什么不复用 env reminder 的注入点**：env 每轮发（cwd 可能变），plan_mode 节奏发（用户不会突然离开 plan mode）。两类 reminder 频率不同源，混在一起逻辑会乱。

### D4: 7 条规则双重强化 = 完整版在 system prompt + 简短版在工具 description

**为什么两份不重样**：`prompt_text` 完整版含"为什么"和"怎么做"（适合长上下文阅读），`tool_prefix` 简短版 1-2 句（适合 LLM 选工具时扫读）。互补不重复。

**为什么 `applies_to=("*",)` 规则（如 error_then_decide）不污染工具 description**：全局规则放工具描述里没意义——LLM 选 Edit 时不需要看到"先分析错误再决定"，那是写代码时的通用原则。

**为什么不直接改 `baozicode/tools/*.py` 里的 `TOOL = ToolDefinition(...)`**：规则是项目级策略，不属于工具本身的契约。`RuleRegistry.augment_tool` 在运行时增广，原 TOOL 常量不变 → 关闭规则（如 `rules.edit_requires_read=False`）就能完全回退。

### D5: CacheBreakpoint 接口级，4 后端 v0.4 忽略

**为什么先接口后实现**：Anthropic 显式 cache_control、OpenAI 自动 cache、MMS/DeepSeek 行为不明 → v0.4 抽象出统一接口（`location` + `priority`），不实际生效。v0.5 逐后端填。

**为什么用 `list[CacheBreakpoint]` 而不是单 `cache_control: bool`**：未来需要标记多处断点（系统开头 / 工具后 / 用户前），每个有 priority 决定保留顺序。一次性扩展到位。

**考虑过的方案 A：v0.4 直接在 Anthropic backend 落地 cache_control**：收益只在 Anthropic，OpenAI 用户没受益。v0.4 抽象的代价是 1 个 kwarg + 1 个类型，收益是 v0.5 可以无侵入扩展所有 4 后端。

### D6: Agent 构造签名 BREAKING 变更

`Agent(system_prompt="...")` → `Agent(config=AppConfig(...))`。

**为什么必须改**：`system_prompt` 只是一个字符串字段，但 v0.4 还需要 `custom_instructions` / `skills_dir` / `memory_path` / `plan_reminder_interval` 等多个字段。一一作为 kwarg 加到 Agent.__init__ 会膨胀 7+ 参数，不如直接把 `config: AppConfig` 传进来（这本来就是配置的真源）。

**影响范围**：v0.3 的 `tests/test_agent_loop.py` 和 `tests/test_plan_mode.py` 里有 ~6 处直接传 `system_prompt`。Task 2.4 一次性迁移。

**回滚**：如果 v0.4 整体回滚，Agent.__init__ 恢复成 `system_prompt` kwarg 即可，配置层不动。

## Risks / Trade-offs

**R1：稳定 system 字符串因 config 变化而变化 → 缓存失效**
- 缓解：测试断言 Agent 跑多轮时 `system` 字节级一致（`test_stable_system_byte_identical_across_iterations`）
- 检测：v0.5 跑真实 API 时盯 `UsageStats.cache_read_tokens` 是否 > 0

**R2：reminder 注入破坏 cache prefix**
- 缓解：reminder 放 `messages[-1]` 之前（不在 `messages[0]`），mock 测试验证（`test_env_reminder_is_spliced_before_user_message`）

**R3：tool description 增广后 token 增多**
- 缓解：上限测试（≤7 条规则时 total ≤ 4000 token）；`RulesConfig` 提供逐条开关
- 量化：粗估每条规则加 30-50 token，7 条约 200-350 token，对单次 API call 增量 < 5%

**R4：BREAKING 变更影响所有 Agent 调用方**
- 缓解：v0.3 测试一次性迁移（Task 2.4）；README 写明；提供迁移示例
- 不可逆：Agent 签名变了不能 100% 向后兼容，v0.4 必须一次性 ship

**R5：v0.4 没真实 API 验证，行为改进是 mock 推断**
- 缓解：mock LLM 跑 8 个典型场景（3 个在 v0.4 落地，5 个在 v0.5+ 迭代）；v0.5 用 Anthropic Opus 4.7 跑真实对照
- 风险：mock LLM 不能完全模拟真实 LLM 行为，定性指标可能偏差

**R6：`<system-reminder>` user-role 方案在 4 后端都"能用"但"不原生"**
- 缓解：4 后端测试都接受 user-role message（v0.3 已有 49 个测试覆盖）
- 长期：v0.5+ 可以逐步把 reminder 切到原生 system（Anthropic 的 system blocks / OpenAI 的 developer message）

## Migration Plan

**3 个 commit 独立可回滚**：

1. `feat(prompt): modular system prompt builder skeleton` — Phase 1
   - 新增 `baozicode/prompt/` 包
   - 0 改动 `agent/` / `llm/` / `config/`
   - 21 个新测试
   - 回滚：`git revert` 单 commit，不影响其他模块

2. `feat(agent): integrate PromptBuilder + reminder injection` — Phase 2
   - 修改 `Agent.__init__` 签名（BREAKING）
   - 修改 `AppConfig` / `AgentConfig` / 新增 `RulesConfig`
   - 8 个新测试 + ~6 个测试迁移
   - 回滚：恢复 `system_prompt` kwarg；PromptBuilder 仍可独立使用

3. `feat(llm): add cache_breakpoints interface + cache-aware /status` — Phase 3
   - `LLMClient.stream` 加 `cache_breakpoints` keyword-only 参数
   - 4 后端接受并忽略
   - `/status` 加 cache 字段
   - 12 个新测试
   - 回滚：移除 `cache_breakpoints` 参数；后端恢复 v0.3 行为；`/status` 恢复只显示 input/output

**部署顺序**：本地全过 → commit 1 → 跑 92 tests → commit 2 → 跑 100 tests → commit 3 → 跑 108 tests → push → README 更新。

**回滚触发条件**：
- Commit 1 后测试 < 92 → 修测试不修代码
- Commit 2 后行为回归 → 检查 Agent 迁移是否完整
- Commit 3 后后端抛错 → 检查 4 个 backend 的 stream 签名是否都更新

## Open Questions

- **Q1**：`PlanModeReminder.interval=5` 是否对长任务（>20 轮）够用？需要 v0.5 跑真实长任务验证。
- **Q2**：`<system-reminder>` 方案的 token 浪费（user-role 不进 cache）vs 缓存收益（稳定 system 命中）哪个更大？v0.5 用真实 API 算账。
- **Q3**：7 条规则在真实 LLM 上遵守率如何？v0.5 跑 8 场景得基线，决定 v0.6 是否调规则文本。
- **Q4**：是否需要 `task_complete` 和 `cancel` reminder 的具体实现？v0.4 已定义类型但未实现，v0.5 决定要不要做。
