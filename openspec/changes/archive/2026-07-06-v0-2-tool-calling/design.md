## Context

v0.1 BaoZiCode 已经能跑 TUI 多轮对话，支持 4 个 LLM 后端（Anthropic / OpenAI / MiniMax / DeepSeek），通过 `/model` 切换。OpenSpec 已有 3 个 capability：`configuration`、`interactive-tui`、`llm-streaming`，全部 spec 已在主分支。

当前瓶颈：模型只能"说"不能"做"。所有"读 README.md 总结一下"类需求都要用户自己把内容粘进对话。v0.2 让模型能自主调用 7 个工具（Read / Write / Edit / Bash / Grep / Glob / WebFetch），把对话工具变成 coding agent。

约束（已与用户对齐）：
- **权限模型**：Write/Edit/Bash 高风险 → 弹确认框；Read/Grep/Glob/WebFetch 低风险 → 自动放行
- **协议适配**：内部统一 `ToolDefinition / ToolCall / ToolResult` 数据类，4 个后端各自消化 SDK 差异
- **执行流**：暂停式（模型吐完 tool_use → 暂停 → UI 显示 → 执行 → 喂回 tool_result → 恢复流），不做并行
- **Bash cwd**：会话跟随 + `realpath` 投影防目录逃逸；可一键切回固定根模式

v0.1 已留的扩展点：
- `ContentDelta.type` 枚举已含 `"tool_use"`
- `Message.to_dict()` / `Message` 都是 dataclass，方便加 ContentBlock 联合
- 4 个后端都走 `AsyncOpenAI` 或 Anthropic SDK，统一在 base class 改 `stream()` 签名

## Goals / Non-Goals

**Goals:**
- 模型能调用 7 个工具完成真实编码任务
- 暂停式流式执行，UI 每步可见（🔧 调用 / 📄 结果）
- 高风险工具必弹确认，低风险工具自动放行（可通过 config 覆盖）
- 4 个后端都支持 tool calling（Anthropic 原生 + OpenAI 兼容 3 个）
- 对话历史支持 `tool_use` / `tool_result` 块（多轮 agent loop）
- 不破坏 v0.1 的 16 个测试

**Non-Goals:**
- 子代理（SubAgents、Task 工具） — 留到 v0.3
- 持久化对话 / 工具调用历史 — 仍是仅内存
- Plan mode（plan + accept + execute） — 留到 v0.3
- 流式并行工具调用 — 一个 turn 内多个 tool_use 串行执行
- MCP（Model Context Protocol） server 集成 — 留到 v0.4
- 自定义工具插件系统 — 工具在 `baozicode/tools/` 硬编码

## Decisions

### D1. 内部统一数据类（不向 SDK 类型泄漏）

`ToolDefinition(name, description, parameters, risk)` + `ToolCall(id, name, arguments)` + `ToolResult(tool_call_id, content, is_error)` 三个 dataclass 作为内部统一表示。

**Why**: 4 个后端 SDK 的 tool 协议差异极大（Anthropic `tool_use` blocks vs OpenAI `function calling`）。如果让上层依赖 SDK 类型，每加一个后端或换 SDK 都要改 UI 层。统一表示 + 后端适配器模式，把 SDK 细节关在 `llm/` 模块里。

**Alternatives**:
- ❌ 用 Anthropic SDK 的类型当主类型，OpenAI 兼容层做转换 → 偏 Anthropic 假设，未来加新协议难
- ❌ 各后端自己处理，没有统一类型 → 工具注册表和 TUI 都要分支判断，重复多
- ✅ 统一内部 dataclass + 后端适配器 → 新增后端只需要写新的 adapter

### D2. Message.content 保留 str 快速路径

`Message.content: str | list[ContentBlock]`，纯文本消息继续用 str（含 tool 的消息用 list）。

**Why**: v0.1 的所有对话消息都是纯文本（user 输入 + assistant 回复）。如果全部强制 list[ContentBlock]，每条消息都要包一层 `[{type:"text", text:...}]`，浪费且语义模糊。保留 str 让"纯文本消息"路径零开销；只有含 tool_use / tool_result 的消息才升级。

**Alternatives**:
- ❌ 全部统一为 list[ContentBlock] → 简单但冗余，纯文本路径变重
- ✅ 联合类型 + 运行时 isinstance 分支 → 快速路径保持，复杂路径也能表达
- ❌ 拆成 TextMessage / ToolMessage 两个 class → 类型多一份，序列化复杂

