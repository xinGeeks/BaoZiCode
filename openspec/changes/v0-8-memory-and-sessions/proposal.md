# v0.8 Memory & Sessions — Proposal

## Why

BaoZiCode (v0.7) 的 Agent 每次启动都是"失忆"状态——既不记得上次项目上下文,
也不知道用户偏好,只会按 `BaoZiCode.md` 那一份静态项目指令机械执行。三个
机制都没有:

1. **跨 session 的项目指令分层加载** — `BaoZiCode.md`(项目根)是单层硬编码,
   用户级 / 项目 `.baozicode/` 级指令混在一起没优先级,且不能 `@include`
   引用其他文件复用
2. **会话存档 + 恢复** — 进程退出即丢,不能 `/resume` 接上次干一半的任务;
   v0.7 的 `.baozicode/context/<sid>/` 是临时 offload 区,不是会话日志
3. **自动长期记忆** — 用户偏好、纠正反馈、项目知识全靠用户手动写笔记;
   模型跑完一个任务不知道把"用户偏好 X"记下来

v0.8 把这三件事一次补齐:**指令分层 + 会话存档 + 自动笔记**,让 Agent
"越用越懂你",中断后能平滑接着干。技术约束(spec 明确要求):

- **JSONL 追加写 + 不维护 meta** — 元信息从 JSONL 自身扫出,少一份要同步
  的状态
- **快照式异步笔记更新** — `create_task` 不阻塞主循环,完成时核 session_id
- **三级分层超限** — 警告 → 自动压缩 → 人工兜底,避免无限递归 LLM call
- **`YYYYMMDD-HHMMSS-xxxx` session ID** — v0.7 `uuid4()` 跟着迁移
- **v0.4 的 `memory_path` 单文件字段** — 保留读,标注下版废弃

## What Changes

新增 3 个能力模块 + 改 5 个现有契约:

- **新增 `baozicode/instructions/`** — 三层 `BaoZiCode.md` 加载器(`@include`
  深度限制 5 + visited 防环 + 路径拦截)、拼接、注入 system prompt 头部。
- **新增 `baozicode/sessions/`** — JSONL append-only 存档、resume 异常处理
  (坏行跳过 / orphan tool_call 截断 / token 超限先压一次 / time_gap 提醒)、
  30 天启动时清理。
- **新增 `baozicode/memory/`** — 双层记忆目录(user + project)、每条带 YAML
  frontmatter 的 Markdown 笔记、`MEMORY.md` 索引文件、三级分层超限处理、
  异步笔记更新 LLM 编排(fenced JSON 输出 + 快照式)。

修改:

- `agent/loop.py` — `Agent.run()` 加 3 个 hookpoint:`add_*` 后 JSONL append、
  `COMPLETED|MAX_ITERATIONS_REACHED` 后异步笔记更新、`_inject_reminders`
  新增 `time_gap` / `memory_refreshed` 类型。
- `conversation/manager.py` — 接受 `SessionArchiver` 回调,`add_*` 时通知
  append(旁挂,不动 in-memory API)。
- `prompt/sections/memory.py` — 从单文件 `config.memory_path` 改为读双层
  `MEMORY.md` 索引拼接(旧字段保留可读,标注下版废弃)。
- `config/schema.py` — 新增 `MemoryConfig` / `SessionConfig`,`AgentConfig`
  加 `time_gap_threshold_hours`。
- `tui/chat_screen.py` — 新增 `/resume` `/memory` `/new` 三个 slash 命令。
- `cli.py` — 启动 banner 暴露 sessions 摘要 + `--resume <id>` `--new` flag。
- `app.py` — bootstrap 顺序调整为 config → permissions → mcp →
  **instructions → memory → sessions list → resume(如有)** → TUI。
- `context/`(v0.7)— `session_id` 从 `uuid4().hex` 改为 `YYYYMMDD-HHMMSS-xxxx`,
  兼容迁移老 uuid 目录。

## Capabilities

### New Capabilities

- **`instructions-loader`**: 启动时按优先级加载三层 `BaoZiCode.md`(`user_global`
  → `.baozicode/` → `project_root`),`@include` 引用其他文件时限制嵌套深度
  5、visited set 防环、路径必须落在项目根或 `~/.baozicode/`。拼接结果注入
  system prompt 顶部。
