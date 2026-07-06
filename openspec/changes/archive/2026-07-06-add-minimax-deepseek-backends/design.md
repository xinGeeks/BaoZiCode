## Context

v0.1 已实现 2 个后端（Anthropic 和 OpenAI），架构稳定。新增需求是支持另外 2 个 OpenAI 兼容 API 提供商：**MiniMax**（项目方自研，OpenAI 兼容）和 **DeepSeek**（国产，OpenAI 兼容）。

关键观察：OpenAI、MiniMax、DeepSeek 三者的 `AsyncOpenAI` 客户端构造和流式解析逻辑**完全相同**，只是默认 `base_url` 和默认 `model` 不同。这是个典型的"用继承/类属性表达差异"的场景。

v0.1 已归档，specs 提升到基线（`openspec/specs/{interactive-tui,llm-streaming,configuration}/spec.md`）。本 change 将在该基线上做 delta 修改。

## Goals / Non-Goals

**Goals：**
- 4 个后端并存（anthropic / openai / minimax / deepseek），用户改 `backend:` 字段即可切换
- 代码层面通过继承/类属性收敛重复逻辑
- 配置文件 schema 向后兼容（旧 `config.yaml` 不会"半失效"，缺 minimax/deepseek 块时给出明确报错）
- `/model` 命令支持 4 选项列表选择
- 文档和示例同步更新

**Non-Goals：**
- 不为 MiniMax / DeepSeek 写完全独立的 client（保持 OpenAI 兼容协议假设；如未来有差异再独立）
- 不实现 backend-specific 的高级特性（如 DeepSeek 的 `deepseek-reasoner` 推理模式、MiniMax 特有的 system prompt 模板等）
- 不为新后端做自动重试 / 限流策略（继承 OpenAIBackend 的默认）
- 不改 `LLMClient` 抽象层接口
- 不改 `ContentDelta` / `Message` 数据结构
- 不引入新依赖（`openai` SDK 已经能覆盖所有 OpenAI 兼容后端）

## Decisions

### D1. 用 `OpenAICompatibleBackend` 基类 + 三个子类的层次结构

**决策**：
```python
class OpenAICompatibleBackend(LLMClient):
    """所有 OpenAI 兼容 API 的共同基类。"""
    DEFAULT_BASE_URL: ClassVar[str] = "https://api.openai.com/v1"
    DEFAULT_MODEL: ClassVar[str] = "gpt-5"
    
    def __init__(self, api_key, model=None, base_url=None):
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or self.DEFAULT_BASE_URL,
        )
        self._model = model or self.DEFAULT_MODEL
    
    async def stream(self, messages, system=None) -> AsyncIterator[ContentDelta]:
        # 与原 OpenAIBackend 完全相同的流式逻辑
        ...

class OpenAIBackend(OpenAICompatibleBackend):
    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-5"

class MiniMaxBackend(OpenAICompatibleBackend):
    DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"  # 占位
    DEFAULT_MODEL = "MiniMax-M3"

class DeepSeekBackend(OpenAICompatibleBackend):
    DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
    DEFAULT_MODEL = "deepseek-chat"
```

**理由**：
- 三个子类的实现**完全是空的**——它们只通过类属性声明默认值
- 流式逻辑只写一份，bug 修复只需改基类
- 未来要加新的 OpenAI 兼容后端（如 Moonshot、Together）只需要新建一个 4 行的子类

**备选**：
- **方案 B：参数化 OpenAIBackend**（`OpenAIBackend(LLMClient)`，构造函数接收 `default_base_url` / `default_model`，工厂传参）
  - 优点：不需要新文件
  - 缺点：工厂代码会塞很多 if/elif；类属性方案更 Pythonic、扩展性更好
- **方案 C：完全独立**（`MiniMaxBackend` 复制 `OpenAIBackend` 全部代码）
  - 优点：每个后端可独立定制
  - 缺点：代码重复，未来加第 4、第 5 个后端会爆

**结论**：基类 + 子类，零代码重复。

---

### D2. AppConfig 字段扩展而非可选化

**决策**：
```python
class AppConfig(BaseModel):
    backend: Literal["anthropic", "openai", "minimax", "deepseek"]
    system_prompt: str = "..."
    anthropic: BackendConfig
    openai: BackendConfig
    minimax: BackendConfig       # 新增
    deepseek: BackendConfig      # 新增
```

**理由**：
- 与 v0.1 一致——`anthropic` 和 `openai` 块一直是必填；新加的 `minimax` 和 `deepseek` 也必填，保持规则的统一
- 必填让"切换后端"是单字段编辑（改 `backend:` 即可），不需要再去配另一块
- 如果允许某块缺失，需要复杂逻辑（"如果 backend=minimax 但 minimax 块缺失则报错，否则忽略"），不如直接全必填

**取舍**：
- **旧 config.yaml 用户**：需要补上 `minimax:` 和 `deepseek:` 两个块才能启动
- **错误信息**：Pydantic 会明确指出缺哪些字段，用户知道补什么

