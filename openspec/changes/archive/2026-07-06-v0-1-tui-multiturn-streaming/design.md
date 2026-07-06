## Context

BaoZiCode 是从零开始的 Python 项目。v0.1 范围已经与用户对齐：终端 TUI + 多轮对话 + Anthropic/OpenAI 双后端 + YAML 配置 + SSE 流式 Markdown 渲染。

本文档说明 v0.1 的**架构与实现方式**，不解释动机（动机见 `proposal.md`），不写行级实现（行级任务在 `tasks.md`）。

当前状态：
- 仓库为空（只有 `.git/`、`.claude/`、`openspec/`、`docs/`、`CLAUDE.md`）
- Python 版本目标：3.11+（需要 `tomllib`、`asyncio` 改进、`Self` 类型等）
- 目标平台：终端（Windows Terminal、macOS Terminal、Linux 各种）

## Goals / Non-Goals

**Goals：**
- 启动 `baozicode` 后 1 秒内看到 ASCII 包子 banner 与欢迎语
- 用户敲回车后，AI 回答以流式渲染（边收边显示、代码块语法高亮）
- 多轮上下文正确维持：第 N 轮对话能把前 N-1 轮整段发给 LLM
- 修改 `config.yaml` 中 `backend: anthropic ↔ openai` 即可切换后端，**代码无改动**
- 4 个斜杠命令（`/help` `/clear` `/exit` `/model`）立即可用
- 流式输出期间输入框锁定，输出完成自动解锁
- 模块分层清晰：UI / 对话 / LLM 客户端 / 配置 四层互不渗透

**Non-Goals：**
- 工具调用（Read/Write/Edit/Bash/Grep/Glob/WebFetch）—— v0.2
- 对话持久化到磁盘 —— v0.2
- 子代理、子任务、计划模式 —— v0.3+
- 多模态（图片、文件）—— 暂不做
- token/成本统计显示 —— v0.2
- 权限系统 —— v0.3
- HTTP 代理、超时自定义等高级网络设置 —— 用 SDK 默认即可
- 国际化（i18n）—— 仅中文/英文混排，不抽象

## Decisions

### D1. TUI 框架：Textual

**决策**：使用 `textual` 作为 TUI 框架。

**理由**：
- 原生 async 编程模型（`@on` 事件、`async def` 消息处理），与 LLM 流式天然契合
- 自带 `Markdown` 组件 + `MarkdownStreamer`，开箱即用地实现 L4 级别"边收边高亮"
- 组件化、TCSS 样式、屏幕切换——未来加工具确认弹窗、计划模式 UI 都有现成基础设施
- 维护活跃（Textualize 公司），文档完整

**备选**：
- Rich + prompt_toolkit：更轻量，但要自己拼流式刷新和 Markdown 增量渲染，v0.2 加工具确认弹窗时也要重写
- prompt_toolkit 单用：底层但开发体验差

**结论**：Textual 的"重"换来的是"少写一半代码"和"未来功能有地方放"。

---

### D2. LLM 客户端：薄层包装官方 SDK

**决策**：直接使用 `anthropic` 和 `openai` 官方 Python SDK，在它们之上抽出一个**极薄**的 `LLMClient` 抽象类。

**理由**：
- SSE 解析、错误重试、token 计数这些脏活 SDK 已经处理了，没必要重复造
- 抽象层只关心"输入消息列表、输出增量 token 文本"，把"消息 ↔ SDK 调用"的转换藏在每个 backend 里
- 抽象层接口稳定，v0.2 加 tool_use 时只需扩展 `ContentDelta` 的类型变体

**抽象层签名**：
```python
class LLMClient(ABC):
    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
    ) -> AsyncIterator[ContentDelta]: ...
```

**数据类型**：
```python
@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str  # v0.1 简化：纯文本；v0.2 改为 list[ContentBlock]

@dataclass
class ContentDelta:
    type: Literal["text", "thinking", "tool_use"]  # v0.1 只用 "text"
    text: str  # 增量文本
```

**关键差异处理**：Anthropic 的 `system` 是消息数组外的独立参数，OpenAI 的 `system` 是 `messages[0]` 且 `role="system"`。两个 backend 各自负责转换，**上层 ConversationManager 只调一个 `stream(messages, system)` 即可**。

