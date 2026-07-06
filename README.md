# BaoZiCode 🥟

> 一个用 Python 开发的命令行 AI 编码助手，类似 Claude Code。

![version](https://img.shields.io/badge/version-0.2.0-blue)

## 是什么

BaoZiCode 是一个跑在终端里的多轮 AI 对话 TUI。它支持：

- 🧠 **多轮上下文** — AI 能记住之前说过的话
- ⚡ **流式响应** — 边收边渲染（代码块自动语法高亮）
- 🔌 **四后端** — Anthropic Claude / OpenAI GPT / MiniMax / DeepSeek，YAML 一键切换
- 🎨 **Textual TUI** — 现代终端界面，输入框、流式输出、ASCII 包子 banner
- 🛠️ **斜杠命令** — `/help` `/clear` `/exit` `/model` `/tools` `/permissions`
- 🧰 **工具调用**（v0.2+）— 7 个工具，agent loop 自动串联
- 🔒 **权限控制**（v0.2+）— auto_allow / deny / batch_confirm

## 当前版本：v0.2

- ✅ 7 个工具：Read / Write / Edit / Bash / Grep / Glob / WebFetch
- ✅ Agent loop — 流式吐文字 → 自动调工具 → 把结果喂回模型 → 续流
- ✅ Anthropic + OpenAI 兼容后端都支持 `tools` 参数
- ✅ Bash 有 cwd 三状态机（防目录逃逸）
- ✅ 权限弹窗（高风险工具逐一确认，可选批量模式）
- ✅ 6 个斜杠命令
- ❌ 对话持久化 —— v0.3+
- ❌ 计划模式 —— v0.3+

## 安装

需要 Python 3.11+。

```bash
# 1. 克隆 / 进入项目目录
cd BaoZiCode

# 2. 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. 安装依赖（开发模式）
pip install -e .
```

`Grep` 工具在系统装了 `rg`（ripgrep）时优先使用；没装则 fallback 到 Python `re`。

## 配置

```bash
# 1. 复制配置文件模板
cp config.example.yaml config.yaml
cp .env.example .env

# 2. 编辑 .env，填入你的 API Key
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...

# 3. （可选）编辑 config.yaml 切换后端
# backend: anthropic   # 或 openai / minimax / deepseek
# 4. （可选）编辑 config.yaml 配置工具权限
# permissions:
#   auto_allow: [Grep, Glob]
#   deny: []
#   batch_confirm: false
#   bash_locked_cwd: false
```

配置文件查找顺序：
1. `--config <path>` 命令行参数
2. 当前目录 `./config.yaml`
3. `~/.config/baozicode/config.yaml`

## 启动

```bash
baozicode            # 使用默认配置
baozicode -c my.yaml # 使用指定配置文件
python -m baozicode  # 等价
```

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示可用命令 |
| `/clear` | 清空对话历史 |
| `/exit` | 退出（Ctrl+C 同样有效） |
| `/model` | 切换到另一后端 |
| `/tools` | 列出 7 个可用工具（含风险等级） |
| `/permissions` | 显示当前生效的权限配置 |

## 工具清单

**低风险**（自动执行）：
- `Read` — 读文本文件
- `Grep` — ripgrep / Python re 搜索
- `Glob` — 文件匹配
- `WebFetch` — HTTP 抓取 + HTML 去 tags

**高风险**（弹窗确认）：
- `Write` — 整文件覆写（自动创建父目录）
- `Edit` — `old_string` 精确替换（必须唯一）
- `Bash` — shell 命令（cwd 锁项目根，`cd` 在根内可跟随）

## 项目结构

```
baozicode/
├── __main__.py             # python -m baozicode
├── cli.py                  # argparse 入口
├── app.py                  # Textual App
├── tui/
│   ├── chat_screen.py      # 主对话屏幕 + 斜杠命令 + agent loop
│   ├── tool_card.py        # ToolCallCard / ToolResultCard
│   ├── permission_modal.py # 高风险工具确认弹窗
│   ├── banner.py           # ASCII 包子
│   └── styles.tcss         # Textual 样式
├── llm/
│   ├── base.py              # LLMClient 抽象 / Message / ContentBlock / ContentDelta
│   ├── anthropic.py         # Anthropic 后端（含 tool_use 累积）
│   ├── openai.py            # OpenAICompatibleBackend 基类 + OpenAIBackend 子类
│   ├── minimax.py           # MiniMax 后端（OpenAI 兼容）
│   ├── deepseek.py          # DeepSeek 后端（OpenAI 兼容）
│   └── factory.py           # 后端选择
├── tools/                  # 7 个工具 + 注册表
│   ├── base.py              # ToolDefinition / ToolCall / ToolResult
│   ├── read.py / write.py / edit.py / bash.py
│   ├── grep.py / glob.py / webfetch.py
│   └── registry.py          # get_all_tools / execute_tool
├── conversation/
│   └── manager.py          # 多轮历史（含 tool_call / tool_result 消息）
└── config/
    ├── schema.py           # Pydantic 数据模型（含 Permissions）
    └── loader.py           # YAML + .env 加载
```

## 架构

```
TUI (Textual)
  ├── ChatScreen (agent loop)
  │     ├── 流式渲染 (Markdown.get_stream)
  │     ├── ToolCallCard / ToolResultCard
  │     └── PermissionModal
  ↓
ConversationManager  →  LLMClient (抽象)
                          ├─ AnthropicBackend   (tool_use blocks 累积)
                          └─ OpenAIBackend      (function calling)
                              ├─ MiniMaxBackend
                              └─ DeepSeekBackend
                       ↓
Tools registry  →  7 个 ToolDefinition + execute()
                       ↓
                    Config (YAML + .env)
```

依赖单向：UI 不直接 import anthropic / openai，全部走 `llm/factory.create_client(config)`。

## License

MIT
