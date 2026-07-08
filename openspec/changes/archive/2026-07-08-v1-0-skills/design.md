# Design — v1.0 Skills

## Context

v0.4 PromptBuilder 留下 Skills section stub (`baozicode/prompt/sections/skills.py:13-30`):
boot 扫 `config.skills_dir` 全量灌入 system prompt。v0.5-v0.9 期间 Skills
本体未实现,但留下了三块可复用的基础设施:

- v0.5 `_v5_executor` 的 `permissions.check(call)` 入口
- v0.7 `ConversationManager` 的 `add_turn` / `add_tool_result` 子对话隔离能力
- v0.9 `CommandRegistry.register(CommandDef(...))` 的运行时挂载机制

v1.0 Skills 把这三块串起来,实现「按需激活的能力包」+「共享/独立双执行模式」
+「双层工具白名单」。本设计文档聚焦 7 个关键决策和未决风险。

完整设计见本文件各 Decision 段。

## Goals / Non-Goals

**Goals:**

- 把 Skill 从「全量常驻 system prompt 段落」改成「两阶段加载 + 按需激活」
- 支持 `shared` / `independent` 两种执行模式,独立模式可指定带多少历史
- 工具白名单双层防御:LLM 看不到(收窄 `augmented_tools`)+ 调了也被拦
  (v0.5 `_v5_executor` permission check 之前加 `skill_whitelist_check`)
- Skill 加载后自动注册为 `/<name>` slash 短命令(`/clear` 时一并清空)
- 3 个样板 Skill 内嵌在包里:`commit` / `review` / `test`(项目级可覆盖)
- 占位符 `{var}` Python 风格替换,跟 v0.9 `commands.review_prompt` 模板风格一致

**Non-Goals:**

- Skill 间的依赖/链式加载(一个 Skill require 另一个 Skill)v1.0 不支持
- Skill 自身的 `apply_skill` / `unapply_skill` 命令 v1.0 不暴露(走 `/skill
  load/unload` 即可)
- watchdog 文件监控热更新(v1.0 走显式 `/skill reload`)
- 跨 session 的 Skill 状态持久化(v1.0 Skill 激活跟当前对话绑定,/clear
  清空,退出即失;v1.1 再考虑)
- Skill 编写辅助工具 / 校验器(只是 SOP Markdown + frontmatter,LLM 自己写)

## Decisions

### D1: 3 级存放 + 项目级覆盖

**路径**:

- 内置:`baozicode/skills/builtin/{commit,review,test}/SKILL.md`(包内,随包发)
- 用户:`~/.config/baozicode/skills/<name>/SKILL.md`(跟人走,所有项目共享)
- 项目:`<project>/.baozicode/skills/<name>/SKILL.md`(跟项目走,只本项目)

**优先级**:项目 > 用户 > 内置。同名 Skill 项目级文件**完全覆盖**用户级和内置
(不合并 frontmatter),保证「项目想换 commit 模板」只需放同名目录,不用 fork。

**为什么内置内嵌在包内**:开箱即用,装包即有 3 个样板,不依赖用户手 init。
项目级 `.baozicode/skills/commit/SKILL.md` 即可覆盖,无需去碰包内文件。

**考虑过的方案 A:内置也放 `~/.config/baozicode/skills/`**:首次启动 copy 过去。
放弃理由:升级包不跟进 + 用户误改后无法回滚。

### D2: YAML frontmatter 严格 schema

```yaml
---
name: review                          # 必填,唯一,lowercase a-z + `-`
description: 审查自 {since} 起的改动   # 必填,一句话
allowed-tools: [Read, Grep, Glob]      # 可选,默认 = 当前会话所有工具
mode: shared                          # 可选,默认 shared;可选 independent
history-bubbles: 5                     # mode=independent 时用,默认 0
model: claude-sonnet-4-6               # 可选,默认继承当前 Agent 的 model
---
# Skill 正文:SOP 指令

请审查自 {since} 以来的所有改动。{var_extra}
...
```

