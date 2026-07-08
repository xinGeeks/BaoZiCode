# v0.9 Command Registry — Design

## 1. Context

BaoZiCode v0.6 在 `baozicode/tui/chat_screen.py` 硬编码了 16 个 slash 命令,
长成一段 40 行的 `if/elif` 树 (`_handle_slash`),列表项另存在同一文件的
`SLASH_COMMANDS` tuple 中。这种命令增长方式把 TUI、命令定义、handler 实现
和补全逻辑糅在一起,在三个方向上失分:

- **可测性**:handler 直接调 `self.app.xxx()`,单元测试要拉起整个 Textual
  App + mock 一堆内部状态
- **可扩展性**:加一个命令要改 4 处(if/elif 一行 + SLASH_COMMANDS 一行 +
  `_help_text` 描述 + 写 `_cmd_xxx` 方法)
- **可复用性**:未来要再加 CLI 子命令、HTTP 接口,得把这一坨整段重抄

更糟的是已有 6 个命令(**`/exit /model /tools /mcp /stop /auto`**)在 v0.5/v0.6
累积过程中加入了但**没形成清晰分层**:`/auto` 实际上是 v0.5 L5 权限 modal 的
内部入口,被切割出来当用户面 slash 反而模糊了用户对"Agent 是自动还是人工"
的判断;`/model /tools /mcp /stop` 4 个则是 v0.6 调试期临时塞进去的状态检查
入口,在 v0.7 上下文压缩 / v0.8 存档 / v0.9 重构命令的语境下属于"杂事"。

v0.9 把 slash 命令从 **TUI 装饰** 升级成 **一等公民模块**,独立成一个
`baozicode/commands/` 顶层包,持元数据 + boot 校验 + 接口隔离 + 实时补全 +
10 个内置命令,职责干净、可测。

## 2. Goals

- **统一注册**:所有命令用 `CommandDef(...)` 一行注册,加新命令不碰 dispatcher
- **Boot panic**:别名撞名在 `freeze()` 阶段就退出,不延迟到运行
- **大小写不敏感**:用户打字 `PLAN` `plan` `Plan` 都对
- **Handler async + 联合类型**:未来加 IO 不用改签名
- **Narrow context**:handler 通过 ctx 拿 7 个动作 + 2 个属性,不 import textual
- **Tab 实时补全**:每次按键都算,空输入不弹单匹配
- **状态栏 [DEFAULT]/[PLAN] 标记**:plan_mode 一目了然
- **/plan /do 严格动词**:只切模式,args 静默丢
- **删除 6 个杂事命令**:`/exit /model /tools /mcp /stop /auto`,changelog
  标注迁移路径(`/auto` 用户行为合入 `/permission mode auto`)
- **`/permission` 主 + `/permissions` 别名**:匹配 v0.5-v0.6 老用户肌肉记忆

## 3. Architecture

### 3.1 模块结构

```
baozicode/commands/
├── __init__.py        # 公开 API:CommandRegistry / CommandType / CommandContext
│                      #           / CommandResult / dispatch / register_builtins
├── registry.py        # CommandDef / CommandRegistry / freeze() / lookup() / all_visible()
├── context.py         # CommandContext 接口 + TextualContext 实现(只 import textual 不 import business)
├── dispatcher.py      # parse_command() + dispatch() — slash 解析+路由
├── completor.py       # TabCompleter.candidates(prefix) -> list[str],单匹配 helper
└── builtin.py         # register_all(registry) — 10 个内置命令注册函数
```

依赖方向严格保持新约束:

```
tui/chat_screen.py  ─→  commands/  ─→  permissions/types.py
                       │          ─→  llm/base.py  (UsageStats 类型)
                       │          ─→  textual/screen.py  (push_modal 类型 hint)
                       ─→  config/schema.py  (CommandsConfig)
```

**`commands/` 不 import `baozicode/agent/` / `tui/` / `prompt/` / `sessions/`**。
这是新约束 — 用于让 commands 包可独立 unit-test + 未来加 CLI/HTTP 前端。

### 3.2 数据流图

```
┌──────────────────────────────────────────────────────────────┐
│ 用户在 Input widget 输入                                       │
└─────────────┬─────────────────────────────────────┬──────────┘
              │ on_input_changed (每按一键)         │ on_submitted (Enter)
              ↓                                     ↓
┌────────────────────────────┐    ┌────────────────────────────────┐
│ TabCompleter.candidates()  │    │ Dispatcher.dispatch(input)     │
│  - 过滤 hidden             │    │   输入空 → no-op               │
│  - 过滤大小写               │    │   首字非 / → Agent.run(input)  │
│  - prefix 命中             │    │   首字 / → parse + route       │
│  返回 list[str]             │    └──────────┬─────────────────────┘
└────────────┬───────────────┘               │
             ↓                                ↓
┌────────────────────────────┐    ┌────────────────────────────────┐
│ Menu(0匹配→hint            │    │ parse_command:                │
│     1匹配→自动补全          │    │   split 第一空格 → (name, args)│
│     2+→弹菜单)             │    │   name.lower()                │
└────────────────────────────┘    │   registry.lookup()            │
                                  └──────────┬─────────────────────┘
                                             ↓
                                  ┌────────────────────┐
                                  │ 未命中 → ctx.error │
                                  │                  │
                                  │ 命中 → handler    │
                                  │   (args, ctx)     │
                                  └────────┬─────────┘
                                           ↓
                                  handler returns CommandResult:
                                  ┌────────┼────────┐
                                  ↓        ↓        ↓
                              Local    UiState   PromptResult(text)
                                │        │         │
                                │        │         ↓
                                │        │   ctx.send_to_agent(text)
                                │        ↓
                                │   ctx.refresh_status() + 内部状态(plan_mode/session_mode)
                                ↓
                            (no chat echo unless ctx.show_info called)
```

