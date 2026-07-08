# v0.7 Context Management — Design

## Context

BaoZiCode (v0.6) accumulates message history across Agent iterations with only
per-tool-result caps (Read 50 KB / 2000 lines, WebFetch 30 KB). The
**cumulative** token cost is unbounded — 30 mid-size tool calls (~15-30 KB
each) easily blows a 128 K context window, after which the LLM either returns
empty or raises mid-stream. The Agent then terminates with no clean recovery.

The fix is a two-layer compression strategy that runs **before** each LLM call:

```
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │   Layer 1    │    │   Layer 2    │    │     LLM      │
   │  预防(prevent)│───▶│  兜底(fallback)│───▶│  stream()    │
   │  8K/20K bytes │    │  13K reserve │    │              │
   └──────────────┘    └──────────────┘    └──────────────┘
         │                    │
         ▼                    ▼
   写盘 + preview       6 段结构化摘要
   (.baozicode/         (Goal/Progress/
    context/<sess>/)     Decisions/Files/
                          Open Issues/Next)
```

Constraints (from CLAUDE.md + user requirements):
- 用户原始消息尽量原文保留 — Layer 1 offloads only `ToolResultBlock` content;
  user / assistant text messages and `ToolUseBlock`s are never summarized.
- 中文本地语言 — token estimator weights CJK text 3:5 vs 1:3 for ASCII.
- 摘要 prompt 明确禁止模型调任何工具 — `LLMClient.stream(..., tools=[])`.
- 摘要连续失败 3 次要熔断 — circuit breaker → `StopReason.COMPACTION_FAILED`.
- 自动 13K 余量 / 手动 3K 余量 — `CompactionConfig.reserve_tokens_*`.
- 现有五层防御不动 — Layer 1/2 是纯文本改写,不触发工具调用,权限检查照旧。

Stakeholders: Agent loop (`baozicode/agent/loop.py`), LLM 抽象层
(`baozicode/llm/base.py`), TUI slash dispatcher
(`baozicode/tui/chat_screen.py`), 配置 schema
(`baozicode/config/schema.py`), 用户 (`.baozicode/context/` 磁盘管理 + 状态栏).

## Goals / Non-Goals

**Goals:**
- 单个 `ToolResultBlock` 字节数 > 8K → 写盘 + inline 替换为预览(首尾各 25 行)
- 单条 `Message(role="tool", content=[...])` 字节合计 > 20K → 挑大的依次写盘
- 累积历史 token > `context_window - reserve` → 调 LLM 生成 6 段摘要
- 摘要 prompt 强制 `tools=[]`,固定分隔符 `---ANALYSIS---/---SUMMARY---`
- 摘要失败 3 次 → `CompactError` + `StopReason.COMPACTION_FAILED`
- `/compact` slash 命令 + `_compact_requested` 标志(插队 + 中断重启)
- 摘要后追加 `<system-reminder type="post_compaction">` 边界消息
- 磁盘布局:`.baozicode/context/<session_id>/<block_id>.json`
- `on_unmount` + `/clear` 按 session_id 清理
- `/status` 显示压缩计数 + 累计节省 token
- `ToolResultBlock` 新增 `offloaded_to: Path | None` + `original_size: int` 字段
  (default = 旧行为,零破坏)
- `AgentConfig.context_window_tokens = 128_000`,`BackendConfig.context_window_tokens: int | None = None`
- 估算器无外部依赖(启发式 char ratio,不引 tiktoken)

**Non-Goals:**
- 不做跨 session 持久化 — 磁盘文件只服务于当前 session
- 不做增量 / 滚动摘要 — 每次 Layer 2 触发是全量重摘要
- 不做 multi-tier 缓存 — `<system-reminder type="context_summary" ttl="sticky">`
  跟 LLM 缓存通道正交,本版本不优化
- 不引 tiktoken / sentencepiece — 启发式估算,误差靠 13K 余量兜底
- 不做 streaming-thinking 摘要 — 单轮固定分隔符是主方案,thinking 留作 v0.8+ 优化
- 不动现有五层防御契约
- 不动 PromptBuilder / cache_breakpoints(摘要 LLM 调用走最简路径,无 system_reminder
  注入,无 cache 断点)

## Decisions

### D1. 两层压缩顺序与触发时机

**选择:** Layer 1 (offload) 每次 `llm.stream()` 之前都跑(Layer 1 无成本,
只是 byte count + 偶尔写盘);Layer 2 (summary) 只在 `estimate_messages() > context_window - reserve`
时才跑。Layer 1 永远在前,即使 Layer 2 也会跑,因为 Layer 2 估算 token
更准的输入是已经 offload 过的 messages。