- **`session-archive`**: 每条 `add_user` / `add_message` / `add_tool_result`
  同步 append 到 `.baozicode/sessions/<sid>.jsonl`;会话 ID `YYYYMMDD-HHMMSS-xxxx`
  (`xxxx` = `secrets.token_hex(2)`,同秒撞时 +1 后缀)。启动时扫目录,
  坏行跳过 + warning,orphan tool_call 截断、token 超限跑一次 `maybe_compact`、
  跨度过长插 `<system-reminder type="time_gap">`。30 天外 JSONL 启动时清理。
- **`auto-memory`**: 4 类笔记(`user-pref` / `correction` / `project` /
  `reference`)各为带 YAML frontmatter 的 Markdown,双层目录 `~/.baozicode/memory/`
  + `<project>/.baozicode/memory/`。`Agent.run()` 自然停(`COMPLETED` 或
  `MAX_ITERATIONS_REACHED`)后异步调 LLM,输入近 5 轮对话 + 完整 memory index,
  输出 fenced JSON 操作列表(`add` / `update` / `delete`),dedup 由 LLM 判断。
  索引上限 200 行 / 25KB,三级分层处理:① 180 行 / 22KB 黄色警告
  ② ≥200 行 / ≥25KB 后台 LLM 压缩(每会话最多 1 次,失败转 ③)
  ③ 仍超限切人工介入,弹红色告警 + `/memory compress`/`prune` 命令。

### Modified Capabilities

- **`agent-loop`**: `Agent.run()` 加 hookpoint:`add_*` 后 JSONL append、
  `COMPLETED|MAX_ITERATIONS_REACHED` 后 fire `asyncio.create_task(memory_update)`、
  `_inject_reminders` 接受 `time_gap` / `memory_refreshed` reminder 类型。
- **`conversation-manager`**: 接受 `SessionArchiver` 回调构造注入,`add_*`
  方法内调 append,语义无破坏(无 archiver 时等同 v0.7 行为)。
- **`prompt-modular`**: `sections/memory.py` 从单文件改读双层
  `MEMORY.md`,渲染内容从 `## 长期记忆` 升级为 `## 长期记忆(项目级)` +
  `## 长期记忆(用户级)` 双段。
- **`configuration`**: 新增 `MemoryConfig`(笔记分类、超限阈值、笔记 LLM
  prompt 模板)+ `SessionConfig`(存档目录、清理天数、time_gap 阈值)。
  `AgentConfig.time_gap_threshold_hours = 8`。`AppConfig.memory_path`
  标 deprecated 但保留可读(本期静默 fallback,启动 warning)。
- **`interactive-tui`**: 新增 `/resume`(列 sessions + 选一个恢复)、
  `/memory`(查 4 类笔记索引 + 当前 session 触发的笔记更新状态)、
  `/new`(忽略现有,强制新会话)。StatusBar 暴露笔记计数 + 压缩次数。
- **`context-management`(v0.7)**: `session_id` 格式从 `uuid4().hex` 改
  `YYYYMMDD-HHMMSS-xxxx`,`.baozicode/context/<sid>/` 目录名同步。启动时
  检测老 uuid 目录,自动 rename 成新格式(同时间戳碰撞时挂"_legacy"后缀)。

## Impact

**新增包**:

- `baozicode/instructions/`(对齐 v0.7 `context/`,新模块结构)
  - `loader.py` — 三层文件扫描 + `@include` 解析 + 拼接 + 缓存
  - `include.py` — `@include` 递归解析器(深度 / visited / 路径校验)
  - `schema.py` — `InstructionLayer` / `LoadedInstructions` 数据类
  - `__init__.py` — `bootstrap(project_root, config) -> LoadedInstructions`
- `baozicode/sessions/`(对齐 v0.7 `context/`,新模块结构)
  - `archive.py` — `SessionArchiver`(`append` / `close` / 同步写入 JSONL)
  - `resume.py` — `load_session(id, root)`(坏行跳过 / orphan 截断 / 调
    `maybe_compact` / time_gap 注入)
  - `cleanup.py` — 30 天过期清理(启动时跑)
  - `schema.py` — `SessionMeta` / `SessionEntry` 数据类
  - `__init__.py` — `list_sessions(root)` / `bootstrap(...)`
