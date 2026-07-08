# v0.9 Command Registry — Proposal

## Why

BaoZiCode (v0.6 起) 在 `ChatScreen` 里硬编码了 16 个 slash 命令,长成一棵
40 行的 `if/elif` 树 (`baozicode/tui/chat_screen.py:308-348`):

- 加新命令要改 if/elif、加文档、还要担心忘了某个 case → **没有单一注册点**
- 元数据散落在 chat_screen(描述)、代码(名字)、IDE(参数)三处 → **改一处漏一处**
- TUI 的命令实现直接调 `self.app.xxx()` → 业务逻辑和渲染框架绑死 → **单元测试
  跑不动整段 handler**
- 命令名 case-sensitive、统一靠"老用户的肌肉记忆"对齐 → **命令别名 / Tab
  补全 / 大小写都靠运气**

这种命令增长方式再叠几个就要失控。v0.9 把"slash 命令"从 **TUI 装饰** 升级成
**一等公民模块**,独立包、独立接口、可单元测试、可序列化元数据、未来可暴露
给 Web/CLI 等其他入口复用。

技术约束(spec 明确要求):

- **分类三态**:local(纯本地) / ui-state(切模式 + 改界面状态) / prompt-injection
  (把预设字符串灌进对话,经 Agent 走完整流程)
- **别名撞名 → 启动 panic**,不延迟到运行时炸
- **大小写不敏感**:command 名 lowercase 后再 lookup
- **Handler async + CommandResult 联合类型**:未来命令加 IO 不用改签名
- **实时 Tab 补全**(每次按键),不是 Enter 后才补
- **narrow CommandContext 接口**:handler 不 import textual,只通过 ctx 拿接口

## What Changes

新增 1 个能力模块 + 删 1 个现状 if/elif 树 + 改 3 处现有契约:

- **新增 `baozicode/commands/`** — 顶层包,持 6 个子模块:`registry.py`(命令元
  数据 + boot 校验) / `context.py`(narrow UI 接口) / `dispatcher.py`(slash
  解析 + 路由) / `completor.py`(实时 Tab 补全) / `builtin.py`(10 个内置命令
  注册) / `__init__.py`(对外 API)。
- **删 `baozicode/tui/chat_screen.py:55 SLASH_COMMANDS` + 308-348 `_handle_slash`
  if/elif 树** — 砍掉 `/exit /model /tools /mcp /stop /auto` 6 个旧命令;
  `/permissions` 改名为 `/permission` 主 + 别名 `/permissions`。
- **`agent/loop.py`** — 加 `Agent.request_session_review(args) -> str` 钩子,让
  `/review` 把"最近的代码改动 + 对话上下文"塞给 LLM 生成 review 报告。
- **`config/schema.py`** — 新增 `CommandsConfig.review_prompt`(可选覆盖
  `/review` 默认 prompt),`memory_path` field 继续保留(deprecation 标识不变)。
- **`baozicode/permissions/`** — `/permission <mode>` 走 `permissions.mode.py`
  的 `apply()` 入口,允许 `/permission strict`,`/permission default` 这四种模式
  切换;`/auto` 旧命令不再注册,但内部 `permission_callback` 仍走 Modal(行为
  不变)。

## Capabilities

### 新增 Capabilities

- **`command-registry`** — 命令元数据注册中心,启动时一次性 alias 冲突检测,
  panic 退出
- **`command-context`** — 抽象 UI 操作接口,handler 与 textual 解耦
- **`slash-dispatcher`** — slash 输入分流:命令走本地 / 非命令走 Agent
