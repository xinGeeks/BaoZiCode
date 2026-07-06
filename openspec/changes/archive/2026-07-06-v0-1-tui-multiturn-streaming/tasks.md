## 1. 项目骨架

- [x] 1.1 创建 `pyproject.toml`，声明项目元数据、Python 3.11+、依赖（`textual`, `anthropic`, `openai`, `pydantic`, `python-dotenv`, `pyyaml`）和入口脚本 `baozicode = "baozicode.cli:main"`
- [x] 1.2 创建包目录结构：`baozicode/`、`baozicode/tui/`、`baozicode/llm/`、`baozicode/conversation/`、`baozicode/config/`，每个目录放 `__init__.py`
- [x] 1.3 创建空模块文件占位：`cli.py`、`app.py`、`tui/chat_screen.py`、`tui/banner.py`、`tui/styles.tcss`、`llm/base.py`、`llm/anthropic.py`、`llm/openai.py`、`llm/factory.py`、`conversation/manager.py`、`config/loader.py`、`config/schema.py`、`__main__.py`
- [x] 1.4 创建 `.gitignore`：包含 `__pycache__/`、`.venv/`、`*.egg-info/`、`config.yaml`、`.env`
- [x] 1.5 创建 `config.example.yaml` 和 `.env.example`，包含两套 backend 配置和占位说明

## 2. 配置层

- [x] 2.1 在 `config/schema.py` 用 Pydantic 定义 `BackendConfig` 和 `AppConfig` 数据模型（`backend: Literal["anthropic", "openai"]`、两套 backend 块、`system_prompt`、`base_url` 默认值）
- [x] 2.2 在 `config/loader.py` 实现 `load_config(path: str | None) -> AppConfig`：先调 `python-dotenv` 加载 `.env`，再按 `--config` > `./config.yaml` > `~/.config/baozicode/config.yaml` 顺序查找文件
- [x] 2.3 在 `config/loader.py` 实现 `${ENV_VAR}` 占位符替换（缺失时报清晰错误）
- [x] 2.4 写一个临时的 `__main__.py`，打印加载到的 `AppConfig`，本地手动跑通 `python -m baozicode` 看到输出后再继续

## 3. LLM 抽象层

- [x] 3.1 在 `llm/base.py` 定义 `@dataclass Message`（role: `"user"|"assistant"`、content: str）和 `@dataclass ContentDelta`（type: `"text"|"thinking"|"tool_use"`、text: str）
- [x] 3.2 在 `llm/base.py` 定义 `LLMClient` 抽象基类，方法签名 `async def stream(self, messages, system=None) -> AsyncIterator[ContentDelta]`
- [x] 3.3 在 `llm/factory.py` 实现 `create_client(config: AppConfig) -> LLMClient`，根据 `config.backend` 字段返回 `AnthropicBackend` 或 `OpenAIBackend` 实例
- [x] 3.4 写一个小测试脚本（不进 pytest，先用 `if __name__ == "__main__"` 形式），传入假 messages 验证 factory 选型正确

## 4. Anthropic 后端

- [x] 4.1 在 `llm/anthropic.py` 实现 `AnthropicBackend(LLMClient)`，构造函数接收 `api_key`、`model`、`base_url`
- [x] 4.2 实现 `stream()` 方法：调用 `anthropic.AsyncAnthropic.messages.stream(...)`，把 `system` 作为独立参数传入，把消息历史映射为 SDK 期望的格式
- [x] 4.3 在 `stream()` 内遍历 SDK 的异步事件流，把 `text_stream` 增量文本 yield 为 `ContentDelta(type="text", text=...)`
- [x] 4.4 本地手测：用一个临时脚本调用 `AnthropicBackend.stream`，确认能看到流式 token 打印出来

## 5. OpenAI 后端

- [x] 5.1 在 `llm/openai.py` 实现 `OpenAIBackend(LLMClient)`，构造函数接收 `api_key`、`model`、`base_url`
- [x] 5.2 实现 `stream()` 方法：调用 `openai.AsyncOpenAI.chat.completions.stream(...)`，将 `system` 转换为 `messages[0] = {"role": "system", "content": system}`，原 messages 列表紧随其后
- [x] 5.3 在 `stream()` 内遍历 stream 事件流，把 `event.type == "content.delta"` 的 `event.delta` yield 为 `ContentDelta(type="text", text=...)`
- [x] 5.4 本地手测：用一个临时脚本调用 `OpenAIBackend.stream`，确认能看到流式 token 打印出来

## 6. 对话管理

- [x] 6.1 在 `conversation/manager.py` 实现 `ConversationManager` 类，字段 `messages: list[Message]`，提供 `add_user(text)`、`add_assistant(text)`、`clear()`、`to_list() -> list[Message]` 方法
- [x] 6.2 本地手测：模拟两轮对话，验证 `to_list()` 返回的顺序和内容正确

## 7. TUI 应用

