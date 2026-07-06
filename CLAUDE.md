项目名：BaoZiCode
本地语言：中文

## v0.1 范围

多轮对话 TUI + Anthropic / OpenAI / MiniMax / DeepSeek 四后端 + YAML 配置 + SSE 流式 Markdown 渲染。v0.2 才会加工具调用（Read/Write/Edit/Bash/...）。

## 模块结构

```
baozicode/
├── __main__.py         # python -m baozicode
├── cli.py              # argparse 入口
├── app.py              # Textual App（持有 config / conversation / llm_client）
├── tui/
│   ├── chat_screen.py  # 主对话屏幕 + 斜杠命令 + 流式渲染
│   ├── banner.py       # ASCII 包子
│   └── styles.tcss     # Textual 样式
├── llm/
│   ├── base.py         # LLMClient ABC、Message、ContentDelta
│   ├── anthropic.py    # AnthropicBackend（独立 SDK）
│   ├── openai.py       # OpenAICompatibleBackend 基类 + OpenAIBackend 子类
│   ├── minimax.py      # MiniMaxBackend（OpenAI 兼容）
│   ├── deepseek.py     # DeepSeekBackend（OpenAI 兼容）
│   └── factory.py      # create_client(config) → LLMClient
├── conversation/
│   └── manager.py      # 多轮历史（仅内存，v0.1 不持久化）
└── config/
    ├── schema.py       # Pydantic AppConfig / BackendConfig（4 后端全部必填）
    └── loader.py       # YAML + .env + ${VAR} 替换
```

## 依赖方向（不要打破）

```
tui/  →  conversation/  →  llm/base.py
                  ↓
                 llm/{anthropic,openai}.py
                  ↓
                config/
```

- `tui/` 不能直接 import `anthropic` / `openai`（必须经 `llm/factory.py`）
- `llm/` 不能 import `textual`
- 业务状态挂在 `App` 实例上，Screen 通过 `self.app` 访问

## 关键约定

- LLM 抽象：`LLMClient.stream(messages, system) -> AsyncIterator[ContentDelta]`
- Anthropic 的 `system` 走独立参数；OpenAI 走 `messages[0] role=system` —— 差异在每个 backend 内部消化
- 入口命令：`baozicode`（在 `pyproject.toml` 的 `[project.scripts]` 声明）
- 配置：YAML 写非敏感配置，`.env` 写 API Key，YAML 用 `${ENV_VAR}` 占位符引用

## OpenSpec

`openspec/changes/` 下是 spec-driven 的变更提案。当前活跃 change 是 `add-minimax-deepseek-backends`（v0.1 已归档到 `archive/`）。
