## Context

v1.1 Hooks 系统已在生产可用,但 2026-07-09 release 后做了一次 v1.1.1 fix 收尾审计,发现 6 项遗留:

| # | 类别 | 现状 | 影响 |
|---|---|---|---|
| 1 | 功能 gap | executor slot=temp 读 `agent.state.temp_reminders`,但 Agent 无此字段 | 用户配 `slot: temp` 实际是 noop + warning log |
| 2 | 功能 gap | `/clear` 不清 hook 注入状态 | 新对话继承上一轮的 `## Hook Overrides` 段 + sticky hook_prompt reminder |
| 3 | 文档漂移 | `audit.py` 模块 docstring 写 `.baozicode/audit.log`,实现用 `.baozicode/hooks/<session>.audit.jsonl` | 后人 fork 易踩坑 |
| 4 | 测试 gap | `rotate_if_needed()` 路径未单测 | 100MB 阈值触发逻辑无回归保护 |
| 5 | 测试 gap | HookValidationError → SystemExit 集成路径无 e2e | app 启动崩溃模式未验证 |
| 6 | 测试 gap | 性能 smoke test 缺失 | pre hook 引入的开销无 baseline |
| 7 | 过程 | archive 的 tasks.md 100+ 项全 `[ ]` | 审计 / 历史追溯失真 |

约束:
- **零 breaking change** — v1.1 已 release,所有修复必须向后兼容(配置 / API / 数据格式)
- **不引入新 capability** — 这是 polish,不是新 feature
- **spec 必须先于代码** — OpenSpec spec-driven 流程要求新增 / 修改的 requirement 必须先在 specs/ 里落地
- **保留 v1.1 已实现的执行路径** — 不重构 executor / dispatcher 主逻辑,只补缺

## Goals / Non-Goals

**Goals:**
1. 让 v1.1 spec 承诺的 3 种 prompt slot 全部可用,行为可测
2. 让 `/clear` 真正清掉所有 hook 注入状态,符合 "新会话干净起点" 的用户期望
3. 让 audit log 路径在代码、docstring、README、迁移文档四处一致
4. 把 hooks 系统关键路径(rotate / SystemExit / 性能)的测试覆盖补齐
5. 把 archive 的 tasks.md 与实际交付状态对齐,留作 implementation evidence

**Non-Goals:**
- 不实现 sub-agent 实际执行(v1.1 占位,v1.1.1 不动)
- 不实现 system.compaction / system.cancel 的 action 执行(spec 明确 no-op)
- 不改 hooks 系统的对外 API(load_hooks / HookDispatcher / HookRegistry 签名稳定)
- 不改 ToolResult schema(已稳定,只是被消费方读)
- 不改 pipeline 顺序 L1 → hook.pre → L2-L5 → execute → hook.post(已稳定)
- 不改 v1.1 已交付的 run_once / parse_expr assignment / async audit 等修复

## Decisions

### D1: Agent 上加 `_temp_reminders` 字段(选完整实现而非删除 slot)

**决策**:在 `Agent.__init__` 加 `self._temp_reminders: list[str] = []`;`executor.execute_prompt` slot=temp 改读 `agent._temp_reminders`;`_inject_reminders` 在消费完所有 reminders 后,把 `_temp_reminders` 内容追加到当前轮 reminders 列表,然后清空。

**为什么不删 slot=temp**:
- spec `hooks-lifecycle` 在 proposal.md 里就是按 3 slot 写的,改 schema 是 breaking
- temp slot 在 "turn-scoped" 提醒场景下有用(例如 prompt action 想让 LLM "下轮加一句风格提醒" 而不希望它跨轮持续)
- 实现成本很低(一个 list + 两处 append/clear)

**为什么不重定向到 sticky**:
- sticky 跨 turn 持续,语义不同;用户配 `slot: temp` 期望它下轮消失
- 重定向会让 spec 与实现行为偏差,未来 debug 困惑

**实现要点**:
```python
# Agent.__init__
self._temp_reminders: list[str] = []

# _inject_reminders 在 plan_reminder + pending_reminders 处理完之后
if self._temp_reminders:
    for body in self._temp_reminders:
        reminders.append(Message(role="user", content=f'<system-reminder type="hook_prompt" ttl="once">{body}</system-reminder>'))
    self._temp_reminders = []  # 消费即清
```

executor 改:`temp_list = getattr(ctx.agent, "_temp_reminders", None)`(字段名直接对齐)。

### D2: `/clear` 清 hook 状态范围

**决策**:`_clear_conversation` 在现有清空后,补 3 行:

```python
# 清 hook 注入状态(v1.1.1)
if hasattr(app, "agent") and app.agent is not None:
    app.agent._pending_reminders = []
    app.agent._hook_stable_overrides = []
    app.agent._temp_reminders = []
```

**为什么清这三项**:
- `_pending_reminders` 里的 sticky hook_prompt 会在新对话第一轮就被注入 → 跨对话污染
- `_hook_stable_overrides` 是 append 到 `stable_system` 末尾的 `## Hook Overrides` 段,直接污染新对话的 prompt(更糟,因为 stable_system 是 byte-identical 缓存命中关键,override 清不干净会让 cache key 漂移)
- `_temp_reminders` 跨轮持久化没意义(本意就是 turn-scoped)

**为什么不在 hook 层做**:
- hook executor 通过 dispatch 调用,不知道 "clear" 概念
- clear 是 UI-state 切动作,由 chat_screen 集中处理

### D3: Audit log 路径描述统一