**替代方案:** 只跑 Layer 2。**否决** — Layer 2 触发时已经累积了几十 KB 历史,
  LLM 摘要时仍要把这些全量文本作为 input 喂一次,代价高;Layer 1 把大头
  砍掉后 Layer 2 的 prompt 更小、模型更能聚焦。

**替代方案:** 工具执行时就直接 offload(不在 Agent 循环里跑)。
  **否决** — offload 是 token 预算策略,不是工具语义;且想 idempotent 重跑
  (第二/第三轮还能再压),集中到 `maybe_compact()` 入口更直观。

### D2. Layer 1 阈值:8K per block / 20K per message

**选择:** `per_block_threshold=8192` 字节,`per_message_threshold=20480` 字节。

**理由:** 启发值。50 KB Read 结果会被压成 ~5 KB 预览 + 5 KB 路径提示
  (头 25 + 尾 25 行 + 元数据,典型 100 字符/行 ≈ 5 KB)。20 KB / 消息聚合
  对应 2-3 个典型 8K 块,够大又不至于把单条 message 压到 LLM 看不见 tool
  名字 / tool_use_id 的程度。

**替代方案:** 5K / 15K。**否决** — 太敏感,正常 Read 一个 200 行的 Python
  文件(约 8 KB)就会被截。
**替代方案:** 16K / 50K。**否决** — 太宽松,30 轮 16K 块就逼近窗口。

### D3. Layer 1 preview 格式:首尾各 25 行 + 元数据

**选择:** preview = `--- preview (X bytes) ---\n<first 25 lines>\n... [N lines / M bytes omitted] ...\n<last 25 lines>\n--- offloaded to: <path> ---`

**理由:** 头 25 行通常含工具输出元数据(命令、参数、文件头),尾 25 行
  通常含状态码、错误信息、统计 — 这两个边界是 LLM 失忆后最需要看的。
  中间部分(典型 95%) 是数据行,真正用到时 LLM 会主动 Read 文件,摘要
  提示语 "需要文件细节请重新调工具" 明确指示这一点。

**替代方案:** 只留头 25 行。**否决** — 错误信息(尤其 Bash stderr)经常在
  尾部,丢失影响诊断。
**替代方案:** 头 50 + 尾 50。**否决** — preview 反而比原内容长,失去压缩意义。

### D4. Layer 2 摘要策略:6 段固定 + draft/formal 分隔

**选择:** `## Goal / ## Progress / ## Decisions / ## Files / ## Open Issues / ## Next`
  六段固定 header;prompt 强制模型先写 `---ANALYSIS---...---END_ANALYSIS---`
  草稿,再写 `---SUMMARY---...---END_SUMMARY---` 正式摘要;parser 只取
  SUMMARY 段。

**理由:** 六段是工程上"够用且不啰嗦"的最大集 — Goal + Progress 给 LLM
  上下文锚点;Decisions 防止 LLM 推翻自己之前的取舍;Files 让后续 Read
  调用有据可查;Open Issues + Next 是 LLM 下一步决策的输入。Draft
  段让模型先思考再输出,实测能让最终段更连贯,且 parser 端只取 SUMMARY
  段,失败模式确定(找不到 SUMMARY 直接报错 + 计数 +1)。

**替代方案:** 单段自由文本摘要。**否决** — 没有结构化字段,LLM 难以
  在后续轮次做精确决策。
