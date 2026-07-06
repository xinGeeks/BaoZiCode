# BaoZiCode 🥟

> 一个用 Python 开发的命令行 AI 编码助手，类似 Claude Code。

![version](https://img.shields.io/badge/version-0.1.0-blue)

## 是什么

BaoZiCode 是一个跑在终端里的多轮 AI 对话 TUI。它支持：

- 🧠 **多轮上下文** — AI 能记住之前说过的话
- ⚡ **流式响应** — 边收边渲染（代码块自动语法高亮）
- 🔌 **四后端** — Anthropic Claude / OpenAI GPT / MiniMax / DeepSeek，YAML 一键切换
- 🎨 **Textual TUI** — 现代终端界面，输入框、流式输出、ASCII 包子 banner
- 🛠️ **斜杠命令** — `/help` `/clear` `/exit` `/model`

## v0.1 范围

- ✅ 多轮对话（仅内存，不持久化）
- ✅ Anthropic + OpenAI 双后端
- ✅ YAML 配置 + `.env` 密钥
- ✅ 流式 Markdown 渲染（L4 级别：边收边高亮代码块）
- ✅ 4 个斜杠命令
- ❌ 工具调用（Read/Write/Bash/...）—— v0.2
- ❌ 对话持久化 —— v0.2
- ❌ 计划模式 / 权限系统 —— v0.3+

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

## 配置

```bash
# 1. 复制配置文件模板
cp config.example.yaml config.yaml
cp .env.example .env

# 2. 编辑 .env，填入你的 API Key
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...

# 3. （可选）编辑 config.yaml 切换后端
# backend: anthropic   # 或 openai
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
| `/model` | 切换到另一后端（Anthropic ↔ OpenAI） |

## 项目结构

```
baozicode/
├── __main__.py         # python -m baozicode
├── cli.py              # argparse 入口
├── app.py              # Textual App
├── tui/
│   ├── chat_screen.py  # 主对话屏幕 + 斜杠命令
│   ├── banner.py       # ASCII 包子
│   └── styles.tcss     # Textual 样式
├── llm/
│   ├── base.py              # LLMClient 抽象基类
│   ├── anthropic.py         # Anthropic 后端
│   ├── openai.py            # OpenAICompatibleBackend 基类 + OpenAIBackend 子类
│   ├── minimax.py           # MiniMax 后端（OpenAI 兼容）
│   ├── deepseek.py          # DeepSeek 后端（OpenAI 兼容）
│   └── factory.py           # 后端选择（4 后端 dict 查表）
├── conversation/
│   └── manager.py      # 多轮历史
└── config/
    ├── schema.py       # Pydantic 数据模型
    └── loader.py       # YAML + .env 加载
```

## 架构

```
TUI (Textual)  →  ConversationManager  →  LLMClient (抽象)
                                          ├─ AnthropicBackend
                                          └─ OpenAIBackend
                       ↓
                    Config (YAML + .env)
```

依赖单向：UI 不直接 import anthropic / openai，全部走 `llm/factory.create_client(config)`。

## License

MIT
