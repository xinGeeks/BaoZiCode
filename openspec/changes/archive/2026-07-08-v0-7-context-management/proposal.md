## Why

BaoZiCode 的对话历史目前无 token 预算保护。Read/Bash/Grep/WebFetch 等工具各自有单条上限(30-50KB),但**累积**起来没任何防护 — 跑 30 轮中等量工具调用就可能逼近 200K 上下文窗口,LLM 流式报错或返回空,Agent 彻底瘫掉。Agent 在长任务(代码迁移、批量重构、文档抓取)下尤其脆弱,用户只能盯着看它死。

## What Changes

新增两层 token 预算压缩,保证对话累积再多也不会因为上下文溢出而瘫掉:

- **Layer 1 预防(单条消息级)** — 每次 API 请求前遍历 messages;单 `ToolResultBlock` 超 8KB 或单条 tool 消息所有 blocks 合计超 20KB 时,把过大内容存盘(`.baozicode/context/<session>/<block>.json`),inline 块替换为预览(首尾各 25 行 + 总行数 + 磁盘路径)。
- **Layer 2 兜底(累积历史级)** — 估算 messages 总 token 接近上下文窗口减去余量时,调用 LLM 生成 6 段结构化摘要(`Goal / Progress / Decisions / Files / Open Issues / Next`),把较早历史替换为单条 user-role 摘要消息(用 `<system-reminder type="context_summary">` 包裹);尾部 ~10K token / 至少 5 条原文保留。摘要 prompt 用固定分隔符 `---ANALYSIS---/---SUMMARY---`,模型先写草稿再写正式段,只入正式段。
- **熔断** — 摘要连续 3 次失败(LLM 异常 / 找不到分隔符 / 摘要后仍超预算 / 解析后 < 50 chars)→ 抛 `CompactError`,Agent 终止 with 新 StopReason `COMPACTION_FAILED`。
- **补边界消息** — 摘要后紧跟 `<system-reminder type="post_compaction">`,提示模型"需要文件细节请重新调工具,不要照着摘要脑补代码"。
- **手动触发** — 新 `/compact` slash 命令(对齐 Claude Code 命名);走插队 + 标记位,Agent 在下次 `llm.stream()` 之前检测 `_compact_requested` 标志,中断当前 run 跑压缩,完成后从同一 user 消息重启。
- **触发时机** — 自动触发在每次 `llm.stream()` 之前(Agent.run 主循环 hookpoint);自动保留 13K 余量(防 token 估算误差),手动保留 3K(用户主动要压)。
- **配置** — `AgentConfig.context_window_tokens: int = 128_000`(默认);`BackendConfig.context_window_tokens: int | None = None`(可选覆盖,None 时跟全局);`AgentConfig.compaction: CompactionConfig` 集中放阈值。
- **存盘** — `.baozicode/context/<session_id>/<block_id>.json`(项目级);`.gitignore` 新增一行;App `on_unmount` 和 `/clear` 时按 session_id 前缀清理。
- **数据模型** — `ToolResultBlock` 新增两个字段 `offloaded_to: Path | None` + `original_size: int`(frozen=False,其它调用点只多默认值,不破现有契约)。

## Capabilities

### New Capabilities
- `context-management`: 两层 token 预算压缩(单条预防 + 累积兜底)+ 6 段结构化摘要 + 熔断 + 边界消息 + `/compact` slash 命令 + `.baozicode/context/` 磁盘管理

### Modified Capabilities
- `agent-loop`: Agent.run 主循环新增 `maybe_compact(messages, trigger, ctx)` hookpoint;新增 StopReason `COMPACTION_FAILED`;`_cancel_event` 增加 `_compact_requested` 语义
- `tool-calling`: ToolResultBlock 新增 `offloaded_to` + `original_size` 字段(纯加默认,无破坏)
- `interactive-tui`: TUI 新增 `/compact` slash 命令;StatusBar 可选展示"已压缩 N 次"
- `configuration`: AppConfig 增加 `agent.compaction: CompactionConfig` + `agent.context_window_tokens` + `backend.context_window_tokens_override`

## Impact

**新增模块** `baozicode/context/`(跟 `baozicode/permissions/` 对齐):
- `schema.py` — `CompactionConfig`、`CompactionResult`、`ContextWindow`
- `estimator.py` — token 估算(role 加固定开销 + content char ratio + 中文加权)
- `storage.py` — `.baozicode/context/` 读写 + session 级清理
- `layer1.py` — `OffloadEngine`(单 block + 单消息聚合截断,生成 preview)
- `layer2.py` — `CompactEngine`(tail window 计算 + LLM 摘要 + 熔断 + 6 段解析)
- `boundary.py` — `<system_reminder>` 模板(摘要 + post-compaction)
- `__init__.py` — `maybe_compact(messages, *, trigger, ctx)` 公开 API

**改造点**:
- `baozicode/agent/loop.py` — Agent.run 新增 hookpoint + `_compact_requested` 标志
- `baozicode/agent/events.py` — 新增 StopReason.COMPACTION_FAILED
- `baozicode/llm/base.py` — ToolResultBlock 加两个字段
- `baozicode/tui/chat_screen.py` — `/compact` slash 命令注册 + dispatch
- `baozicode/config/schema.py` — AgentConfig + BackendConfig + CompactionConfig
- `.gitignore` — 新增 `.baozicode/context/`

**依赖**:
- 调 LLM 走摘要时,需要可注入的 `LLMClient` 句柄(用 Agent 已有的 `self._llm`,无新依赖)
- 估算器无外部依赖(启发式 char ratio,不引 tiktoken — 后续可优化)

**测试**:
- `tests/context/` 新增 conftest(fake tmp_path project_root + fake session_id)
- 估算器、storage、layer1(单 block + 聚合)、layer2(LLM mock)、boundary、Agent.run hookpoint 端到端
- `/compact` slash 命令 dispatch
- 熔断 / 失败 3 次触发 COMPACTION_FAILED
- 8 个新测试文件,~50 个新测试

**用户可见行为变化**:
- 长会话不再"突然暴毙",而是悄悄在 boundary 写一次 `<system-reminder>`
- TUI 状态栏可选显示"已压缩 N 次"指标(v0.7 加,默认开启)
- 用户可主动 `/compact` 强制压缩