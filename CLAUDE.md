项目名：BaoZiCode
本地语言：中文

## v0.2 范围

多轮对话 TUI + 4 后端 + 7 个工具（agent loop）+ 权限系统。

## 模块结构

```
baozicode/
├── __main__.py         # python -m baozicode
├── cli.py              # argparse 入口
├── app.py              # Textual App（持有 config / conversation / llm_client）
├── tui/
│   ├── chat_screen.py  # 主对话屏幕 + agent loop + 斜杠命令 + 流式渲染
│   ├── tool_card.py    # ToolCallCard / ToolResultCard 组件
│   ├── permission_modal.py  # 高风险工具确认 Modal（含 batch 模式）
│   ├── banner.py       # ASCII 包子
│   └── styles.tcss     # Textual 样式
├── llm/
│   ├── base.py         # LLMClient ABC、Message、ContentBlock、ContentDelta
│   ├── anthropic.py    # AnthropicBackend（tool_use 流式累积 + JSON 解析容错）
│   ├── openai.py       # OpenAICompatibleBackend 基类 + OpenAIBackend 子类
│   ├── minimax.py      # MiniMaxBackend（OpenAI 兼容）
│   ├── deepseek.py     # DeepSeekBackend（OpenAI 兼容）
│   └── factory.py      # create_client(config) → LLMClient
├── tools/              # v0.2 新增
│   ├── base.py         # ToolDefinition / ToolCall / ToolResult dataclass
│   ├── read.py         # 读 UTF-8 文本（50KB/2000 行 cap）
│   ├── write.py        # 整文件覆写（自动 mkdir parents）
│   ├── edit.py         # 字符串精确替换（old_string 必须唯一）
│   ├── bash.py         # asyncio.create_subprocess_shell + cwd 三状态机
│   ├── grep.py         # ripgrep + Python re fallback
│   ├── glob.py         # pathlib.Path.glob 包装
│   ├── webfetch.py     # httpx + HTML 去 tags
│   └── registry.py     # get_all_tools / execute_tool
├── conversation/
│   └── manager.py      # 多轮历史（add_tool_call / add_tool_result / add_message）
└── config/
    ├── schema.py       # Pydantic AppConfig / BackendConfig / Permissions
    └── loader.py       # YAML + .env + ${VAR} 替换
```

## 依赖方向（不要打破）

```
tui/  →  conversation/  →  llm/base.py  →  tools/base.py
                  ↓                ↓
                 llm/{anthropic,openai}.py    tools/{read,write,...}.py
                  ↓                ↓
                config/            (via registry.py)
```

- `tui/` 不能直接 import `anthropic` / `openai`（必须经 `llm/factory.py`）
- `llm/` 不能 import `textual`
- `tools/` 不依赖 `tui/` / `llm/` / `conversation/`（纯函数 + 路径）
- 业务状态挂在 `App` 实例上，Screen 通过 `self.app` 访问

## 关键约定

- LLM 抽象：`LLMClient.stream(messages, system, tools) -> AsyncIterator[ContentDelta]`
- `ContentDelta.type`：`"text"` → str；`"tool_use"` → `ToolCall` 实例
- Anthropic 的 `system` 走独立参数；OpenAI 走 `messages[0] role=system` —— 差异在每个 backend 内部消化
- `Message.content` 是 `Union[str, list[ContentBlock]]`：`str` 是 v0.1 快速路径；`list[ContentBlock]` 含 `TextBlock / ToolUseBlock / ToolResultBlock`
- 工具调用统一抽象：`ToolDefinition`（喂给 LLM）+ `ToolCall`（LLM 请求）+ `ToolResult`（喂回 LLM），后端 SDK 类型不出 `baozicode/llm/`
- Bash cwd 三状态机：会话启动锁项目根 → `cd` 跟随 → 每次执行前 `Path.resolve().is_relative_to(project_root)` 防逃逸
- 入口命令：`baozicode`（在 `pyproject.toml` 的 `[project.scripts]` 声明）
- 配置：YAML 写非敏感配置，`.env` 写 API Key，YAML 用 `${ENV_VAR}` 占位符引用；`permissions:` 块 v0.2 可选

## OpenSpec

`openspec/changes/` 下是 spec-driven 的变更提案。当前活跃 change 是 `v0-2-tool-calling`（v0.1 已归档到 `archive/`）。