### D3. 暂停式执行流（不是并行/不是 batched-stop）

模型在流中吐 `tool_use` delta → ChatScreen 检测到 → 当前文本流暂停 → 挂载 🔧 卡片 → 高风险弹 Modal → 执行工具 → 挂载 📄 结果 → 追加 tool_result 到 ConversationManager → 把控制权交还给模型继续流。

**Why**: 同步可见，UX 最清晰。v0.1 已经是"流式 + 流式期间输入锁定"，用户习惯了"等模型说完才能再打字"，扩展为"等工具跑完才能继续"是自然延伸。

**Alternatives**:
- ❌ 后台并行（文本不打断，工具结果作为附注插入） → 实现复杂，且模型在工具返回前就开始嘴硬文本，UX 割裂
- ❌ 一轮一批 stop-and-go → 一个 turn 多个 tool_use 一起处理，但 v0.2 只做单 tool 简单，没必要
- ✅ 暂停式 → 简单、可预测、与 v0.1 状态机一致

### D4. Bash 三状态机（启动锁根 + 会话跟随 + 防逃逸）

启动时 `cwd` 锁定到项目根（`Path.cwd()` 或 config 指定）。每条 `cd xxx` 后 cwd 跟随更新。每次执行 Bash 前用 `Path(cwd).resolve()` 检查目标路径的祖先是否包含项目根；超出则拒绝（并把 cwd 拉回项目根）。

**Why**: 大部分用户希望 Bash 在"项目内"跑（`npm test`、`pytest`），但偶尔需要 `cd src && ls` 之类的子目录操作。完全锁根太死板；完全不限制太危险。三状态机兼顾。

**`bash_locked_cwd: true` 开关**：一键切回 v0.1 风格的固定根模式（cwd 永远是项目根，`cd xxx` 不生效）。

**Alternatives**:
- ❌ 永远锁根 → 太死板，不能 cd 子目录
- ❌ 完全自由 → 太危险，`cd / && rm -rf` 误操作无法防
- ✅ 三状态 + 防逃逸检查 → 兼顾灵活和安全
- ✅ 加 `bash_locked_cwd` 开关 → 给极端保守用户提供 escape hatch

### D5. 单 Modal 默认 + 批量确认可选

每个高风险工具调用独立弹 ModalScreen 确认。连续 ≥2 个同类型工具（如连续 3 个 Bash）才会出现"批量确认"按钮（"全部允许 Y / 全部拒绝 N / 逐个看 ..."），行为通过 `permissions.batch_confirm: true` 开关打开。

**Why**: 默认 UX 简单（一次一个弹窗），不强制用户理解新概念。批量场景（3 个连续 Bash）确实存在但少见，做成可选不打扰默认流程。

**Alternatives**:
- ❌ 每次都逐个弹窗（不批量） → 简单但 3 个连续 Bash 太烦
- ❌ 总是批量确认 → 把简单情况也复杂化
- ✅ 默认单 Modal + 批量增强开关 → 默认简单 + 复杂场景有 escape hatch

### D6. 工具注册表（硬编码在 baozicode/tools/）

7 个工具在 `baozicode/tools/` 下各自一个文件 + `registry.py` 用 `get_all_tools() -> list[ToolDefinition]` 收集。v0.2 不做插件系统（用户不能注册自定义工具）。

**Why**: 7 个工具都是平台级、必装的。插件系统的复杂度（沙箱、权限路径、错误处理）远超 7 个工具本身的价值。v0.4 再考虑。

**Alternatives**:
- ❌ 完全插件化（用户加 entry_points） → v0.2 过度设计
- ❌ 把所有工具塞一个 tools.py → 单文件 800 行难维护
- ✅ 一文件一工具 + registry 收集 → 简洁、可测、可演进

### D7. 后端适配器在 llm/{anthropic,openai}.py 内消化

`AnthropicBackend.stream` 把 SDK 的 `tool_use` / `input_json_delta` 块转换为 yield `ContentDelta(tool_use, ToolCall)`；构造 tool_result 消息时用 SDK 的 `tool_result` block。`OpenAICompatibleBackend` 对应地用 SDK 的 `tool_calls` 数组 / `role:tool` 消息。

**Why**: 适配器集中在 backend 类里，调用方（ChatScreen、ConversationManager）只看内部统一类型。`llm/base.py` 只放抽象，不放适配代码。

