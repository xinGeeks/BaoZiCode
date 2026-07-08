# Changelog

BaoZiCode 所有重要变更记录在此。版本号遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)。

## [v0.8] — 2026-07

### 新增能力

- **三层项目指令文件** (`BaoZiCode.md`)
  - 三层加载:`~/.config/baozicode/BaoZiCode.md` (user_global, 最低) < `<项目>/.baozicode/BaoZiCode.md` (project_local) < `<项目>/BaoZiCode.md` (project_root, 最高)
  - 启动时按优先级拼接,高优先级排在前面让模型优先遵循
  - `@include <相对路径>` 引用其他 Markdown 片段(深度 ≤ 5 / 环路拦截 / 项目目录白名单)
  - 三层全无 → 静默 + banner 一行建议

- **JSONL 会话存档**
  - 每条 message 追加写到 `sessions/<YYYYMMDD-HHMMSS-xxxx>.jsonl`(中间加 flush + fsync)
  - 崩只会丢最后一行;恢复时坏行可跳过
  - session_id 格式从 v0.7 `uuid4` 32 字符改为 v0.8 `YYYYMMDD-HHMMSS-xxxx` 20 字符
    (4 字符随机 hex 防同秒撞车),启动时自动迁移旧 uuid 目录
  - 不单独维护 meta 文件,ID / 标题 / 消息数直接扫 JSONL 算
  - **resume 异常四件套**:坏行跳过 / orphan tool_call 截断 / token 超限自动压 / 间隔 > 8h 插 time_gap reminder
  - 30 天过期 session 启动时自动清理

- **双层自动记忆**
  - `user_dir` (跟人走) / `project_dir` (跟项目走) 两层物理隔离的笔记目录
  - 笔记 4 类 + 自动路由:`user-pref` / `correction` → user,`project` / `reference` → project
  - `MEMORY.md` 索引(200 行 / 25KB 上限)灌进 system prompt 顶部
    `## 长期记忆 (用户级)` / `## 长期记忆 (项目级)` 段
  - Agent 自然停下后**异步**调 LLM 抽取笔记(快照式并发,去重交给 LLM 判断)
  - 溢出三级分层状态机:`NORMAL` → `WARN` → `AUTO_COMPRESS`(回 NORMAL)→ `HUMAN_NEEDED`
  - 跨 session 删除别人写的笔记被拒绝(防止互踩);`update` 允许跨 session 追加 / 合并

- **CLI flag**:`--resume <SESSION_ID>` / `--new` / `--no-banner`
- **Slash 命令**:`/resume` (弹选择) / `/memory` (双层状态) / `/new` (确认后开新)
- **TUI 启动 session 选择器**:无 flag + 有历史 sessions → 启动时弹 `StartupSessionScreen`
  询问续哪个 / 开新 / 保持当前

### 修改能力

- `session_id` 格式:uuid4 32 字符 → YYYYMMDD-HHMMSS-xxxx 20 字符
- 启动顺序调整:`permissions → instructions → memory → sessions`
- 启动 banner 增三行摘要(指令 / 记忆 / 会话),`--no-banner` 可抑制
- `/status` 增量:`session_id` / `sessions(磁盘): N 个` / `memory.user: X 条` / `memory.project: Y 条`
- `ConversationManager` 透明接 `SessionArchiver`,每条 `add_*` 同步落盘
- `Agent.__init__` 接受 `project_root`,自动从两层 memory 目录读 index 灌进 prompt

### 废弃字段

- `AppConfig.memory_path` —— 单文件长效记忆字段,改为 `MemoryConfig.user_dir` +
  `MemoryConfig.project_dir` 双层目录。v0.9 移除。
  - 兼容行为:文件存在 + 新双层目录都空 → fallback 当 user 级索引读,banner 打印一行 WARN
  - 启动时显式检测:用户改了 `memory_path` 默认值 + 文件存在 → stderr `WARN: memory_path is deprecated, ...`

### 升级路径

- 最小动作:不动配置也能跑,所有新机制有 `enabled: true` 默认值
- 推荐动作:创建 `<项目>/BaoZiCode.md`,把项目规范 / 注意事项写进去
- 可选动作:迁移旧 `memory.md` 到新双层目录(见 `docs/migrations/v0.7-to-v0.8.md` §四)

详见 [docs/migrations/v0.7-to-v0.8.md](docs/migrations/v0.7-to-v0.8.md)。

## [v0.7] — 2026-06

### 新增能力

- **上下文压缩**:两层 token 预算压缩
  - Layer 1(offload):单 block > 8 KB 或单 message > 20 KB → 写盘 + preview
  - Layer 2(摘要):逼近 `context_window - 13K`(自动)或 `- 3K`(手动)→ 调 LLM 生成 6 段摘要
  - `.baozicode/context/` 自动加 `.gitignore`
  - 摘要 prompt 显式禁止调工具,先写 `---ANALYSIS---` 草稿再写 `---SUMMARY---` 正文
  - 熔断:连续 3 次失败 → `StopReason.COMPACTION_FAILED`
- **`/compact` 手动触发**:Agent 空闲直接跑;运行中通过 `agent.request_compact()` 在下个迭代顶部生效
- **`/clear` + `on_unmount`**:清空 `.baozicode/context/<session>/` 目录