**备选**：
- 自己撸 `httpx` + 解析 SSE：教育意义大但代码量翻倍，SSE 重连/错误重试都要自己写
- LangChain / LlamaIndex：太重，抽象层渗入业务代码

**结论**：薄层包装是"学得到 + 跑得稳"的甜点。

---

### D3. 流式渲染：Textual Markdown + MarkdownStreamer

**决策**：使用 Textual 的 `Markdown` widget 配合 `MarkdownStreamer` 实现边收边渲染。

**理由**：
- 这是 Textual 官方为流式 Markdown 设计的组件，完美契合 L4 需求
- 内部用增量解析，不会每次都重渲染整段，性能可控
- 代码块自动调用 Pygments 语法高亮

**实现要点**：
```python
# 在 chat_screen.py 中
markdown_widget = Markdown()
streamer = MarkdownStreamer(markdown_widget)

async for delta in llm_client.stream(messages, system):
    await streamer.stream_token(delta.text)  # 喂 token 进去
```

**备选**：
- 自己用 Rich 的 `Console` 重画：性能差，要自己处理光标定位
- 不渲染 Markdown 直接显示原文：体验差

**结论**：用 Textual 的现成方案，不重新发明。

---

### D4. 配置加载：YAML + .env 双层

**决策**：
- 非敏感配置（`backend` 名称、模型名、`base_url`）写在 `config.yaml`
- 敏感配置（API Key）写在 `.env` 文件
- YAML 中 `api_key` 字段填 `${ENV_VAR_NAME}` 占位符，启动时由 `python-dotenv` 加载后替换

**理由**：
- `.env` 天然不入 git（写进 `.gitignore`），避免 API Key 泄露
- YAML 仍可分享、版本管理（个人项目里跨机器同步）
- 业界主流做法（如 `docker-compose`、各种 Python SaaS 框架）

**配置文件查找顺序**：
1. `--config <path>` 命令行参数（最高优先级）
2. 当前目录 `./config.yaml`
3. 用户 home `~/.config/baozicode/config.yaml`
4. 报错退出并提示用 `config.example.yaml` 初始化

**配置 schema（Pydantic）**：
```python
class AppConfig(BaseModel):
    backend: Literal["anthropic", "openai"]
    system_prompt: str = "You are BaoZiCode, a helpful AI coding assistant."
    anthropic: BackendConfig
    openai: BackendConfig

class BackendConfig(BaseModel):
    api_key: str
    model: str
    base_url: str | None = None
```

**示例 `config.yaml`**：
```yaml
backend: anthropic
system_prompt: "You are BaoZiCode..."

anthropic:
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-sonnet-4-6
  # base_url: https://api.anthropic.com  # 可选

openai:
  api_key: ${OPENAI_API_KEY}
  model: gpt-5
  # base_url: https://api.openai.com/v1
```

**结论**：双层结构干净、安全、易于分享。

---

### D5. 流式期间输入框锁定

**决策**：模型正在输出时，输入框 `disabled` 状态生效；输出完成后自动启用。

**理由**：
- 实现最简单（一个布尔状态 + `Input.disabled` 切换）
- 避免竞态：用户敲了新内容，旧输出还没完，状态会乱
- 后期要做"边打边打断"（v0.2 候选）再升级状态机

**实现要点**：
```python
class ChatScreen(Screen):
    is_streaming: bool = False

    async def send_message(self, user_text: str):
        self.is_streaming = True
        self.query_one("#input", Input).disabled = True
        # ... 调用 stream，渲染到 Markdown
        self.is_streaming = False
        self.query_one("#input", Input).disabled = False
        self.query_one("#input", Input).focus()
```

**结论**：先稳后巧，v0.1 用最简单方案。

---

### D6. 斜杠命令：Textual `Input.submitted` 前缀检测

**决策**：在 `Input.submitted` 事件中检测文本是否以 `/` 开头，是则路由到命令处理器；否则当作用户消息发送给 LLM。

**4 个命令**：
- `/help` — 弹出帮助面板（用 `ModalScreen`）
- `/clear` — 清空对话历史 + 清空 Markdown widget
- `/exit` — 调用 `app.exit()`
- `/model` — 在状态栏弹出选项：`Anthropic: claude-sonnet-4-6` / `OpenAI: gpt-5`（或当前 YAML 中配的模型名）

