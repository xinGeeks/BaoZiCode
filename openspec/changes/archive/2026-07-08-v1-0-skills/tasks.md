# v1.0 Skills — Tasks

## Phase 1 — Schema + Frontmatter 解析

实现 `baozicode/skills/schema.py` 的 Pydantic 模型 + frontmatter 解析。
不涉及文件 IO,纯内存。

**验收**:

- `SkillFrontmatter` Pydantic 模型覆盖 7 字段(name / description / mode /
  allowed_tools / history_bubbles / model / hidden)
- `parse_frontmatter(md_text: str) -> (SkillFrontmatter, body: str)` 抽
  `---` 包围的 YAML,留 body 原样
- 解析失败 → 抛 `ValueError`,带文件路径信息(让上层 caller 决定怎么处理)
- 字段类型错 / 必填缺失 → `ValueError`
- `mode` 不在 `{"shared", "independent"}` → `ValueError`
- `history_bubbles > 50` → `ValueError`(v1.0 硬上限)
- `name` 不匹配 `^[a-z][a-z0-9-]*$` → `ValueError`
- 15 个单测覆盖每个失败模式 + 1 个完整 happy path

## Phase 2 — Registry 扫描 + 3 级优先级合并

实现 `baozicode/skills/registry.py` 的 `SkillRegistry.scan(builtin_dir,
user_dir, project_dir, tool_registry) -> SkillRegistry`。

**验收**:

- 3 个目录路径全 None / 不存在 → 空 registry(无 WARN)
- 目录存在但无 Skill 子目录 → 空 registry(无 WARN)
- 解析失败的文件 → 跳过,WARN 一行(file path + reason)
- 全部失败 → 单行汇总 WARN(不是每文件一行)
- 项目 > 用户 > 内置 覆盖:同名 Skill 项目级完全替换
- `allowed-tools` 引用不存在的 tool → `SystemExit` (boot panic)
- `list_visible()` 按 name 字母序排,排除 `hidden=True`
- `reload(name)` 重新读盘,失败保留旧版本
- 12 个单测覆盖每个失败/成功路径

## Phase 3 — Builtin 3 个样板 Skill

在 `baozicode/skills/builtin/{commit,review,test}/SKILL.md` 写 3 个样板。
每个 < 50 行 SOP,带 `# TODO: 项目可覆盖此文件,定制你的 commit 格式`
引导注释。

**验收**:

- `commit` — 根据当前 `git diff --staged` 生成符合 conventional commits 的
  message,只允许 Write(写文件)+ Bash(调 git)
- `review` — 审查最近 N 个 commit,只允许 Read + Grep + Bash(调 git log)
- `test` — 跑 pytest 收集失败,只允许 Bash
- 3 个样板在没用户 Skill 时也能在 `/skill list` 看到
- 项目放 `.baozicode/skills/commit/SKILL.md` 即可覆盖(测试覆盖)
- 8 个单测覆盖样板存在性 + frontmatter 字段正确性

## Phase 4 — Activation 状态 + 动态 section 渲染

实现 `baozicode/skills/activation.py` 的 `SkillActivation` 类 + 动态
section 渲染。

**验收**:

- `activate(name, body, ...)` 幂等(同 body 第二次 noop,不同 body 替换)
- `deactivate(name)` 移除 + 注销对应 slash 命令
- `clear()` 清空 + 注销所有动态 slash 命令
- `render_active_section()` 空 active → 返回 `""`
- 非空 → 返回 `<system-reminder type="active_skills" sticky="true">` 包裹
  的 Markdown,含每个 Skill 的模式/白名单/正文
- `is_active(name) -> bool`
- 10 个单测覆盖 activate/deactivate/clear/render 各路径

## Phase 5 — Loader 双入口(load_skill tool + /skill slash)

实现 `baozicode/skills/loader.py` 的 `SkillLoader.load_skill(name, args,
source="slash"|"tool")` + 暴露两个入口。

**验收**:

- `load_skill` ToolDefinition 注册到 `ToolRegistry`,`tool_type="internal"`
- `/skill` slash 命令注册到 v0.9 `CommandRegistry`,args 解析 `--key=value`
- 两个入口都调 `SkillLoader.load_skill` 同一函数
- 占位符替换:`{var}` → args[var];未匹配的 `{var}` 保留字面量;`{{literal}}`
  → `{literal}`(Markdown 转义)
- 加载成功后:`SkillActivation.activate(...)` + `CommandRegistry.register(
  CommandDef(name=name, ...))`
- 失败:`is_error=True` 错误描述
- 12 个单测覆盖双入口 + 占位符 + 错误处理

## Phase 6 — 工具白名单双层防御

实现 `skill_whitelist_check(call) -> PermissionDecision`,在 v0.5
`_v5_executor` 现有 `permissions.check` 之前调。

**验收**:

- v0.5 `_v5_executor` 改造:在 `permissions.check` 之前调
  `skill_whitelist_check`