**备选**：
- **方案 B：把 4 块都设为可选**（`minimax: BackendConfig | None = None`）
  - 缺点：旧用户无痛升级但新用户必须懂得"想用 minimax 就得配上 minimax 块"——心智负担更重
  - 缺点：工厂代码要处理"backend 选了但块没配"的情况

**结论**：全必填，简单、明确。

---

### D3. switch_backend(target: str) 接受目标后端名

**决策**：
```python
# v0.1
def switch_backend(self) -> None:
    self.config = self.config.switched()  # 硬编码"另一个"
    self.llm_client = create_client(self.config)

# v0.1.x
def switch_backend(self, target: BackendName) -> None:
    if target == self.config.backend:
        return  # 已经是目标
    new_cfg = self.config.model_copy(update={"backend": target})
    self.config = new_cfg
    self.llm_client = create_client(new_cfg)
```

**理由**：
- 二选一时代的"切到另一个"假设在 4 后端下不成立
- 接受显式 target 让调用方（chat_screen）控制逻辑，App 内部只是无脑执行

**API 变化**：
- 旧：`app.switch_backend()` 无参
- 新：`app.switch_backend("minimax")` 带参
- 影响范围：仅 `chat_screen.py._switch_model` 一处调用

**结论**：签名清晰、单一职责。

---

### D4. /model 改为 4 选项列表选择器

**决策**：保留 `ModelSelectScreen`（ModalScreen），但弹窗内容从"两个按钮"改为"4 个按钮列表 + 当前后端高亮"。

**实现要点**：
```python
class ModelSelectScreen(ModalScreen[str | None]):
    def __init__(self, current: str, options: list[tuple[str, str]]) -> None:
        # options = [(backend_name, model_label), ...]
    
    def compose(self) -> ComposeResult:
        for backend, model in self.options:
            label = f"{backend} · {model}"
            if backend == self.current:
                label += " (当前)"
            yield Button(label, id=f"select-{backend}")
        yield Button("取消", id="cancel")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            backend = event.button.id.removeprefix("select-")
            self.dismiss(backend)
```

**理由**：
- 4 选项用 4 个按钮 + 一个"取消"，最多 5 行 UI，简洁
- 当前后端加"(当前)"标识，避免用户重复点
- 复用现有的 ModalScreen 模式，符合 v0.1 的代码风格

**备选**：
- **方案 B：单选 ListView**（用 Textual 的 `ListView` + `ListItem`）
  - 优点：可滚动，适合 4+ 选项
  - 缺点：当前只有 4 个，4 按钮够用；ListView 还要处理选中态样式
- **方案 C：保留二选一（仅切到"另一个"）**
  - 缺点：要从 anthropic 切到 deepseek 必须先经过 openai 或 minimax，体验差

**结论**：4 按钮列表，够用、好实现。

---

### D5. 占位的 MiniMax base_url 用 `https://api.minimaxi.com/v1`

**决策**：默认 `https://api.minimaxi.com/v1`，模型 `MiniMax-M3`，均可在 YAML 中覆盖。

**理由**：
- MiniMax 是项目方自研模型，base_url 没有公开标准；先用占位
- 用户的 `.env` 里有 `MINIMAX_API_KEY` 的话，猜测的 base URL 错了也能在首次调用时报错
- 占位 URL 与 OpenAI/DeepSeek 命名风格一致，方便记忆

**取舍**：
- 首次调用大概率会报 connection error（URL 不对）
- 用户需要查阅 MiniMax 官方文档，把 base_url 改对

**结论**：占位是务实的选择；YAML 可覆盖是兜底。

---

## Risks / Trade-offs

- **R1：旧 config.yaml 启动会失败**（缺 minimax / deepseek 块）→ Mitigation：README 升级提示 + 友好报错（Pydantic 列出缺哪些字段）
- **R2：MiniMax 默认 base_url 猜错** → Mitigation：用户改 YAML 即可；错误信息会显示实际访问的 URL
- **R3：`switch_backend(target)` 是 breaking change（无参 → 有参）** → Mitigation：调用方只有 chat_screen.py 一处，迁移成本低
- **R4：4 按钮弹窗在小终端上可能被截断** → Mitigation：按钮 label 控制在 30 字符内，4 按钮 + 取消 = 5 行；常见终端 80×24 足够
- **R5：MiniMax/DeepSeek 未来若偏离 OpenAI 协议** → Mitigation：基类 `OpenAICompatibleBackend` 已是单独类，可以独立 override `stream()` 方法不影响其他后端

## Migration Plan

无（v0.1 → v0.1.x 是直接升级）：

1. 升级代码
2. 旧 config.yaml 用户需要在配置文件中补 `minimax:` 和 `deepseek:` 两个块（最小骨架）
3. `/model` 重新支持 4 选项
4. 启动方式、其它斜杠命令不变

## Open Questions

无。所有关键决策已与用户对齐。