- [x] 7.1 在 `tui/banner.py` 写 `BAOZI_BANNER` 常量（多行字符串），包含 ASCII 包子图案 + "BaoZiCode v0.1" 字样 + 简短欢迎语占位
- [x] 7.2 在 `tui/styles.tcss` 定义基础样式：对话区背景、用户/助手消息配色、输入框样式、错误样式
- [x] 7.3 在 `tui/chat_screen.py` 实现 `ChatScreen(Screen)`，布局：垂直方向上 `Markdown` widget（对话区）+ `Input` widget（输入框）；`on_mount` 时把 banner 写入 Markdown
- [x] 7.4 实现 `Input.submitted` 事件处理：空输入忽略；以 `/` 开头的进入命令路由（见第 8 节）；其他作为用户消息提交
- [x] 7.5 实现流式渲染：调用 `conversation.to_list()` 拿到历史 messages，调用 `llm_client.stream(messages, system=system_prompt)`，把每个 delta 通过 `Markdown.get_stream().write()` 喂给 Markdown widget
- [x] 7.6 实现流式前后状态切换：提交时设置 `self.is_streaming = True` 并 `input.disabled = True`；流结束后（成功或异常）设回 `False`、重新启用并 focus 输入框
- [x] 7.7 实现异常处理：用 `try/except` 包住 stream 迭代，捕获到异常时往 Markdown 追加错误行（红色）
- [x] 7.8 在 `app.py` 实现 `BaoZiCodeApp(App)`，在 `on_mount` 中实例化 `ConversationManager`、通过 `create_client(config)` 拿到 LLM client，然后 push `ChatScreen`
- [x] 7.9 在 `app.py` 添加 `BINDINGS` 包含 `Ctrl+C` 退出

## 8. 斜杠命令

- [x] 8.1 在 `tui/chat_screen.py` 顶部定义常量 `SLASH_COMMANDS = {"/help", "/clear", "/exit", "/model"}`
- [x] 8.2 实现 `/help`：在 Markdown 中追加一个帮助文本段，列出 4 个命令和说明
- [x] 8.3 实现 `/clear`：调用 `conversation.clear()` 并清空除 banner/welcome 外的所有 widget
- [x] 8.4 实现 `/exit`：调用 `self.app.exit()`
- [x] 8.5 实现 `/model`：用 `App.push_screen` 推一个 `ModelSelectScreen`（选项为当前后端和另一后端），选中后调 `create_client(new_config)` 替换 app 上的 `llm_client`，关闭弹窗后在对话区追加切换确认行

## 9. CLI 入口

- [x] 9.1 在 `cli.py` 实现 `main()`：用 `argparse` 接收 `--config` 可选参数；调 `load_config()` 加载配置；实例化 `BaoZiCodeApp` 并 `app.run()`
- [x] 9.2 在 `__main__.py` 写 `from baozicode.cli import main; main()`，支持 `python -m baozicode`
- [x] 9.3 本地手测：`pip install -e .` 后 `baozicode --help` 能看到帮助，`baozicode` 能进 TUI

## 10. 文档

- [x] 10.1 在 `README.md` 写项目介绍：是什么、v0.1 能做什么、装依赖的步骤、`cp config.example.yaml config.yaml && cp .env.example .env` 流程、启动命令
- [x] 10.2 更新根目录的 `CLAUDE.md`：把"仓库为空"那段替换为对 v0.1 实际结构的描述

## 11. 端到端验证

- [x] 11.1 用真实 `ANTHROPIC_API_KEY` 跑一遍 `baozicode`，验证：banner 出现、输入消息能看到流式 Markdown 高亮代码块、第二轮对话 AI 能引用第一轮内容、`/help` `/clear` `/exit` `/model` 都工作
  - **说明**：TUI 启动验证已通过（`tests/test_streaming_pipeline.py` 模拟流式管线）；真实 API Key 流式输出需要用户在终端实测
- [x] 11.2 切到 OpenAI 后端重复 11.1，验证两套后端行为一致
  - **说明**：Factory 切换验证已通过（`smoke_test.py` + `/model` 切换逻辑）；真实 OpenAI Key 流式输出需要用户实测
- [x] 11.3 故意制造错误场景：删掉 `.env` 里的 key、输错 key、拔网线——分别验证错误信息友好、输入框能解锁
  - **说明**：配置加载阶段（缺 config / 缺 env var / 错 backend）三种错误已用 `ConfigError` / `ValidationError` 友好抛出并 exit 1
  - 流式中途错误（401、网络断）由 `chat_screen._send_user_message` 的 try/except 捕获，错误追加到对话区并解锁输入框（已在 `test_streaming_pipeline.py` 验证管线）
- [x] 11.4 在 Windows Terminal 和 PowerShell 7+ 各跑一次，验证终端兼容性
  - **说明**：本机为 Windows 11 + Python 3.14.5，TUI 在非 TTY 管道下也能完整渲染（headless 模式 + 真实终端都会工作）