- L0 收窄:Agent.augmented_tools 按 active Skills 的 `allowed_tools` ∩ 取
  交集(无 active Skills = 不过滤)
- L5' 防御:即使 LLM 调了白名单外工具 → deny with
  `layer="L0_skill_whitelist"`
- `load_skill` (tool_type="internal") 跳过白名单检查
- 11 个单测覆盖 5 个场景(单/多/无/白名单外/内部 tool)

## Phase 7 — execution 两种模式

实现 `baozicode/skills/execution.py` 的 `run_skill(name, args, ctx) -> None`。
shared 模式追加 user 消息;independent 模式起子 Agent。

**验收**:

- shared 模式:占位符替换后 body 通过 `ctx.send_to_agent(body)` 送进主对话
- independent 模式:
  1. snapshot 主对话最后 N 轮(N=history_bubbles,clamp 到 [0, 50])
  2. 新 ConversationManager + 新 Agent(同 backend / 同 registry / 同 activation)
  3. 注入 snapshot + Skill body
  4. 跑 Agent.run() 到底
  5. 调 LLM 生成 3 段摘要
  6. `ctx.send_to_agent(summary)`
  7. 销毁子 Agent / 子 ConversationManager
- `model` 覆盖字段生效
- 子 Agent 的 augmented_tools 排除 `load_skill`
- 14 个单测覆盖两种模式 + history_bubbles clamping + model override

## Phase 8 — ChatScreen 接入 + dynamic section 注入

修改 `baozicode/tui/chat_screen.py`:
- on_mount 时调 `SkillRegistry.scan(...)` + 把名字注入 system prompt
- on_input_submitted 经 dispatcher 路由 `/skill`
- 每轮 Agent.run 前调 `SkillActivation.render_active_section()` → 注入
  `<system-reminder type="active_skills" sticky="true">` 到 messages[-1] 之前

**验收**:

- chat_screen 持有 `app._skill_activation: SkillActivation`
- on_mount 调 `skill_registry.scan()` 后 `app._skill_registry` 就绪
- on_input_changed 不变(走 v0.9 completor)
- on_input_submitted 路径:slash → dispatch(包括 `/skill`);非 slash → Agent
- Agent.run 前 `_inject_dynamic_active_skills(messages)` 在 messages[-1] 之前
  插入 active_skills reminder
- `/clear` 触发 `_skill_activation.clear()`(已有 `/clear` 实现的 hook)
- 9 个单测覆盖 dispatch 路由 + reminder 注入 + /clear 联动

## Phase 9 — Config schema + v0.4 兼容

修改 `baozicode/config/schema.py`:
- 新增 `SkillsConfig{builtin_dir, project_dir, user_dir, scan_on_boot,
  summary_model}`
- `AppConfig.skills: SkillsConfig = SkillsConfig()`
- 启动检测 `AppConfig.skills_dir` 字段(如果用户配了)→ WARN 提示迁移

**验收**:

- 默认 `SkillsConfig{builtin_dir=baozicode/skills/builtin, user_dir=~/.config/baozicode/skills, project_dir=<project>/.baozicode/skills, scan_on_boot=True, summary_model="claude-sonnet-4-6"}`
- 旧 `skills_dir` 字段被检测时 → stderr 一行 `WARN: skills_dir is deprecated, use SkillsConfig.user_dir`
- 4 个 schema 单测 + 2 个 loader 集成测试

## Phase 10 — 集成测试 + 删除 v0.4 stub

新增 `tests/integration/test_v10_skills.py`:
- boot 完整流程:registry 扫描 + system prompt 注入 + 3 个 builtin 可见
- 完整 load + execute 流程:LLM 调 load_skill → 激活 → 工具白名单生效 →
  shared 模式追加 / independent 模式子 Agent
- `/clear` 清空激活 + 注销 slash
- 切换 Skill 工具白名单:LLM 看不到白名单外工具

**验收**:

- 12 个端到端测试覆盖完整流程
- 全量回归:之前所有 808 个测试仍然绿

## Phase 11 — Docs

- CHANGELOG 加 v1.0 entry(新增 / 修改 / 废弃 / 升级路径)
- docs/migrations/v0.9-to-v1.0.md:`skills_dir` 字段迁移 + Skill 加载用法
- README 当前版本升级 v1.0 + 新章节「Skills」+ Skill 用法示例
- config.example.yaml:`skills:` 块 + 字段注释 + v0.4 `skills_dir` 弃用说明

**验收**:

- migration doc 完整覆盖 `skills_dir` → `SkillsConfig` 字段映射
- README 加 Skill 工作流图 + 3 个样板用法

## Phase 12 — Archive

- `git status` 干净
- `git add -A && git commit -m "feat(v1.0): Skills — two-stage loading + 2 execution modes + tool whitelist"`
- `mv openspec/changes/v1-0-skills openspec/changes/archive/`
- `openspec list` 验证

**验收**:

- HEAD 在 release commit
- v1-0 change 已移入 archive

---

**总测试目标**:v0.9 → v1.0 测试数 808 → ~970 (+162 个新测试)
