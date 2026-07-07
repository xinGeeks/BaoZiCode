# v0.4 — Modular System Prompt with Cache Strategy

**Date:** 2026-07-07
**Status:** Design (post-brainstorming, pre-plan)
**Schema:** spec-driven (OpenSpec)

## 1. 目标

把 BaoZiCode 的 system prompt 从「单字符串占位符」重构为「模块化拼装 + 缓存友好 + 行为强化」三层架构。目标：

- 稳定指令（身份 / 行为规则 / 工具描述）走可缓存通道，省钱省时间
- 环境信息和运行时补充指令走 user-role 消息通道，**不破坏 cache prefix**
- 关键行为规则在 system prompt 和工具 description 双重强化，提高 LLM 遵守率
- 配套缓存命中率验证 + 5-8 个典型场景定性对比

v0.4 只动 `LLMClient` 接口和 Agent 拼装层，**4 个后端不实现**具体 `cache_control` 行为（v0.5 再填）。这是 abstract-first 策略。

## 2. 架构总览

**新包**：`baozicode/prompt/`

```
baozicode/prompt/
├── __init__.py
├── modules.py           # 模块 dataclass 定义
├── sections/            # 每个模块一份纯函数
│   ├── identity.py
│   ├── constraints.py
│   ├── task_mode.py
│   ├── action_exec.py
│   ├── tool_usage.py
│   ├── tone_style.py
│   ├── text_output.py
│   ├── env_info.py
│   ├── custom.py
│   ├── skills.py
│   └── memory.py
├── rules.py             # RuleRegistry + 默认 7 条规则
├── reminder.py          # PlanModeReminder (节奏控制)
├── builder.py           # PromptBuilder.build() 主体
└── types.py             # BuiltPrompt / CacheBreakpoint / SystemReminder
```

**核心类型**（`prompt/types.py`）：

```python
@dataclass
class BuiltPrompt:
    stable_system: str
    dynamic_messages: list[Message]
    augmented_tools: list[ToolDefinition]
    cache_breakpoints: list[CacheBreakpoint]


@dataclass(frozen=True)
class CacheBreakpoint:
    location: Literal["system_start", "system_end", "after_tools", "before_user"]
    priority: int  # 0-100


@dataclass
class SystemReminder:
    kind: Literal["env", "plan_mode", "task_complete", "cancel"]
    content: str
    ttl: Literal["once", "static", "session"] = "static"
```

**调用点**：`Agent.__init__` 调 `PromptBuilder.build(plan_mode=self._plan_mode)` 一次，结果存 `self._prompt: BuiltPrompt`。每次 LLM 调用直接复用。

**依赖方向**：
- `prompt/` 可被 `agent/` 引用
- `prompt/` 引用 `tools/base.py`、`config/schema.py`
- `prompt/` 不引用 `llm/`（v0.4 只构建文本，不调 API）

## 3. 模块系统

**`BuiltPrompt.stable_system` 拼装顺序**（按优先级，空行分隔）：

| 序 | 模块 | 来源 | 缓存属性 | 典型 token |
|---|---|---|---|---|
| 1 | identity（身份） | hard-coded + `config.system_prompt` | 稳定 | 50-80 |
| 2 | constraints（系统约束） | hard-coded | 稳定 | 100-150 |
| 3 | task_mode（任务模式） | 根据 `plan_mode` 拼"plan"/"do" | 稳定 | 80-120 |
| 4 | action_exec（动作执行） | 通用执行约定 | 稳定 | 100-150 |
| 5 | tool_usage（工具使用） | 7 条关键规则（双重强化"全局"侧） | 稳定 | 250-400 |
| 6 | tone_style（语气风格） | 简洁 / 中文为主 | 稳定 | 50-80 |
| 7 | text_output（文本输出） | Markdown / 代码块 | 稳定 | 50-80 |
| 8 | env_info（环境信息） | cwd / OS / Python / git | **动态** | 80-120 |
| 9 | custom（自定义） | `config.custom_instructions` | 半稳定 | 0-N |
| 10 | skills（已激活） | 扫描 `~/.config/baozicode/skills/*.md` | 半稳定 | 0-N |
| 11 | memory（长期记忆） | 读 `~/.config/baozicode/memory.md` | 半稳定 | 0-N |

**关键点**：
- 1-7 进 stable_system（按优先级排序）
- 8-11 不进 stable_system，走消息通道（详见 §4）
- 9-11 可选，源为空时该段不出现
- 模块之间 `\n\n` 分隔，标题用 `## {name}` 加粗