Pydantic 严格校验:必填字段缺失 / 字段类型错 / `allowed-tools` 引用不存在的
工具名 → 整个 Skill 文件解析失败(不阻断其他 Skill)。`mode` 必须是
`shared` / `independent` 之一,否则失败。

**为什么 `allowed-tools` 缺失 = 不收窄**:默认行为 = 「不影响当前可用工具集」
(向后兼容默认 Skill 行为),不强制每个 Skill 都要列白名单。

### D3: 两阶段加载 = boot 可见 + 激活后钉

**阶段 1 — boot 注入(`skill-registry` spec)**:

```python
# baozicode/prompt/sections/skills.py(改实现)
def render(ctx):
    skills = ctx.skill_registry.list_visible()  # [(name, desc), ...]
    return "## 可用 Skill\n\n" + "\n".join(f"- /{n}: {d}" for n, d in skills)
```

LLM 启动时看到名字 + 一句说明,需要用就 `load_skill(name)`。

**阶段 2 — 激活后钉(`skill-activation` spec)**:

`SkillActivation` 持有当前激活集合(可多个)。chat_screen 在每轮
`Agent.run` 前调 `SkillActivation.render_active()` → 生成一段
`<system-reminder type="active_skills" sticky="true">` 块,内容是所有
激活 Skill 的完整正文拼起来的。注入位置:`messages[-1]` 之前(同 v0.4
`<system-reminder>` 注入点)。

**为什么走 system-reminder 而不是 BuiltPrompt**:

- BuiltPrompt 是 boot 时构造并 cache;Skill 激活/停用是运行时事件,
  每次激活都 rebuild = cache miss(Anthropic cache key 失效)
- `system-reminder` 走 user-role 消息,4 后端都支持,前缀破坏范围只到
  最近 user 那段
- 现有 `_inject_reminders(messages, iteration)` 注入点直接复用

**多个 Skill 同时激活**:Activation 是个 dict,按 Skill 名字去重;同一 Skill
加载多次 = noop(已激活)。显示时按加载顺序拼接。

**为什么多个 Skill 同时激活**:用户场景是「`/skill review` + `/skill test`」
同时跑两个 SOP。v1.0 不引入 Skill 冲突检测(假设 Skill 之间不互斥)。

### D4: 占位符 {var} 替换

**语法**:`{var}` / `{var_name}`。`{}` 内的字符串作为 key,运行时从
`load_skill(name, args={"var": "value", ...})` 字典里查替换。

**没匹配到的占位符**:
- 保留字面量(类似 `str.format` 在缺 key 时抛错,v1.0 改为保留 `{var}` 不替换)
- 这样 LLM 写 SOP 时不熟悉替换规则,留 `{unknown}` 也能用

**示例**:

```yaml
# SKILL.md frontmatter + body
description: 审查自 {since} 起的改动
mode: shared
---
请审查自 {since} 以来的所有改动,关注 {focus_area}。
```

用户调 `/skill review --since="5 轮前" --focus_area="权限层"` →
LLM 调用 `load_skill(name="review", args={"since": "5 轮前", "focus_area": "权限层"})`
→ 替换后 body: `请审查自 5 轮前 以来的所有改动,关注 权限层。`

**为什么 Python 风格而不是 shell 风格**:与 v0.9 `commands.review_prompt`
模板(`{since}` 占位符)一致,LLM 写 SOP 习惯用花括号,Markdown 中花括号
冲突可转义为 `{{` / `}}`。

### D5: 双层工具白名单(L0_skill_whitelist + v0.5 L3)

**两层叠加**:

1. **L0(新增)**: Skill 激活后,`Agent.augmented_tools` 只保留
   `Skill.allowed-tools` ∩ `当前所有工具`。LLM 看不到白名单外的工具,
   自然不会调。
