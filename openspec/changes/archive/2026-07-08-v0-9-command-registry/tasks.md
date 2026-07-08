# v0.9 Command Registry — Tasks

## Phase 1 — Registry Skeleton

实现 `baozicode/commands/registry.py` 的 `CommandDef` / `CommandType` /
`CommandResult` 联合类型 + `CommandRegistry` 类的 `register()` / `freeze()` /
`lookup()` / `all_visible()`。

**验收**:
- `CommandRegistry.register(CommandDef(name="x", ...))` 接受后从 lookup 可见
- `register(CommandDef(name="X", ...))` 抛 `ValueError`(名字必须 lowercase)
- 两个 def 的 aliases 撞名 + `freeze()` → `SystemExit("alias collision: ...")`
- `lookup("REV")` (uppercase) → 找到 `name="rev"` 的 def
- `lookup("__nope__")` → `None`
- 13 个单测通过(`tests/commands/test_registry.py`)

## Phase 2 — CommandResult + handler 签名

定义 `LocalResult / UiStateResult / PromptResult` 三个 frozen dataclass,导出
`CommandResult` 联合类型。

**验收**:
- `Union[LocalResult, UiStateResult, PromptResult]` 类型 hint 工作
- `match result:` 模式匹配可访问 `.text` 字段(只在 PromptResult 上有)
- 测试:assert isinstance 检查三种 type tag

## Phase 3 — CommandContext 接口

写 `baozicode/commands/context.py` 定义 `CommandContext` Protocol + 真正
实现 `TextualCommandContext`。后者持有 `app: BaoZiCodeApp` 引用但只 import
`textual.screen` + `baozicode/llm/base` + `baozicode/permissions/types` + 
`baozicode/config/schema` — **不 import 业务模块**。

**验收**:
- 跑 `python -c "import baozicode.commands.context"` 不报循环 import
- 静态扫描 `context.py` 的 import set 符合 spec
- 11 个单测覆盖每个方法桩调 + send_to_agent 排队路径

## Phase 4 — builtin.py 10 命令注册

写 `register_all(registry)` 把 10 个命令的 `CommandDef(...)` 列出来。Handler
先用 stub 函数(返回 `LocalResult()` 或 `PromptResult(text="stub")` 等),后续
phase 替成实际实现。

**验收**:
- `register_all(reg); reg.freeze()` 不抛
- `reg.lookup("permissions")` → 主名 `permission` 的 def(alias 工作)
- 列出 10 个 name + aliases + type 矩阵(写在测试里)

## Phase 5 — dispatcher + completor

写 `dispatcher.py` 的 `parse_command(input)` + `dispatch(input, ctx)`;写
`completor.py` 的 `TabCompleter.candidates(prefix, registry)`。

**验收**:
- 12 个 dispatcher 单测:空 / 非前缀 / / 前缀 / 单名 / 带 args / 未知命令 / 大小写
- 10 个 completor 单测:空前缀全列 / 单匹配 / 多匹配 / hidden 排除 / 大小写
  不敏感

## Phase 6 — chat_screen 接入

修改 `baozicode/tui/chat_screen.py`:

- 加 `_registry` 字段,`on_mount` 时构造 + `register_all()` + `freeze()`
- 把 `on_input_changed` / `on_input_submitted` 事件订阅接到 dispatcher/completor
- 删 `SLASH_COMMANDS` tuple + `_handle_slash` if/elif + 6 个旧 `_cmd_xxx` 方法
- 加 `_ctx` 字段,初始化为 `TextualCommandContext(app=self)`
- `_update_status_bar` 加 mode marker `[DEFAULT]` / `[PLAN]` / 等

**验收**:
- pytest `tests/test_chat_screen_dispatch.py` 现有 4 个 slash 测试在新 dispatcher 下仍然绿
- 新增 1 个测试验证 status bar 显示 `[PLAN]` 当 plan_mode=True
- 删 6 旧命令相关测试如果有就移除 + changelog 标注

## Phase 7 — 10 个内置命令实现

逐一替 stub 为实 handler:

| 命令 | 实现要点 |
|------|----------|
| /help | 遍历 `registry.all_visible()` 渲染 description + usage;ctx.show_info 输出 |
| /compact | 调 `app.run_compact_now()`;返回 UiStateResult |
| /clear | 清 input + 历史 commit;返回 UiStateResult |
| /plan | `app.plan_mode = True`;ctx.refresh_status() |
| /do | `app.plan_mode = False`;ctx.refresh_status() |
| /session | 委派给 `StartupSessionScreen` (push_modal) |
| /memory | 调 `app.memory_status()` 渲染 |
| /permission | `switch_mode` + mode 校验(默认/strict/permissive/plan) |
| /status | 渲染 token / session / memory / 后端 / agents 状态 |
| /review | `PromptResult` 注入 review prompt |

**验收**:
- 每个命令 2-3 个单测(handler 单独验证 + 集成验证通过 dispatcher)
- `/permission foo` 抛 visible error
- `/review` 默认文本 + `{since}` 占位替换正确

## Phase 8 — config schema 扩展

在 `baozicode/config/schema.py` 加:

- `CommandsConfig.review_prompt: str | None = None`
- `AppConfig.commands: CommandsConfig = CommandsConfig()`

changelog / config.example.yaml 同步。

**验收**:
- `AppConfig(...)` 默认 commands 块就绪
- 2 个 schema 测试覆盖字段

## Phase 9 — 集成测试 + 删除旧命令迁移

新增 `tests/integration/test_v09_command_dispatch.py`:

- 全 10 命令经 dispatcher 端到端跑(stub ctx)
- busy 状态下 send_to_agent 排队
- Tab 补全的多匹配菜单路径

**验收**:
- 8-10 个 e2e 测试全绿
- 全量回归:之前所有 710 个测试仍然绿

## Phase 10 — Docs

- CHANGELOG 加 v0.9 entry(新增 / 修改 / 废弃 / 升级路径)
- docs/migrations/v0.8-to-v0.9.md:6 旧命令迁移表 + /permission 改名
- README "当前版本" 升级 v0.9.0 + slash 命令表刷新
- config.example.yaml:加 `commands:` 块 + `review_prompt` 注释

**验收**:
- migration doc 完整覆盖 6 个旧命令的替代路径
- changelog 段跟前面 v0.8 / v0.7 风格一致

## Phase 11 — Archive

- `git status` 干净
- `git add -A && git commit -m "feat(v0.9): command registry + 10 built-in slash commands"`
- `mv openspec/changes/v0-9-command-registry openspec/changes/archive/`
- `openspec list` 验证

**验收**:
- HEAD 在 release commit
- v0-9 change 已移入 archive
- archive/CLI-yaml 索引同步

---

**总测试目标**:v0.8 → v0.9 测试数 710 → ~770 (+60 个新测试)
