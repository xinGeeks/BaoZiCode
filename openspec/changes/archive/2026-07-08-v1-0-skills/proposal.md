# v1.0 Skills — Proposal

## Why

BaoZiCode (v0.4 起) 在 `baozicode/prompt/sections/skills.py` 留了一个
**stub Skills section**: boot 时扫 `config.skills_dir/*.md`,把每个文件
全量塞进 `stable_system`,LLM 一启动就看到所有 Skill 的完整 SOP 文本。

这个设计有三个根本问题:

1. **token 黑洞**: 即使用户只用 1 个 Skill,所有 Skill 全文都进 system prompt。
   Skill 多了之后 system prompt 会膨胀到不可控(每个 Skill 100-500 行 SOP)。
2. **不可组合**: 全量常驻 → LLM 没有「按需加载」的决策点,要么吞下所有,
   要么一个都用不上(被淹没)。
3. **配置陈旧**: `config.skills_dir` 是 v0.4 单层路径,没有项目 / 用户 / 内置
   三级覆盖,没有 frontmatter 元信息(工具白名单 / 执行模式 / 模型选择),
   没有参数占位符替换,没有热更新。

更关键的是 v0.9 已经把 slash 命令做成了一等公民模块 (`commands/` 包),Skill
「加载后自动注册为短命令」的需求可以无缝嫁接到 `CommandRegistry`,不需要再
发明一套 dispatcher。Skills 是把 v0.9 的命令机制 + v0.4 的 SOP 概念 + v0.5
的权限白名单串起来的最后一块拼图。

v1.0 把 Skills 从 **「全量常驻的 system prompt 段落」** 升级成
**「按需激活的能力包」**:boot 时只把名字 + 一句说明注入对话,要用时再由
内置 `load_skill` 工具(或 `/skill` slash 命令)按需把完整指令加载到环境
上下文最显眼的位置,同时用工具白名单 + v0.5 权限双层防御收窄模型可选
工具,让 Skill 既能「共享当前对话」也能「开独立子对话跑完再回流摘要」。

技术约束(已锁的 7 个决策):

- **两阶段加载**: boot 注入 `name + desc` 到 system 段;激活时把完整正文
  钉到 Agent 每轮的动态 section
- **3 级存放**: 项目 `.baozicode/skills/` > 用户 `~/.config/baozicode/skills/`
  > 内置 `baozicode/skills/builtin/`(内嵌在包内,可被项目级覆盖)
- **2 种执行模式**: `shared` 共享当前对话;`independent` 开新 ConversationManager
  + 新 Agent,跑完生成摘要回流;独立模式可指定「带多少历史进去」
- **双层工具白名单**: 同时收窄 Agent.augmented_tools(LLM 看不到)+ v0.5
  `_v5_executor` permission check(LLM 强调也被拦)
- **Python 风格占位符**: `{var}` / `{name}` 替换 `load_skill(name, args=...)`
  传入的参数
- **双入口触发**: 用户 `/skill <name> {args}`(slash)+ LLM `load_skill(name)`
  tool 调(模型也能自己决定加载)
- **显式热更新**: boot 读盘缓存,`/skill reload <name>` 显式重读;不引入
  watchdog 文件监控

## What Changes

新增 1 个能力模块 + 删 1 个 v0.4 stub + 改 3 处现有契约 + 废弃 1 个 v0.4
配置字段:

- **新增 `baozicode/skills/`** — 顶层包,持 6 个子模块:
  - `schema.py` — SkillDef / SkillFrontmatter / ToolWhitelist / ExecutionMode Pydantic 模型
  - `registry.py` — Skill 扫描 + 3 级优先级合并 + frontmatter 解析 + 失败跳过
  - `loader.py` — `load_skill(name, args)` 入口,负责查找 + 解析 + 激活 + 注册 slash
  - `activation.py` — 活跃 Skill 状态 + 动态 section 渲染 + `/clear` 钩子
  - `execution.py` — shared 模式(直接追加 prompt) / independent 模式(子对话)
  - `builtin.py` — `commit` / `review` / `test` 三个样板(内嵌在包内)
- **删 `baozicode/prompt/sections/skills.py` 的全量灌入逻辑** — v0.4 那个
  「扫到空目录返回空字符串」的 stub 替换为「调 `SkillRegistry.list_visible()`
  渲染 `name + desc`」。不删文件本身(避免破坏 `sections/__init__.py` 索引),
  改实现。
- **`baozicode/prompt/builder.py`** — 加 `set_dynamic_section(name, renderer)` /
  `render_dynamic_sections(ctx)` hook,让 chat_screen 在每轮 Agent.run 前
  注入「当前激活 Skill」的动态 section。BuiltPrompt 仍 cache,但多了一层
  「每轮可变的段落」。