**替代方案:** 8 段(含 Risks / Metrics）。**否决** — 多 2 段并未带来
  信息增益,但 prompt 长 + 摘要 token 用得多,触发频率提高。

### D5. Layer 2 tail window:10K token AND 5 message(取较大)

**选择:** `recent_window_tokens=10_000`,`recent_window_min_messages=5`,
  tail 取消息直到两个阈值都满足(以 token 为主,因为一条大消息可能单条就
  满足 token 阈值)。

**理由:** ~10K token 对应 30-50 轮对话(典型 200-400 token/轮),足够 LLM
  看见"最近一次 plan 调整 + 几次工具调用"而不丢失上下文。5 条消息兜底
  防止短 token 任务被过早压缩。两者取较大是 Claude Code 行为对标(用户要求
  1 万 token / 至少 5 条)。

**替代方案:** 固定 10 条消息。**否决** — 10 条极短问答 vs 10 条单次 Read
  50 KB 结果,token 差异 10 倍,固定消息数不可控。

### D6. 熔断 3 次,失败定义 4 种

**选择:** `max_consecutive_failures=3`,失败计数任何一种就 +1:
  (a) `LLMClient.stream()` 异常
  (b) 响应文本 < 50 chars
  (c) 找不到 `---SUMMARY---` 分隔符
  (d) 摘要后 estimated token 仍 > `context_window - reserve`
  3 次后抛 `CompactError`,Agent 终止 with `StopReason.COMPACTION_FAILED`。
  成功一次重置 0。

**理由:** (a)(b)(c) 是模型能力 / 格式问题;(d) 是 prompt 不够清晰,模型
  没真的压缩。3 次是"给模型两次重试机会,但不死循环"的经验值 —
  Claude Code 也是 3 次。增加 (d) 防止"摘要看着 2000 token 实际语义
  空洞导致估算失真"。

**替代方案:** 不设熔断,无限重试。**否决** — 摘要失败通常是 prompt /
  模型状态问题,死循环不解决问题且耗 token。
**替代方案:** 5 次。**否决** — 太宽容,用户已经等了几分钟,再等 5 次没意义。

### D7. /compact interrupt 策略:flag + 在 stream 入口检查

**选择:** `_compact_requested: bool` 标志;TUI 在 `/compact` 时设置;
  Agent 主循环在 `llm.stream()` 入口检测,检测到就:
  1. 取消当前 stream 协程(让 partial output 丢弃)
  2. 跑 `maybe_compact(trigger="manual", reserve=3000)`
  3. 清标志
  4. 从同一 user_message 重启当前 iteration

**理由:** 不用 lock / condition variable,因为 Agent 主循环是单协程,
  flag 写入和读出都在事件循环调度,不需要 atomic。`reserve=3000` 是
  "用户主动要压" — 余量收窄到最小,能压就尽量压。

**替代方案:** 整轮跑完再压缩。**否决** — 长任务跑几十轮,用户中途要压,
  还得等当前轮结束,体验差。

**替代方案:** 把 partial text 也喂给 LLM。**否决** — 摘要 API 跟普通
  `llm.stream()` 完全分离,partial text 是当前轮的产物,属于会被下一轮
  覆盖的"草稿",喂进摘要污染语义。

### D8. 摘要 prompt 用 `tools=[]` 而非 enum 禁工具

**选择:** 摘要 LLM 调用走 `LLMClient.stream(messages, system, tools=[],
  cache_breakpoints=None)` — 直接传空列表,后端 SDK 收空列表就不会构造
  `tools` 字段,模型无法 emit `tool_use` block。

**理由:** 现有 `LLMClient` 4 个后端都已实现 `tools=[]` 的处理(OpenAI
  走 `tools=[]` 直接 omitempty,Anthropic 走 `tools=[]` Anthropic SDK
  也接受)。不需要新增 flag 字段或新方法。

**替代方案:** 加 `LLMClient.summarize(messages, prompt)` 新方法。
  **否决** — 重复实现 stream(),增加维护面,新方法后续要跟 4 个后端同步
  处理 usage / error / cancellation。

### D9. ToolResultBlock 字段:`offloaded_to` + `original_size`,frozen=False

**选择:** 在 `baozicode/llm/base.py` 的 `ToolResultBlock` dataclass 加两字段,
  默认 `offloaded_to=None` / `original_size=0`。其他 12 个调用点
  (Agent loop / permissions / conversation manager / TUI cards) 不动,
  它们只看 `content` + `is_error` + `tool_call_id` 三个旧字段,新字段
  透明。

**理由:** 字段是 metadata,不参与 LLM API payload 序列化(后端只发
  `content` + `is_error` + `tool_use_id`),所以加字段零破坏。

**替代方案:** 把 offload 信息塞 `content` 字符串。**否决** — 破坏 LLM
  看到的实际内容,且 LLM 无法用 type narrowing 区分 preview 和真内容。
**替代方案:** 用一个独立 `OffloadRegistry` 全局 dict。**否决** — 散乱,
  block 本身失去自描述性,TUI 渲染时拿不到 offload 路径。

### D10. 磁盘布局:`.baozicode/context/<session_id>/<block_id>.json`

**选择:** 每个 session 独立目录,block 用 `<tool>_<hash8>_<counter>.json` 命名。
  写盘内容是 `{"content": <original>, "offloaded_at": <iso8601>, "tool":
  <name>, "tool_call_id": <id>}`。`.baozicode/context/` 加进 `.gitignore`。

**理由:** project-level(跟 `permissions.yaml` 对齐),不入 git。
  session 目录是 cleanup 的天然 unit。文件名带 hash 防冲突(同一 block
  hash = sha1(content)[:8])。

**替代方案:** 全局 `~/.cache/baozicode/context/<session>/`。**否决** —
  跨用户跨项目混存,cleanup 复杂;且 offload 跟项目状态相关,放项目内
  更可观察。
**替代方案:** 不写盘,纯 inline 压缩。**否决** — 长任务真的需要 LLM
  后续 Read 回来,inline 等于丢失信息。

### D11. 估算器:无外部依赖 + 中文加权

**选择:** `baozicode/context/estimator.py`,纯 Python 函数:

```python
def estimate_tokens(msg: Message) -> int:
    base = 4  # role overhead
    blocks_cost = 0
    for block in msg.content:  # list[ContentBlock]
        blocks_cost += 3  # block overhead
        if isinstance(block, TextBlock):
            if cjk_ratio(block.text) > 0.3:
                blocks_cost += len(block.text) * 3 // 5
            else:
                blocks_cost += len(block.text) // 3
        elif isinstance(block, ToolUseBlock):
            blocks_cost += len(json.dumps(block.input)) // 3
        elif isinstance(block, ToolResultBlock):
            # offload 后:用 original_size(>0)否则 content 长度
            size = block.original_size or len(block.content.encode("utf-8"))
            blocks_cost += size // 3
    return base + blocks_cost
```

**理由:** tiktoken 引入 5MB 依赖且首次加载慢;Claude Code 也没用
  tiktoken 而是启发式。误差 ±20% 是可接受的,留 13K 余量兜底。

**替代方案:** 引 tiktoken + cl100k_base encoding。**否决** — 依赖成本
  vs 精度收益不划算,13K 余量足够。

## Risks / Trade-offs

**[R1] 启发式估算误差 → 触发过早或过晚**
→ Mitigation: 13K auto 余量(≈10% 128K),3K manual 余量;`/status` 显示
   estimated vs actual(若后端回 usage)做观察;v0.8 引入 tiktoken 可平滑升级。

**[R2] 摘要 LLM 调用本身消耗 token(摘要 prompt + 完整 head 喂一次)**
→ Mitigation: head 一般 < 80K token(13K 余量下 Layer 2 触发时),摘要
   response ≤ 2K token,总成本 ~82K → 80K,几乎不增加 round-trip;但确实
   是 latency 增长点,1 次额外 stream() 调用。

**[R3] 摘要后 LLM 失忆,引用已 offload 的内容时瞎编**
→ Mitigation: 强制追加 `<system-reminder type="post_compaction">需要文件细节
   时请用 Read/Grep/Bash 重新调用对应工具,不要根据摘要脑补代码或路径`;
   加进 `enable_system_reminders=True` 的总开关内。

**[R4] 摘要失败 3 次后,用户损失整个 session 上下文(没摘要,继续累积会
   再超预算)**
→ Mitigation: 失败时保留 offload 后的 messages(即使没摘要,Layer 1 已经
   把单条大块砍了),进入下一轮 LLM 调用;若仍超预算,继续 Layer 1 但
   Layer 2 不再尝试。Agent 终止用 `StopReason.COMPACTION_FAILED`,TUI
   显示明确错误。

**[R5] 摘要 prompt 注入到 system prompt 是否走 cache 通道**
→ Mitigation: **不**走。摘要 LLM 调用 `system` 用临时构造的硬编码串
   (不是 `BuiltPrompt.stable_system`),`cache_breakpoints=None`。
   摘要本身不参与稳态缓存。

**[R6] 并行 / 嵌套 Agent 调用时,外层 session 清掉内层 offload 文件**
→ Mitigation: session_id 唯一(Agent 构造时 `uuid4()`);内层 Agent
   (递归 / sub-agent 场景,v0.7+ 留口子)用自己的 session_id,
   `on_unmount` 只清自己的 `<session_id>/` 子目录,不会误伤。

**[R7] /compact 中断正在 stream 的 LLM,partial 文本泄漏到 TUI**
→ Mitigation: Agent 主循环在中断时不调用 `collector.absorb` 把 partial
   text 收集进 `TurnSnapshot`;stream 协程 cancel 后,partial chunks 丢弃;
   TUI 卡片层看到 `tool_use` 之前可能已经有 `text` deltas — 这些 cards
   在 `_compact_requested` 检测到时全部清掉(由 `ChatScreen._clear_partial_cards` 实现)。

**[R8] ToolResultBlock 新增字段导致序列化向后不兼容**
→ Mitigation: 字段默认 `None` / `0`,现有调用方序列化(例如 conversation
   manager 存盘)走的是 Pydantic dump,新字段被默认 omitempty;TUI 渲染只
   看旧字段。v0.7 内测灰度:CI 全量跑 v0.6 持久化样本看 dump/load round-trip。

## Migration Plan

1. **Phase 1: schema + 估算器(Layer 0 准备)**
   - `baozicode/config/schema.py` 加 `CompactionConfig` / `ContextWindowConfig`
   - `agent.context_window_tokens: int = 128_000` + `agent.compaction: CompactionConfig`
   - `BackendConfig.context_window_tokens: int | None = None`
   - `baozicode/context/__init__.py` + `estimator.py` + `schema.py` — 估算函数 + 数据类
   - 单元测试:估算器中英文 / tool_use / mixed block,`AppConfig` Pydantic 校验

2. **Phase 2: ToolResultBlock 字段 + 存储(Layer 1 准备)**
   - `baozicode/llm/base.py` 加 `offloaded_to: Path | None = None` + `original_size: int = 0`
   - `baozicode/context/storage.py` — 写盘 / 读盘 / 清理
   - `.gitignore` 新增 `.baozicode/context/`
   - 单元测试:storage 读写、session cleanup、gitignore idempotent

3. **Phase 3: Layer 1 offload engine**
   - `baozicode/context/layer1.py` — `OffloadEngine.offload(messages, ctx)`
   - preview 生成、per-block / per-message 阈值、idempotent 重跑
   - 单元测试:50K 单 block 截断、4K 不动、3x8K 聚合、idempotent

4. **Phase 4: Agent loop hookpoint + maybe_compact 调度**
   - `baozicode/context/__init__.py` 暴露 `maybe_compact(messages, trigger, ctx)`
   - `baozicode/agent/events.py` 加 `StopReason.COMPACTION_FAILED`
   - `baozicode/agent/loop.py` 主循环加 `maybe_compact` hookpoint + `_compact_requested` 标志
   - 单元测试:hookpoint 触发顺序、flag 中断重启、reservation 切换

5. **Phase 5: Layer 2 摘要引擎**
   - `baozicode/context/boundary.py` — `<system_reminder>` 模板(摘要 + post_compaction)
   - `baozicode/context/layer2.py` — `CompactEngine.compact(messages, ctx)`
   - prompt 构造(6 段 + 双分隔符)、tail window 计算、parser、circuit breaker
   - 单元测试:tail window(5 条 / 10K token 各自触发)、parser 三种 case、
     熔断 3 次 + reset

6. **Phase 6: TUI 集成**
   - `baozicode/tui/chat_screen.py` 注册 `/compact` + 调 `app.agent.set_compact_requested()`
   - `/status` 加 compression stats 块
   - `/clear` 加 context dir 清理
   - 单元测试:slash dispatch、状态栏、partial cards 清理

7. **Phase 7: 端到端 + 文档**
   - `tests/context/` conftest + 集成测试
   - `README.md` 加 v0.7 section
   - `config.example.yaml` 加 `agent.context_window_tokens` + `agent.compaction`
   - 手动跑:20 轮 / 50 轮 Agent run,看 offload 触发、summary 触发、/compact 中断

Rollback: 任何阶段失败 → git revert 该 phase 提交;`agent.context_window_tokens`
  和 `BackendConfig.context_window_tokens` 加 `Field(default=...)` 兼容,
  无新依赖,可独立 revert 不影响 v0.6 行为。

## Open Questions

1. **摘要调用是否走 cache 通道?** 当前选择 **不走**(用临时 system 串),
   因为摘要 prompt 包含 `---ANALYSIS---` 草稿段,经常因模型微调而 byte-different,
   缓存命中率低,徒增复杂度。等 v0.8 引入 tiktoken + 缓存优化时一并考虑。

2. **offload 后的 preview 是否参与 `<system_reminder>` 的 dedupe?**
   当前 v0.7 选择 **参与** — 跟 `env_info` 一样,每轮 LLM 都会看到一遍
   已有 preview(预览本身就几 KB,几轮后累计增量 < 1K token)。v0.8+ 可考虑
   把"已 offload 的 block 路径表"做成一帧压缩的 sticky reminder。

3. **若用户在同一 session 内切换 backend,context_window 是否热更新?**
   当前选择 **不热更新** — Agent 在 `__init__` 时已锁定 `_context_window`。
   跟现有"切换 backend 要重启 Agent 实例"保持一致(避免 migration 期
   跨 backend 摘要格式不统一)。

4. **多 session 并发(开发时跑两个 BaoZiCode)会否冲突?**
   不会 — `session_id = uuid4()`,两个进程 UUID 几乎不会撞,即便撞了
   各自 offload 自己的文件,`.baozicode/context/<sess1>/` 和 `<sess2>/`
   完全隔离。