**模块渲染契约**：每个 `sections/*.py` 导出 `def render(ctx: BuildContext) -> str`，空内容返回 `""`。`BuildContext` 至少含 `config: AppConfig` 和 `rule_registry: RuleRegistry`。

## 4. 运行时补充消息机制

**为什么用 `<system-reminder>` user-role 消息**

- 当前两个后端的 `_convert_messages` 都**默默 drop** `role="system"` 的 message
- Anthropic 的 system 是单 string，OpenAI 也是单 string，MMS/DeepSeek 不确定
- user-role 是 4 后端都最稳的通道
- system prompt 里教 LLM："用户消息可能含 `<system-reminder>` 标签块，这是系统级补充指令，不要针对它回复"

**消息格式**：

```
<system-reminder type="env" ttl="static">
## 环境信息
- cwd: G:\claudecode\BaoZiCode
- os: Windows 11
- python: 3.11.4
- git: main @ 864c040
- project: BaoZiCode (baozicode/ + tests/ + openspec/)
- 时间: 2026-07-07 14:23 (周二)
</system-reminder>
```

**关键放法**：dynamic_messages 放在 `messages[-1]` **之前**（user message 紧前），不是 `messages[0]`。这样：
- system 稳定 → 全部进 cache
- 历史 messages 不变 → 不破坏 cache
- 补充消息在尾部 → 破坏的是最近 user 那段的尾部 cache（反正快过期）

**注入点**（`Agent.run` 主循环修改）：

```python
async for delta in self._llm.stream(
    messages=self._inject_reminders(messages),
    system=self._prompt.stable_system,
    tools=self._prompt.augmented_tools,
    cache_breakpoints=self._prompt.cache_breakpoints,  # 新增
):
```

`_inject_reminders(messages)` 把 `self._pending_reminders` 拼到 `messages[-1]` 之前，返回新 list（不修改 conversation 原状态）。

**`PlanModeReminder`**（`prompt/reminder.py`）：

```python
class PlanModeReminder:
    """Plan mode 节奏控制：iteration 1 发一次，之后每 5 轮再发一次（内容相同），中间不发。"""
    FULL = 0
    INTERVAL = 5

    def should_emit(self, iteration: int) -> bool:
        if not self._plan_mode:
            return False
        if iteration == 1:        # 首轮发
            return True
        return (iteration - 1) % self.INTERVAL == 0  # 5, 10, 15, ... 重发（内容相同）
```

**Reminder 类型**（v0.4 内置 4 种）：
- `env`：每轮都发（cwd 可能变）
- `plan_mode`：受 `PlanModeReminder.should_emit` 控制
- `task_complete`：上一轮已 done 时的回灌
- `cancel`：用户取消时附在 user message 之后

## 5. 关键规则在工具描述里双重强化

**`RuleRegistry`**（`prompt/rules.py`）：

```python
@dataclass(frozen=True)
class Rule:
    id: str
    prompt_text: str              # system prompt 用（完整版,带"为什么"）
    applies_to: tuple[str, ...]   # ("Edit","Write") 或 ("*",)
    tool_prefix: str              # 工具 description 用（简短前缀,1-2 句）
```

**v0.4 默认 7 条规则**：

| id | applies_to | 简述 |
|---|---|---|
| edit_requires_read | Edit, Write | Edit/Write 前必先 Read |
| prefer_specialized_tools | Read, Grep, Glob, Edit | 优先用专用工具而非 Bash 模拟 |
| bash_timeout | Bash | Bash 必传 timeout,默认 30s |
| parallel_limit | Read, Grep, Glob, WebFetch | 单次并行无副作用工具 ≤4 |
| error_then_decide | * | 工具 is_error 时先分析再决定 |
| absolute_paths | Read, Write, Edit, Grep, Glob | path 必为绝对路径 |
| webfetch_to_file | WebFetch | 大内容写临时文件再 Read |

**注入逻辑**（`PromptBuilder._augment_tools`）：

```python
def _augment_tools(self, tools: list[ToolDefinition]) -> list[ToolDefinition]:
    out: list[ToolDefinition] = []
    for t in tools:
        prefixes = [
            r.tool_prefix
            for r in self._rules
            if r.tool_prefix and ("*" in r.applies_to or t.name in r.applies_to)
        ]
        if not prefixes:
            out.append(t)
            continue
        new_desc = "\n".join(prefixes) + "\n\n" + t.description
        out.append(replace(t, description=new_desc))  # frozen, 用 replace
    return out
```

**关键点**：
- `tool_prefix` 是简短版（1-2 句）
- `prompt_text` 是完整版（带"为什么"和"怎么做"）
- `applies_to=("*",)` 的规则**只进 system prompt，不污染工具 description**
- ToolDefinition 是 frozen → 用 `dataclasses.replace`，原 `TOOL` 常量不变