- **`baozicode/tools/`** — 新增 `load_skill` ToolDefinition,`tool_type=internal`
  (标记为系统级,不受 Skill 白名单约束)。`augmented_tools` 在 Skill 激活时
  按 whitelist 收窄。
- **`baozicode/agent/loop.py`** — `Agent.__init__` 多收一个 `skill_registry` /
  `skill_activation` 参数;`_v5_executor` 在权限 check 之前先做
  `skill_whitelist_check(call)`,不在白名单 → is_error(deny 而非 fallthrough)。
- **`baozicode/tui/chat_screen.py`** — on_mount 调 `SkillRegistry.scan()`
  → 把 `name + desc` 灌进 system prompt;输入框 Enter 触发
  `commands.dispatch("...", ctx, ..., on_load_skill=on_user_skill_cmd)`;
  新增 `BaoZiCodeApp` 字段 `_skill_activation` 持有当前激活集合。
- **`baozicode/config/schema.py`** — 废弃 `AppConfig.skills_dir`(v1.1 移除),
  新增 `SkillsConfig{builtin_dir: Path, project_dir: Path, user_dir: Path, scan_on_boot: bool}`。
  启动时检测到旧 `skills_dir` 字段 → WARN 提示迁移。

## Capabilities

### 新增 Capabilities

- **`skill-registry`** — SkillDef 元数据 + YAML frontmatter 解析 + 3 级
  优先级合并 + 解析失败单文件跳过(不阻断整体)
- **`skill-loader`** — `load_skill` tool + `/skill` slash 双入口,激活 Skill
  注入动态 section + 注册 slash 短命令 + 应用白名单 + 占位符替换
- **`skill-activation`** — 活跃 Skill 集合 + 动态 section 渲染 + 多个 Skill
  同时激活 + `/clear` 自动清空
- **`skill-execution`** — `shared` 模式(直接追加 prompt 进主对话) +
  `independent` 模式(子 ConversationManager + 子 Agent + 摘要回流 +
  「带多少历史」参数)
- **`skill-tool-whitelist`** — Skill 激活时收窄 `Agent.augmented_tools` +
  v0.5 `_v5_executor` 在 permission check 前做白名单过滤;`load_skill`
  标 `internal=True` 不受白名单约束

### Modified Capabilities

- **`configuration`** — 弃用 `AppConfig.skills_dir`,新增 `AppConfig.skills:
  SkillsConfig{builtin_dir, project_dir, user_dir, scan_on_boot}`;启动
  检测旧字段 → WARN
- **`prompt-modular`** — PromptBuilder 加 `set_dynamic_section` / `render_dynamic_sections`
  hook;`skills.py` section renderer 改为调 `SkillRegistry.list_visible()`
  而不是扫 `config.skills_dir`
- **`permissions`** — `_v5_executor` 在 permission check 前先做
  `skill_whitelist_check(call)`,返回 deny(layer="L0_skill_whitelist");
  Skill 未激活时该检查 noop
- **`slash-dispatcher`** — dispatcher 增加 `on_load_skill` 回调参数,
  `/skill` 命令独立 dispatch(不走 builtin 路径,直接调 `SkillLoader`)

## Impact

**代码:**

- 新增: `baozicode/skills/` 包(~7 个文件)
- 新增: `baozicode/tools/load_skill.py`(tool 实现)
- 修改: `baozicode/prompt/builder.py` + `baozicode/prompt/sections/skills.py`
- 修改: `baozicode/agent/loop.py`(`_v5_executor` + `__init__` 加参数)
- 修改: `baozicode/tui/chat_screen.py`(on_mount 加载 + `/skill` dispatch)
- 修改: `baozicode/permissions/`(`skill_whitelist_check` 函数)
- 修改: `baozicode/config/schema.py` + `config.example.yaml` + `loader.py`
- 修改: `baozicode/commands/dispatcher.py`(`/skill` 单独 path)
- 删/改: `baozicode/prompt/sections/skills.py`(实现替换)

**调用方:**

- 所有 `Agent(llm, tools, ...)` 构造需多传 `skill_registry=...` /
  `skill_activation=...`(kwarg-only,不破坏旧调用)
- `BaoZiCodeApp.__init__` 字段新增 `_skill_activation: SkillActivation | None`
- `ChatScreen.on_mount` 多一段 `SkillRegistry.scan()` 调用

**依赖:**

- 无新第三方依赖(frontmatter 解析用 `pyyaml`,已在依赖里)
- `BaoziCode.md` / memory 路径不变;Skill 路径独立(v1.0 新的 `SkillsConfig`)