### 3.3 Registry 内部结构

```python
@dataclass(frozen=True)
class CommandDef:
    name: str                           # 主名,lowercase a-z + "-"
    aliases: tuple[str, ...] = ()       # 附加名,同字符集
    description: str = ""               # 一行简介
    usage: str = ""                     # 用法示例
    type: CommandType = CommandType.LOCAL  # LOCAL / UI_STATE / PROMPT
    params_hint: str | None = None
    hidden: bool = False
    handler: CommandHandler | None = None  # async (args, ctx) -> CommandResult

class CommandType(str, Enum):
    LOCAL = "local"          # 纯本地,无回显
    UI_STATE = "ui_state"    # 改界面状态(plan_mode / archiver / 等)
    PROMPT = "prompt"        # 返回文本送 Agent

@dataclass(frozen=True)
class LocalResult: pass
@dataclass(frozen=True)
class UiStateResult: pass
@dataclass(frozen=True)
class PromptResult:
    text: str

CommandResult = LocalResult | UiStateResult | PromptResult

class CommandRegistry:
    def __init__(self) -> None:
        self._defs: list[CommandDef] = []
        self._index: dict[str, CommandDef] = {}   # name + alias -> def
        self._frozen: bool = False

    def register(self, def_: CommandDef) -> None: ...
    def freeze(self) -> None:                       # 别名冲突 → SystemExit
    def lookup(self, name: str) -> CommandDef | None: ...
    def all_visible(self) -> list[CommandDef]: ...
```

### 3.4 CommandContext 接口

```python
class CommandContext(Protocol):
    @property
    def app(self) -> BaoZiCodeApp: ...
    @property
    def config(self) -> AppConfig: ...

    def show_info(self, text: str) -> None: ...
    def show_error(self, text: str) -> None: ...
    def send_to_agent(self, text: str) -> None: ...
    def switch_mode(self, new_mode: PermissionMode | None) -> None: ...
    def get_token_usage(self) -> UsageStats: ...
    def refresh_status(self) -> None: ...
    def push_modal(self, screen: "Screen") -> None: ...
```

**只有一个实现 (`TextualCommandContext`)**,它持有 `app: BaoZiCodeApp`
引用,把方法分发到 chat_screen 的实际接口。Handler 引用的是 Protocol,不
import textual。

### 3.5 Status bar 更新

`ChatScreen._update_status_bar()` 在末尾拼接 mode marker:

| 模式         | 标记            |
|--------------|-----------------|
| default      | `[DEFAULT]`     |
| strict       | `[STRICT]`      |
| permissive   | `[PERMISSIVE]`  |
| plan         | `[PLAN]`        |

`/plan` 切到 `[PLAN]`(plan_mode=True),`/do` 切回 `[DEFAULT]`(plan_mode=False
且 session_mode 未设)。状态栏其余字段(token / session_id / model)不变。

## 4. Implementation Notes

### 4.1 ChatScreen 接入

`ChatScreen.compose()` 加一个 Input widget — `Input(placeholder="输入消息或 / 开头命令…")`。
现有 `_input` 字段名继续用,但所有事件订阅从 inline lambda 改成：

- `on_input_changed` → `self._on_input_changed(event)` → 调
  `TabCompleter.candidates(event.value)` → 触发菜单显示 / 自动补全
- `on_input_submitted` → `self._on_input_submitted(event)` → 调
  `Dispatcher.dispatch(event.value, ctx)` → local/ui-state/prompt 三分支

删除:
- `SLASH_COMMANDS` tuple(16 项)
- `_handle_slash` 整段 if/elif 树(40 行)
- 6 个被砍命令的 `_cmd_exit/_cmd_model/_cmd_tools/_cmd_mcp/_cmd_stop/_cmd_auto`
- `_help_text` 字符串 — 由 registry.all_visible() 渲染

### 4.2 ChatScreen 删 6 旧命令的迁移