## 6. 缓存策略 + 验证

**`CacheBreakpoint`**（v0.4 接口级）：

```python
@dataclass(frozen=True)
class CacheBreakpoint:
    location: Literal["system_start", "system_end", "after_tools", "before_user"]
    priority: int  # 0-100, 数字越大越优先保留
```

**v0.4 默认 build() 产生 2 个 break point**：

```python
[
    CacheBreakpoint("system_start", priority=100),
    CacheBreakpoint("after_tools", priority=80),
]
```

**`LLMClient.stream` 接口扩展**（向后兼容）：

```python
async def stream(
    self,
    messages: list[Message],
    system: str | None = None,
    tools: list[ToolDefinition] | None = None,
    *,
    cache_breakpoints: list[CacheBreakpoint] | None = None,  # 新增 keyword-only
) -> AsyncIterator[ContentDelta]:
    ...
```

**4 后端 v0.4 行为**：
- 4 个后端都**不实现**具体 cache_control（v0.5 落地）
- `cache_breakpoints` 参数存在但被忽略
- `UsageStats.cache_*` 字段保持 0（v0.3 已有，向后兼容）

**`/status` 命令扩展**（`tui/chat_screen.py`）：
- 当前显示 session_usage（input/output 累计）
- v0.4 加 3 行：`cache_read: N tokens / cache_write: N tokens / hit rate: NN%`
- `hit rate = cache_read / (cache_read + input) * 100%`

**验证策略**：

**定量**（自动）：
1. `test_cache_breakpoints.py` — BuiltPrompt.cache_breakpoints 含 system_start + after_tools
2. `test_prompt_builder.py` — 拼装顺序、空行、模块标题齐全
3. `test_rule_injection.py` — Edit/Write description 含 `【必读】` 前缀
4. `test_plan_reminder.py` — iteration 1,5,10 发；6-9 不发
5. 现有 67 个 v0.3 测试不挂

**定性**（v0.5 跑真实 API，v0.4 留 checklist）：
- 5-8 个典型场景（详见 §8）
- v0.3 baseline / v0.4 simple / v0.4 full 三组对照
- 指标：行为符合度、步数、token 用量、cache hit rate

**目标**（v0.4 验收门槛）：
- 行为符合度提升 ≥20%（v0.4 full vs v0.3，mock LLM 测）
- 稳定 system 段**字节级一致**（多轮 stream 调用间，测试保证）
- 7 条规则中 ≥5 条在对应场景被遵守

## 7. 配置 Schema 变更

**`AppConfig` 新增字段**（`config/schema.py`）：

```python
class AppConfig(BaseModel):
    backend: BackendName
    system_prompt: str = "You are BaoZiCode, a helpful AI coding assistant."

    # ↓ v0.4 新增
    custom_instructions: str = ""
    skills_dir: Path = Path("~/.config/baozicode/skills")
    memory_path: Path = Path("~/.config/baozicode/memory.md")
    plan_reminder_interval: int = 5

    anthropic: BackendConfig
    openai: BackendConfig
    minimax: BackendConfig
    deepseek: BackendConfig
    permissions: Permissions | None = None
    agent: AgentConfig = AgentConfig()
```

**`AgentConfig` 增字段**：

```python
class AgentConfig(BaseModel):
    max_iterations: int = 20
    enable_system_reminders: bool = True
    rules: RulesConfig = RulesConfig()
```

**`RulesConfig`**：

```python
class RulesConfig(BaseModel):
    edit_requires_read: bool = True
    prefer_specialized_tools: bool = True
    bash_timeout: bool = True
    parallel_limit: bool = True
    error_then_decide: bool = True
    absolute_paths: bool = True
    webfetch_to_file: bool = True
```

**`config.example.yaml` 新增块**：

```yaml
# v0.4 新增
custom_instructions: ""
skills_dir: "~/.config/baozicode/skills"
memory_path: "~/.config/baozicode/memory.md"

agent:
  max_iterations: 20
  enable_system_reminders: true
  plan_reminder_interval: 5
  rules:
    edit_requires_read: true
    prefer_specialized_tools: true
    bash_timeout: true
    parallel_limit: true
    error_then_decide: true
    absolute_paths: true
    webfetch_to_file: true
```

**向后兼容**：
- 现有 `AppConfig` 实例化代码不需改
- YAML 缺新字段 → 走默认值
- `extra="ignore"` 兜底（CLAUDE.md 已有）