**决策**:保持 `app.py` 实际用 `<project>/.baozicode/hooks/<session>.audit.jsonl`(per-session,JSONL),改 `audit.py` 模块 docstring + `HookAuditLog.__init__` 注释对齐。spec 在 `hooks-lifecycle` 里也补一个 `Audit log per-session path` requirement 把这个约定钉死。

**为什么不做 .log 后缀 + 不做单文件**:
- 单文件多 session 写入会竞争,JSONL 隔离 + per-session 后缀简单可靠
- 已经在生产跑,改路径是 breaking change

### D4: archive tasks.md 逐项打勾

**决策**:读 `openspec/changes/archive/2026-07-09-v1-1-hooks-lifecycle/tasks.md`,逐项对照当前代码 / 测试 / 文档状态打勾。无法勾的项加一行注释说明(v1.1.1 polish 要补的 / 留作 v1.2 的 / spec 文档承诺但实际不可达的)。

**为什么保留 tasks.md**:
- spec-driven 流程下 archive 的 tasks 是 implementation record,后人审计可看 "v1.1 spec 承诺的 100+ 项里实际兑现了哪些"
- 删除会让历史追溯断档

### D5: 测试结构

**决策**:所有新测试追加到现有 `tests/test_hooks_v11.py`,不另开测试文件。复用已有的 `tests/_agent_helpers.py`(`make_minimal_config` / `make_minimal_agent` / FakeLLM 等 fixture)。

新增测试清单(预计 8 个):
1. `test_execute_prompt_temp_slot_appends_to_agent_temp_reminders` — executor 写
2. `test_inject_reminders_consumes_temp_reminders_once` — Agent 读 + 清空
3. `test_clear_conversation_wipes_pending_reminders_and_stable_overrides` — /clear 路径
4. `test_audit_log_rotation_triggers_at_threshold` — 100MB rotate
5. `test_app_startup_systemexit_on_invalid_hook_config` — e2e 启动崩溃
6. `test_pre_hook_overhead_under_150ms_with_three_shell_hooks` — 性能 smoke
7. `test_hook_audit_log_path_is_per_session_jsonl` — 路径契约
8. `test_archive_tasks_md_all_implemented_items_checked` — process 校验

## Risks / Trade-offs

[R1] **`_pending_reminders` / `_hook_stable_overrides` / `_temp_reminders` 是 Agent 私有字段(下划线前缀),chat_screen 直接 `app.agent._pending_reminders = []` 触犯了封装** → Mitigation:这是 polish 阶段的最快路径。v1.2 评估是否提为公开方法 `agent.clear_hook_state()`,届时 chat_screen 改调公开 API,本 polish 提交只清理功能不动 API。

[R2] **slot=temp 与 `_inject_reminders` 消费顺序不确定** → Mitigation:把 temp_reminders 拼接放在所有其他 reminder 之后,优先级最低,避免抢占 plan_mode / denial_rate_limit 等更紧急的 reminder 位。

[R3] **`/clear` 清 `_hook_stable_overrides` 会让 cache key 在新对话里重新走 "无 override" 路径,等于一次 cache miss** → Mitigation:接受这个开销(/clear 本就是少见的用户动作,cache miss 代价小);在 chat_screen log.info 写一行 "hook_overrides cleared" 方便 debug。

[R4] **性能 smoke test 150ms 阈值是经验值,可能在慢机器上不稳** → Mitigation:写成 `assert overhead < 300ms`(2x buffer),并在测试 docstring 注明 "soft threshold, adjust if CI 抖动";不写 `assert overhead < 150ms` 这种硬阈值。

[R5] **archive tasks.md 打勾是手工过程,容易漏项或错标** → Mitigation:用脚本式 grep 对照(每个 [ ] 项从 git blame 找是否有对应 commit,自动标 [x] 或加注释),减少手工误差。

[R6] **测试 e2e SystemExit 用例会真正 fork 子进程,CI 上慢** → Mitigation:用 `pytest.raises(SystemExit)` 在同进程捕,不真 fork。

## Migration Plan

无 breaking change → 无需 migration。

部署:
1. 合并 `v1-1-1-hooks-polish` → main
2. bump `pyproject.toml` 版本 1.1.0 → 1.1.1(若项目有版本号;否则仅 commit + tag `v1.1.1`)
3. 不需要 release notes(v1.1 已知限制的修复,不是新 feature)

回滚:
- 单 commit revert 即可
- 配置 / 数据格式没动,旧部署的 `.baozicode/hooks/<session>.audit.jsonl` 兼容

## Open Questions

- [ ] **Agent 私有字段访问的封装边界**:v1.2 是否要把 `clear_hook_state()` 提到公开 API?目前 chat_screen 直接 `agent._xxx = []` 触犯封装,但 polish 阶段不改 API。最早的 v1.2 change 可以处理。
- [ ] **archive 的 tasks.md 是否需要按 v1.2 同样的模式继续维护**:历史一致性 vs 维护成本 — 默认按"R5 Mitigation 用脚本对照,定期跑"处理。
- [ ] **`hook_overrides` 段在 stable_system 末尾的位置是否影响 cache key**:v0.4 cache breakpoint 设计是 `system_start` / `after_tools`,stable_system 末尾 override 不在 prefix 里,理论上不影响 cache 命中。但 anthropic prompt caching 的精确语义需要再核 — 如果有影响,可能要把 override 挪到 CacheBreakpoint 之后,而不是 append 到末尾。这个不属于 polish 范围,留 v1.2 audit。