2. **v0.5 L3(改造)**: `_v5_executor` 在 `permissions.check(call)` 之前
   调 `skill_whitelist_check(call)`。如果当前 active Skill 有
   `allowed-tools` 且 call.tool 不在白名单 → `decision=deny`,
   `layer="L0_skill_whitelist"`(新的 layer,排在 L1 之前)。

**为什么双层而不是单层**:

- L0 收窄 augmented_tools 防止 LLM 选错(节省 token + 减少选错概率)
- v0.5 L3 是兜底:即使 LLM 强行调(hallucination / 不当 augmented),也拦下
- 两层都拦 = 零 LLM 误调空间

**例外**:`load_skill` tool 标 `tool_type="internal"`,permission check 时
**跳过 L0 检查**(否则 Skill 自身调自己会被拒)。`internal` 标记也加到
v0.6 MCP 工具已有的 `tool_type` 字段(已有 `internal` 概念,只是 v0.6
未启用)。

### D6: shared / independent 两种执行模式

**shared**(默认): Skill body 替换占位符后,**作为用户消息追加到当前
ConversationManager**。LLM 接着 Skill 指令执行,所有 tool_call / text
response 都进主历史。`/clear` 时跟主历史一起清。

**independent**: 激活 Skill 时:

1. 调 `ConversationManager.snapshot_last_n(n=history-bubbles)`(默认 0 = 空)
2. 新建 `ConversationManager` + 新 `Agent`(共享 backend,共享 prompt builder
   + skill_activation 状态)
3. 替换占位符后的 body 作为 user 消息送进子 Agent
4. 子 Agent 跑完,收集完整 `text` + `tool_use` + `tool_result`,调
   `LLM.summarize(stream)` 生成 3 段摘要(`## 任务执行` / `## 关键发现` /
   `## 后续建议`)
5. 摘要作为 user-role 消息回流到主 ConversationManager(`ctx.send_to_agent`)
6. 子 Agent 销毁

**history-bubbles**:子 Agent 启动时「主对话最近的 N 轮」作为静态 context
注入 messages[0] 之前(类似 system prompt 但走 user-role),让子 Agent
知道「主对话在做什么」。

**为什么两种模式都要**:

- shared: 简单 SOP,跟主对话强相关(如 `/skill commit` 改当前代码)
- independent: 重 SOP,跟主对话关系弱(如 `/skill test` 跑全套测试,
  主对话在做其他事)

### D7: 显式热更新 + slash + tool 双入口

**双入口**:

- **用户**: `/skill <name> {args}`(slash) + `/skill reload <name>` +
  `/skill unload <name>`(走 v0.9 `CommandRegistry`,复用 dispatch)
- **LLM**: `load_skill(name: str, args: dict | None = None) -> dict` tool
  (ToolDefinition,标 `internal=True`)

两条入口都调 `SkillLoader.load_skill(name, args, source="slash"|"tool")` 同一
底层函数,行为一致。

**热更新**:`SkillRegistry` boot 时扫 3 级目录缓存到 `dict[name, SkillDef]`。
`/skill reload <name>` 重新解析该 Skill 的 frontmatter + body 并替换缓存。
**不引入 watchdog**(避免多一个依赖 + 跨平台兼容性)。

**`/clear` 联动**:`BaoZiCodeApp._skill_activation.clear()` + slash registry
里 Skill 动态注册的 `/<name>` 命令也 unregister。`commands/dispatcher.py`
的 `/clear` 实现调这个 hook。

## Risks / Trade-offs

**R1: dynamic section 重渲染每轮跑 → token 开销**

- 每个激活 Skill 的 body 拼起来进 reminder,每轮 LLM 看到的内容都包含
  Skill 全文(SOP 通常 200-800 行)
- 缓解:占位符替换在 load_skill 时一次性完成(不是每轮);reminder 块走
  user-role 消息,前缀破坏范围只到最近 user 那段(cache 仍有效)
- 检测:`/status` 命令显示 `active_skills: N 个 / 总 NNNN tokens` 指标