**理由**：
- Textual 事件模型天然支持命令分发（一个 `if/elif` 链足够）
- `/model` 的"热切换"演示了 v0.1 的核心架构价值——**不重启 UI，只换 LLM client 实例**

**结论**：4 个命令功能清晰，实现轻量。

---

### D7. 模块结构

```
baozicode/
├── __init__.py
├── __main__.py                # python -m baozicode
├── cli.py                     # argparse: --config
├── app.py                     # BaoZiCodeApp(Textual App)
├── tui/
│   ├── __init__.py
│   ├── chat_screen.py         # 主对话屏幕
│   ├── widgets.py             # 复用 widgets（如果有）
│   ├── banner.py              # ASCII 包子
│   └── styles.tcss            # Textual 样式
├── llm/
│   ├── __init__.py
│   ├── base.py                # LLMClient, Message, ContentDelta
│   ├── anthropic.py           # AnthropicBackend
│   ├── openai.py              # OpenAIBackend
│   └── factory.py             # create_client(config) -> LLMClient
├── conversation/
│   ├── __init__.py
│   └── manager.py             # ConversationManager
└── config/
    ├── __init__.py
    ├── loader.py              # load_config(path) -> AppConfig
    └── schema.py              # AppConfig, BackendConfig
```

**目录边界规则**：
- `tui/` 不能直接 `import anthropic` 或 `openai`（只通过 `llm/factory.py`）
- `llm/` 不能直接 `import textual`（业务无关）
- `conversation/` 只能被 `tui/` 和 `llm/base.py` 引用

**结论**：四层清晰、依赖单向，方便后续测试和扩展。

---

### D8. 入口命令

**决策**：
- `pyproject.toml` 中声明 `[project.scripts]`：`baozicode = "baozicode.cli:main"`
- 同时支持 `python -m baozicode`
- 接收可选参数 `--config <path>` 覆盖默认配置文件位置

**结论**：标准 Python 包分发做法。

---

### D9. 启动 Banner：ASCII 包子

**决策**：在 TUI 启动时（`on_mount` 时）显示一个居中的 ASCII 包子 + "BaoZiCode" 字样 + 简短欢迎语。

**草案**（用 Textual 的 `Static` widget 渲染）：
```
        .-^^---.
       /  o   o \
      |    ∆     |     BaoZiCode
       \   ___  /      v0.1 — TUI 多轮 AI 编码助手
        '-._____.-'
       /___________\
      | 蒸 笼 蒸 笼  |
      |_____________|
```

**结论**：纯装饰，不影响功能。未来可定制。

---

## Risks / Trade-offs

- **R1：Textual 版本升级可能 break API** → Mitigation：固定 minor 版本号（如 `textual>=0.50,<0.60`），跟进 release notes
- **R2：官方 SDK 流式行为差异**（如 OpenAI 的 `stream=True` 和 Anthropic 的 `messages.stream`）可能在边界 case 表现不一致 → Mitigation：抽象层只暴露增量 `text` 字符串，差异隐藏在 backend 内部；写单测验证两边行为对齐
- **R3：流式中途断网**（SDK 抛异常）→ Mitigation：捕获异常，渲染错误到对话区（红色），自动解锁输入框，保留已收到的部分回答
- **R4：API Key 缺失或错误**（启动时报 401）→ Mitigation：启动时做一次最小验证（不消费 token 的轻量调用，如 Anthropic 的 `models.list`），失败则报错退出并提示检查 `.env`
- **R5：Markdown 流式渲染不完整**（如三引号代码块在收到结束 ``` 之前已经渲染了一段）→ Mitigation：Textual 的 `MarkdownStreamer` 内部用 buffer 处理，理论上 OK；写测试覆盖 "收到一半代码块 + 后面补全" 的场景
- **R6：长对话导致 token 超限** → Mitigation：v0.1 接受这个限制，超限时报错并提示用户 `/clear`（v0.2 加自动截断/摘要）
- **R7：Textual 在 Windows 终端上的兼容问题**（Windows Terminal vs cmd.exe）→ Mitigation：README 中注明推荐 Windows Terminal / PowerShell 7+；CI 中跑 Windows 平台的冒烟测试

## Open Questions

无。所有关键决策已在 explore 阶段与用户对齐。剩余细节（如默认欢迎语文案、ASCII 包子的具体字符）在 `tasks.md` 任务级别里迭代。

## Migration Plan

不适用——新项目，无既有代码需要迁移。