- `baozicode/memory/`(对齐 v0.7 `context/`,新模块结构)
  - `store.py` — 笔记 CRUD + 索引文件读写 + frontmatter 解析
  - `updater.py` — `update_notes(messages_snapshot, store)` 异步 LLM 编排
  - `overflow.py` — 三级分层超限处理(警告 / LLM 压缩 / 人工兜底)
  - `prompt.py` — 笔记提取 prompt 模板(fenced JSON 输出约束)
  - `schema.py` — `Note` / `NoteType` / `MemoryIndex` 数据类
  - `__init__.py` — `bootstrap(project_root, config) -> MemoryStore`

**修改模块**:

- `baozicode/agent/loop.py` — `run()` 内加 3 个 hookpoint,`_inject_reminders`
  加 reminder 类型分支
- `baozicode/agent/events.py` — `StopReason` 已包含 `COMPLETED` /
  `MAX_ITERATIONS_REACHED`,无需新增 enum
- `baozicode/conversation/manager.py` — `__init__` 接受 `archiver` 参数,
  `add_*` 调 append,语义零破坏
- `baozicode/prompt/sections/memory.py` — 从单文件改读双层 `MEMORY.md`
- `baozicode/config/schema.py` — 新增 `MemoryConfig` / `SessionConfig` +
  `AgentConfig.time_gap_threshold_hours`,`AppConfig.memory_path` 标 deprecated
- `baozicode/cli.py` — 启动 banner 加 sessions 摘要 + `--resume <id>` `--new`
- `baozicode/app.py` — bootstrap 顺序插入 instructions → memory → sessions
  三步,`on_mount` 之前串行完成
- `baozicode/tui/chat_screen.py` — 加 `/resume` `/memory` `/new` 三个 slash
- `baozicode/context/orchestrator.py`(v0.7)— `session_id = uuid4().hex` 改
  `YYYYMMDD-HHMMSS-xxxx`,启动时跑老 uuid 目录迁移

**新配置块**:

```yaml
agent:
  time_gap_threshold_hours: 8

memory:
  enabled: true
  user_dir: ~/.baozicode/memory          # 默认
  project_dir: .baozicode/memory         # 默认(项目根下)
  index_max_lines: 200
  index_max_bytes: 25600
  warning_lines: 180
  warning_bytes: 22528
  recent_turns_for_update: 5
  auto_compress_per_session: 1           # 每会话最多自动压缩次数

sessions:
  enabled: true
  dir: .baozicode/sessions               # 默认(项目根下)
  retention_days: 30
```

**新依赖**: 无。`secrets` / `asyncio` / 标准库 `pathlib` / `yaml` 已就位;
LLM 调用走 Agent 现有的 `LLMClient` 句柄(无需新依赖)。

**测试**:

- `tests/instructions/` — 三层扫描 / `@include` 5 种异常(环、深度、跨边界、
  缺文件、UTF-8 BOM)、拼接顺序、空目录降级
- `tests/sessions/` — JSONL append 原子性(模拟崩溃中段)、坏行跳过、
  orphan 截断、token 超限触发 maybe_compact(用 v0.7 mock)、time_gap 阈值、
  30 天过期清理
- `tests/memory/` — frontmatter 解析、CRUD、索引拼接、三级分层超限状态机、
  LLM 笔记更新(mock LLM 流)+ 快照式 race condition(并发触发不冲突)
- 集成测试:instructions + memory + sessions 三者 + Agent.run() 端到端跑
  短对话,验证 JSONL 文件结构 + 笔记文件创建 + resume 后对话正确还原

**用户可见行为变化**:

- 启动后 `BaoZiCode.md` 文件被注入 system prompt 头部,模型按规范行为
- 任务跑完后笔记数量自动增长,`/memory` 可查
- 关闭再启动能看到 `Resuming session <id> ... [Y]` 提示 + 自动续上次的对话
- `/resume` 列出所有 sessions,可选任一续接
- `/status` 暴露 memory 笔记计数 + 笔记更新触发次数 + 会话存档大小