## Why

BaoZiCode 是一个新的 Python 命令行 AI 编码助手项目，目前仓库为空。v0.1 的目标是建立一个**可演示、可交互、架构清晰**的最小可信版本：一个能在终端里跑起来的多轮对话 TUI，把 LLM 流式回答渲染出来，并通过配置文件无缝切换 Anthropic 与 OpenAI 两个后端。

为什么是 v0.1 这个范围？因为 v0.1 之后的工具调用（Read/Write/Bash/...）、子代理、计划模式、权限系统等所有功能都将**生长在 v0.1 的对话骨架之上**。把对话骨架做扎实（消息流、状态机、后端抽象），后续功能才有干净的挂载点。

## What Changes

- 新增 Python 项目骨架（`pyproject.toml`、包结构、入口命令 `baozicode`）
- 新增 Textual TUI 应用：欢迎页、对话区、输入框、ASCII 包子 banner
- 新增 LLM 客户端抽象层 `LLMClient`，统一 `stream(messages, system) -> AsyncIterator[ContentDelta]` 接口
- 新增两个具体后端：`AnthropicBackend`（包 `anthropic-sdk`）和 `OpenAIBackend`（包 `openai-python`）
- 新增 YAML 配置加载器 + `.env` 集成（API Key 从 `.env` 读取，YAML 只引用变量名）
- 新增多轮对话管理器（仅内存，维护 `List[Message]`）
- 新增 4 个斜杠命令：`/help` `/clear` `/exit` `/model`
- 新增流式 Markdown 渲染（用 Textual 的 `Markdown` + `MarkdownStreamer`，边收边高亮）
- 新增流式期间输入框锁定（避免用户输入与渲染竞态）
- 新增示例配置文件 `config.example.yaml`

## Capabilities

### New Capabilities

- `interactive-tui`: Textual 终端交互界面，包括 ASCII 包子 banner、对话区、输入框、4 个斜杠命令、流式渲染与输入锁定
- `llm-streaming`: LLM 客户端抽象层（统一 `stream` 接口）、Anthropic 与 OpenAI 两个具体后端实现、SSE 流式 token 增量、多轮消息历史管理
- `configuration`: YAML 配置文件加载、`.env` 环境变量集成、`backend` 字段决定使用哪个 LLM 后端、配置文件位置查找策略

### Modified Capabilities

无（项目从零开始，无既有 spec 需要修改）

## Impact

**新增代码**（全部新建）：
- `baozicode/` Python 包
- `pyproject.toml`（依赖 `textual`、`anthropic`、`openai`、`pydantic`、`python-dotenv`、`pyyaml`）
- `config.example.yaml`
- `.env.example`

**新增依赖**（第三方）：
- `textual` — TUI 框架
- `anthropic` — Anthropic 官方 SDK
- `openai` — OpenAI 官方 SDK
- `pydantic` — 配置数据模型校验
- `python-dotenv` — `.env` 加载
- `pyyaml` — YAML 解析

**对现有系统的影响**：无（仓库当前为空，没有既有代码或接口需要兼容）