| 旧 | 替代 |
|----|------|
| `/exit` | `Ctrl+C` 绑定已在 `BINDINGS`;在 status bar 加 hint 一行 |
| `/model` | 修改 config.yaml 重启;banner 标注 |
| `/tools` | 合并入 `/status` 输出 |
| `/mcp` | banner 在启动时打印 MCP 状态;不再单独命令 |
| `/stop` | `Ctrl+C` + v0.7 已支持的 agent cancel hook |
| `/auto` | `/permission mode permissive` 效果相同,L5 modal 内部仍走 `permission_callback` |

### 4.3 /session 实现走委派

`/session` handler 实现:

```python
async def session_cmd(args: str, ctx: CommandContext) -> CommandResult:
    from baozicode.tui.startup_session_screen import StartupSessionScreen
    screen = StartupSessionScreen(current_session_id=ctx.app.session_id)
    chosen = await ctx.push_modal(screen)
    if chosen is None:  # 用户取消
        return UiStateResult()
    if chosen == "__new__":
        ctx.app.start_new_session()
    else:
        await ctx.app.resume_session(chosen)
    ctx.refresh_status()
    return UiStateResult()
```

无 args。`/session foo` 与 `/session` 等价。

### 4.4 /review 实现

```python
async def review_cmd(args: str, ctx: CommandContext) -> CommandResult:
    since = args.strip() or "本次会话开始"
    prefix = ctx.config.commands.review_prompt  # 可选覆盖
    if not prefix:
        prefix = "请审查当前会话自 {since} 以来的所有改动(patch、命令输出、对话)。\n输出三段:## 摘要 / ## 风险点 / ## 建议修复。"
    text = prefix.replace("{since}", since)
    return PromptResult(text=text)
```

`/review` 是 10 个里**唯一**的 `PROMPT` 类型,演示 prompt-injection 路径。

### 4.5 /permission 实现

```python
async def permission_cmd(args: str, ctx: CommandContext) -> CommandResult:
    args = args.strip()
    valid = {"default", "strict", "permissive", "plan"}
    if not args:
        ctx.show_info(f"当前 mode: {ctx.app.effective_mode().value}\n用法: /permission [default|strict|permissive]")
        return LocalResult()
    if args not in valid:
        ctx.show_error(f"未知 mode: {args}. 有效值: default / strict / permissive / plan")
        return LocalResult()
    if args == "plan":
        ctx.app.session_mode = PermissionMode.DEFAULT  # plan_mode 是独立 signal
        # 通过 properties 把 plan_mode 切到 True
    else:
        ctx.switch_mode(PermissionMode(args))
    ctx.refresh_status()
    return UiStateResult()
```

`/permission` 是 v0.5 PermissionMode 切换的标准入口。

## 5. Boot Order 调整

App bootstrap 时(v0.9):

```python
def __init__(self, ...):
    # ... v0.8 bootstrap (permissions → instructions → memory → sessions) ...
    # v0.9 NEW: registry 启动校验
    from baozicode.commands.builtin import register_all
    self.command_registry = CommandRegistry()
    register_all(self.command_registry)
    self.command_registry.freeze()  # alias 冲突 → SystemExit
```

`freeze()` 失败 → 进程 panic,stderr 打印 `alias collision: <name> -> <cmd1> / <cmd2>`,
exit code 1。

## 6. Risk & Open Questions

### 6.1 已知风险

- **`textual` 在 `commands/context.py` 里的 type hint**:`push_modal(Screen)`
  的 `Screen` 是 `from textual.screen import Screen`。这构成 `commands →
  textual` 单向依赖,被 `commands/ → tui/` 的反向依赖不同方向,可接受。
  未来若要彻底脱离 textual,需把 `push_modal` 改成 `Any` 类型 escape hatch。

- **`send_to_agent` 在 busy 状态下排队**:依赖 chat_screen 现有
  `self.app._current_agent` 状态判断。如果 v0.10 改成完全事件驱动的 Agent
  loop,语义需要重新审视。

- **删除 6 旧命令是 breaking change**:v0.5-v0.6 用户如果脚本里写了这些命令
  会失效。changelog 写明 + 不搞 version 6 个月双轨期(v0.9 是 0.x 早期,
  break 边界合理)。

### 6.2 已决策

- 不需要 `CommandRegistry.unregister`(v0.9 没有动态插件)
- 不需要命令分组 / 命令权限(只有 10 个,平铺足够)
- 不需要国际化(描述用中文,符合中文用户主语)
- 不需要 command palette GUI(下版考虑,Tab 补全够用)

## 7. OpenSpec Spec Coverage

| Spec | 覆盖需求 |
|-------|----------|
| `command-registry` | 元数据形状 + alias 校验 + case-insensitive + hidden + handler 签名 + CommandResult 联合 + 10 命令注册 + /plan /do 严格动词 + /review prompt 内容 |
| `command-context` | 7 方法 + 2 属性 + send_to_agent 排队 + switch_mode 语义 + push_modal escape hatch + import audit |
| `slash-dispatcher` | Enter 分流 + 解析 first whitespace + 未知命令引导 + Tab 实时补全(单/多/隐藏) + 空前缀全部补全 + 完成回退 |