**Alternatives**:
- ❌ 在 ChatScreen 里写 if backend == "anthropic" 分支 → 上层依赖 SDK，违反依赖方向
- ❌ 抽 `ToolAdapter` 抽象类，每后端一个 adapter → 抽象层叠层，过度
- ✅ backend 自己实现 stream()，消化 SDK 差异 → 直接、自然

### D8. 流式 tool_use 的拼接处理

Anthropic 的 `tool_use` 块是流式的：`content_block_start{type:tool_use, id, name}` + 多次 `input_json_delta{partial_json}` + `content_block_stop`。需要在 backend 内累积 partial_json 直到 block_stop，再 yield 一个完整的 `ContentDelta(tool_use, ToolCall)`。

**Why**: 这是 Anthropic SDK 流式的真实形态。如果在 chat_screen 层做拼接，要关心 backend-specific 协议，破坏 D7。OpenAI 的流式虽然也是 delta，但目前 openai-python SDK 在 chat completions stream 里把 `tool_calls` 拼好了才 emit，相对简单。

**Alternatives**:
- ❌ 在 ChatScreen 做拼接 → 上层耦合 SDK
- ✅ backend 内部累积 + yield 完整 ToolCall → ChatScreen 只见完整的

## Risks / Trade-offs

- **R1: Anthropic 流式 tool_use 拼接复杂** → 写专门的 streaming-tool-use 单元测试覆盖 0/1/多个 + 各种 partial_json 顺序
- **R2: Bash subprocess 的死锁风险**（stdout 满了子进程阻塞） → 用 `communicate()` 异步读，或用 `asyncio.subprocess.PIPE` + 手动读 + 超时（60s）
- **R3: 工具结果太大撑爆上下文**（cat 一个 100MB 文件） → Read 加硬 cap（默认 2000 行 / 50KB），超过截断并提示模型
- **R4: WebFetch 慢请求阻塞 UI** → httpx 异步 + 默认 30s 超时；不阻塞 stream worker（worker 本身是 async task）
- **R5: 用户误授权高风险工具**（狂按 Y） → 只能 UX 提示，不做额外限制（v0.2 不做 trust level）
- **R6: 新增 `Message.content: str | list[ContentBlock]` 让 v0.1 单测要更新** → 保留 str 快速路径，纯文本对话路径不变；只更新 tool 相关测试
- **R7: Bash cd 防逃逸检查有路径遍历绕过风险**（symlink） → `Path.resolve()` 本身就 follow symlink，再用 `is_relative_to()` 判断祖先关系；测试覆盖 `cd ../../../etc` 和 `ln -s /etc tmp; cd tmp; cat passwd` 两种场景
- **R8: 流式 tool_use 时 input_json 解析失败** → 在 backend 捕获 JSON 解析异常，yield `ContentDelta(tool_use_error, ToolCall(id, name, args={}, error=str(exc)))`，UI 显式标红

## Migration Plan

无 — v0.1 用户升级 v0.2 不需要迁移步骤。`config.example.yaml` 新增的 `permissions:` 块是可选的，不写就用默认（高风险确认 + 低风险自动 + cwd 跟随）。

向后兼容：
- v0.1 的纯文本对话消息保持 str，`Message.to_dict()` 对 str 直接返回 `{"role":..., "content":...}`
- v0.1 的 4 个 backend 不传 tools 时行为不变（`tools=None` → 不发 tools 参数给 SDK）
- v0.1 的 16 个测试不动，应该全过

## Open Questions

- Q1: Grep 工具默认走 ripgrep 还是 Python 实现？ripgrep 更快但要求用户装。**倾向**：subprocess 调 `rg`，失败时 fallback 到 Python `re`。
- Q2: WebFetch 返回 HTML 还是纯文本？LLM 处理纯文本更省 token。**倾向**：用 `selectolax` 或简单 `BeautifulSoup` 提取 text（如果 HTML 简单就只去 tags）。
- Q3: tool_result 内容如果包含大段代码，是否要高亮？**倾向**：v0.2 不做（保持简单），Markdown 渲染已经够看。
- Q4: Edit 工具的 `old_string` 必须唯一还是不唯一？**倾向**：必须唯一（找不到或找到多个都报错让模型重试），这是 Claude Code 的行为。
- Q5: tool 调用失败时的 retry 由谁负责？模型重发还是客户端？**倾向**：客户端只把错误喂回去（`is_error=True`），由模型决定下一步（重发或换工具）。