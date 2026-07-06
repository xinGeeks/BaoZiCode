## Why

v0.1 已支持 Anthropic 和 OpenAI 两个后端。但用户的实际使用场景里会用到 **MiniMax**（项目方自研模型）和 **DeepSeek**（国内广泛使用的国产模型）——这两个后端都提供 OpenAI 兼容的 API，集成成本很低，但当前架构没有为它们显式建模。

把这两个后端加进来，让用户改一行 YAML 就能在 4 个后端之间切换，无需手填第三方 base URL。

## What Changes

- 新增 `OpenAICompatibleBackend` 抽象基类，把 OpenAI、MiniMax、DeepSeek 三个后端的共同逻辑（`AsyncOpenAI` 客户端构造 + 流式解析）收敛进去
- 新增 `MiniMaxBackend(OpenAICompatibleBackend)`：默认 base_url `https://api.minimaxi.com/v1`、默认模型 `MiniMax-M3`（占位，YAML 可改）
- 新增 `DeepSeekBackend(OpenAICompatibleBackend)`：默认 base_url `https://api.deepseek.com/v1`、默认模型 `deepseek-chat`
- `OpenAIBackend` 改造为 `OpenAICompatibleBackend` 的子类，行为不变（base_url `https://api.openai.com/v1`、model `gpt-5`）
- `AppConfig` 的 `backend` 字段从 `Literal["anthropic", "openai"]` 扩展为 `Literal["anthropic", "openai", "minimax", "deepseek"]`
- `AppConfig` 增加 `minimax: BackendConfig` 和 `deepseek: BackendConfig` 两个必填块
- 工厂 `create_client(config)` 扩展为支持 4 个 backend
- `App.switch_backend(target: str)` 改为接受目标 backend 名（而不是硬编码"切换到另一个"）
- `ChatScreen._switch_model` 改为弹出 4 选项的列表选择器（高亮当前），让用户在任何后端之间自由切换
- 更新 `config.example.yaml`、`.env.example`、README 文档
- **BREAKING**：`AppConfig.backend` 字段合法值集合扩大（新增 `minimax` 和 `deepseek`），但旧值 `anthropic` 和 `openai` 仍合法 → **非 breaking**。`switch_backend()` 方法签名变更（无参 → 接受 target 字符串）→ 调用方只在 chat_screen.py 内部，影响范围小

## Capabilities

### New Capabilities

无（这两个后端归属于已有的 `llm-streaming` 和 `configuration` 能力，无需新建 spec）

### Modified Capabilities

- `llm-streaming`: 工厂现在支持 4 个后端（`anthropic` / `openai` / `minimax` / `deepseek`）；`OpenAIBackend` 行为不变但继承自新的 `OpenAICompatibleBackend` 基类
- `configuration`: `AppConfig.backend` 字段支持 `minimax` 和 `deepseek`；`AppConfig` 必填块从 2 个变 4 个（新增 `minimax` 和 `deepseek`）
- `interactive-tui`: `/model` 命令从"在两个后端间二选一"改为"在 4 个后端间列表选择"

## Impact

**新增/修改的代码：**
- `baozicode/llm/openai.py` — 重构为 `OpenAICompatibleBackend` 基类 + `OpenAIBackend` 子类
- `baozicode/llm/MiniMax.py` — 新文件（MiniMax 后端）
- `baozicode/llm/deepseek.py` — 新文件（DeepSeek 后端）
- `baozicode/llm/factory.py` — 支持 4 后端
- `baozicode/config/schema.py` — 扩展 `BackendName`、增加两块
- `baozicode/app.py` — `switch_backend` 签名变更
- `baozicode/tui/chat_screen.py` — `_switch_model` 改用 4 选项列表选择器
- `config.example.yaml` / `.env.example` — 加上 minimax 和 deepseek 块

**对调用方的影响：**
- `App.switch_backend()` 内部使用方只 chat_screen.py 一处，需要同步更新
- 既有 `config.yaml` 用户：需要补 `minimax:` 和 `deepseek:` 两个块，否则 Pydantic 校验失败
- 公共 CLI 行为不变（`baozicode` 启动方式、斜杠命令都不变）