**R2: Skill 解析失败影响范围**

- 单个 Skill 解析失败(YAML 错 / 必填字段缺 / 工具名引用不存在)→ 跳过该 Skill,
  warn stderr 一行,其他 Skill 继续加载
- 不阻断 boot,不阻断其他 Skill
- 缓解:`SkillRegistry.scan()` 收集 `(name, SkillDef | error_msg)`,chat_screen
  boot 时把错误汇总到一行 `WARN: 3 skills failed to load, run /skill list --errors`

**R3: independent 模式调 LLM 摘要 → 额外成本**

- 每次 Skill 跑完调 LLM 生成摘要(3 段),即使 Skill 本身没产生多少内容
- 缓解:摘要 LLM 调用用小 model(`config.skills.summary_model`,默认 sonnet);
  子 Agent 跑完如果只产生 < 100 token text,跳过摘要直接回流原文

**R4: tool_type="internal" 是个新概念**

- v0.6 引入 `tool_type` 字段但未实际启用,v1.0 真正利用
- 风险:旧的 7 工具 + v0.6 MCP 工具的 `tool_type` 默认值需要明确(默认
  `"user"`,只有 `load_skill` 标 `"internal"`)
- 缓解:`ToolDefinition.__post_init__` 默认 `tool_type="user"`,显式覆盖

**R5: `/clear` 清 Skill vs 持久化激活的冲突**

- v1.0 决定 `/clear` 清空激活 Skill(用户期望)
- 但用户重发 Skill 指令会重复加载 → 每次重新解析占位符 + 注册 slash
- 缓解:Skill 加载是幂等的(同 name 重复加载 = 更新 body 不重注册 slash);
  `/skill list --active` 始终显示当前激活集合

**R6: 用户在 SKILL.md 里写 `{var}` 跟 Markdown 表格冲突**

- Markdown 表格里 `{...}` 会被误识为占位符
- 缓解:占位符替换正则只匹配**单独一行内**的 `{var}`(行内不跨段),Markdown
  表格里 `{...}` 在 `|` 包围中通常作为代码片段,加上 `\{` 转义
- 未完全解决:v1.0 先 docs 警告「正文里 `{var}` 必须独占 token,不要嵌入
  表格行内」

**R7: 跨 session Skill 激活不持久化 vs v0.8 会话存档**

- v0.8 JSONL 会话存档保存对话内容;v1.0 Skill 激活状态**不**进 JSONL
- 重启后 Skill 激活集合清空
- 缓解:这是有意的(用户重启动 = 重新选择能力);v1.1 再考虑持久化

**R8: 3 个样板 Skill 的「正确实现」需要 domain 知识**

- `commit` / `review` / `test` 三个样板写什么 SOP 决定用户体验
- v1.0 写 3 个最小可用版本(分别 ~30 行 SOP),用户项目级覆盖即可
- 风险:样板太简陋,用户觉得「还不如手写」;太复杂,反而不灵活
- 缓解:样板走「最小可用 + 注释引导覆盖」,每个样板 < 50 行 SOP,留
  `# TODO: 项目可覆盖此文件,定制你的 commit 格式` 标记

## Open Questions (待 v1.0 spec 阶段确认)

- `Skill.allowed-tools` 引用 v0.6 MCP 工具(`mcp__fs__read_file`)时,
  路径匹配是按前缀还是按完整名?(倾向:按完整名,LLM 知道白名单里写什么)
- 子 Agent 跑完,`summary_model` 默认值用 sonnet 还是 haiku?(倾向:haiku
  节省成本,3 段摘要质量足够)
- Skill body 渲染是 raw markdown 还是把 `{var}` 转义?(倾向:raw,Markdown
  渲染层不参与 Skill body)
- `/skill list` 是不是必需命令,还是 LLM 调 `load_skill` 时报错就够
  (倾向:`/skill list` 必需,人类用户也要能查)