**`loader.py` 加 helper**：

```python
def _resolve_path(p: Path) -> Path:
    return Path(os.path.expanduser(str(p))).resolve()
```

`skills_dir` 和 `memory_path` load 时调一次，runtime 拿到绝对路径。

**读取逻辑**（`prompt/sections/memory.py`）：

```python
def render(ctx: BuildContext) -> str:
    path = ctx.config.memory_path
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    return f"## 长期记忆\n{text}"
```

Skills 类似：扫 `skills_dir/*.md`，v0.4 扫到空目录不报错。

## 8. 测试策略

**Level 1 单元**（~30 个 test）：
- `test_prompt_modules.py`（~10）：每个 sections 的 render，空 / 有 config / 有 rules 三种 ctx
- `test_rule_registry.py`（~6）：7 条默认规则、匹配逻辑、frozen 不变
- `test_plan_reminder.py`（~5）：iteration 节奏
- `test_prompt_builder.py`（~9）：拼装顺序、cache break points、token 上界

**Level 2 集成**（~13 个）：
- `test_agent_with_prompt.py`（~10）：mock LLM 捕获 system/tools/messages/breakpoints
- `test_llm_interface_extension.py`（~3）：cache_breakpoints 是 keyword-only，4 后端不实现也不报错

**Level 3 场景**（~8 个）：
- `test_prompt_scenarios.py`：8 个典型场景跑 mock LLM，断言 tool_call 序列

**Level 4 缓存**（~3 个）：
- `test_cache_strategy.py`：breakpoints 内容、多轮 system 字节级一致、UsageStats cache_* 字段

**目标**：
- v0.4 新增 ≥54 个 test（L1 30 + L2 13 + L3 8 + L4 3）
- 加上 v0.3 已有 67 个 → 总数 ≥120
- 全部 PASS
- `pytest tests/ -v` 退出码 0

**8 个典型场景**（定性评估用，v0.5 跑真实 API）：
1. "把这个目录所有 .py 文件第 3 行加注释" — 测 Glob + Read 批量 + Edit 串行
2. "git log 找出最近 3 个 commit 改了 README 的 hash" — 测 Bash（带 timeout）+ Grep
3. "读取 config.yaml 然后告诉我 system_prompt" — 测 Read 直接用，不绕
4. "在这个文件里搜索 TODO" — 测 Grep 优先 vs Bash+grep
5. "调用一个不存在的工具 'Foo'" — 测 unknown_tool guard
6. "用 plan 模式分析下这个仓库架构" — 测 Plan mode 注入节奏
7. "把 README 翻译成英文" — 测多步 Read + Write 配合
8. "刚才那个文件哪里出错了" — 测 LLM 能否调取历史 tool_result

## 9. 实施分块（建议）

v0.4 拆 3 个 commit，方便 review 和回滚：

1. **Commit 1 — 拼装骨架**：建 `baozicode/prompt/` 包 + 7 个 sections + 11 模块空内容 + BuiltPrompt 类型。**不动** Agent 和 LLMClient。
2. **Commit 2 — Agent 集成**：改 `Agent.__init__` 调 `PromptBuilder.build()`，改 `Agent.run` 用 `self._prompt.stable_system` 和 `_inject_reminders`。**不改** LLMClient.stream 签名。
3. **Commit 3 — 缓存接口 + 后端兼容**：`LLMClient.stream` 加 `cache_breakpoints` keyword-only 参数；4 后端忽略。`/status` 加 cache 字段。

## 10. 不在 v0.4 范围

明确**不做**（避免 scope creep）：
- 4 后端的具体 cache_control 实现（v0.5）
- Skill 系统本体（v0.4 只扫空目录）
- 长期记忆的写入机制（v0.4 只读 `memory.md`，不写）
- 真实的 prompt 调优 / A/B 测试（v0.5 跑真实 API）
- 跨会话的 prompt 复用（v0.4 单 Agent 实例内）
- 任何对 `tools/*.py` 业务代码的修改（只改 `ToolDefinition.description` 注入逻辑）

## 11. 风险与回滚

- **风险 1**：稳定 system 字符串因 config 变化而变化 → 缓存失效
  - 缓解：测试断言 `Agent` 跑多轮时 `system` 字节级一致
- **风险 2**：reminder 注入破坏 cache prefix
  - 缓解：reminder 放 `messages[-1]` 之前（不在 `messages[0]`），mock 测试验证
- **风险 3**：tool description 增广后 token 增多
  - 缓解：上限测试（≤7 条规则时 total ≤ 4000 token）
- **回滚**：每个 commit 独立可回滚（见 §9 分块）
