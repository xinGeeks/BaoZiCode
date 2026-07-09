## Why

v1.1 release 后审计发现 6 项 spec 承诺 / 实现不一致 / 测试 gap,需要在 v1.1.1 polish 阶段修复,不留到 v1.2。
范围都在 hooks 系统内,**不引入新能力**,只是兑现 v1.1 已承诺但未完成的部分 + 修文档漂移 + 补齐测试覆盖。

## What Changes

**功能 gap(代码兑现 spec)**:
- **slot=temp 完整实现** — v1.1 spec 承诺 prompt action 支持 sticky_reminder / stable_system / temp 三种 slot,当前只实现了前两个,`slot=temp` 在 executor 走 "agent.state.temp_reminders 找不到" 警告路径。在 Agent 上加 `_temp_reminders` 字段,executor 注入 + `_inject_reminders` 消费(每轮清空)。
- **`/clear` 清 hook 状态** — `chat_screen._clear_conversation` 当前不清 `_pending_reminders`(sticky hook_prompt)、`_hook_stable_overrides`(钉进 prompt 的 `## Hook Overrides` 段)。/clear 后新对话会看到上一轮的 hook 状态,违反 "/clear = 新会话干净起点" 的语义。补齐 `_pending_reminders` / `_hook_stable_overrides` / `_temp_reminders` 的清空。
- **audit.py docstring 修正** — 默认路径注释写的是 `.baozicode/audit.log`,但 `app.py:113` 实际用 `.baozicode/hooks/<session>.audit.jsonl`。改 docstring 与实现对齐,免得后人 fork。

**测试 gap**:
- **`HookAuditLog.rotate_if_needed()` 单测缺失** — 100 MB 启动期 rotate 路径 v1.1.1 加了 `record_invocation` 测试但没单独覆盖。
- **HookValidationError → SystemExit 集成测试缺失** — `app.py` 启动期是否真把 HookValidationError 转成 SystemExit + 友好 banner,没有 e2e 验证。
- **性能 smoke test 缺失** — tasks.md #12.11 要求 "pre hook N=3 × 30ms shell → 总 overhead < 150ms",没做。

**过程 / 文档**:
- **archive v1-1-hooks-lifecycle/tasks.md 标完成** — 当前 100+ 项全 `[ ]`,实际代码大部分已交付;按代码状态打勾,留作 implementation evidence。
- **CLAUDE.md / 迁移文档 `/clear` 段落补一句** — v1.1.1 起 `/clear` 同时清 hook 注入状态。

## Capabilities

### New Capabilities

无 — 全部是对 v1.1 既有功能的 polish,没有引入新能力。

### Modified Capabilities

- **`hooks-lifecycle`**:补 3 个新 requirement + 修 2 个文档不一致
  - 新增 `Prompt slot delivery` requirement(sticky / stable / temp 三 slot 的生命周期语义)
  - 新增 `Hook state isolation from /clear` requirement(/clear 同时清 pending_reminders / hook_stable_overrides / temp_reminders)
  - 新增 `Audit log per-session path` requirement(spec 与实现对齐:`<project>/.baozicode/hooks/<session>.audit.jsonl`,100 MB rotate)

## Impact

**代码**:
- `baozicode/hooks/executor.py` — slot=temp 路径简化(不再读 `agent.state.temp_reminders`)
- `baozicode/agent/loop.py` — 新增 `_temp_reminders: list[str]` 字段;`_inject_reminders` 消费 temp_reminders;`set_dynamic_section` 不变;`/clear` 调用方补清逻辑
- `baozicode/tui/chat_screen.py:_clear_conversation` — 补清 `agent._pending_reminders` / `_hook_stable_overrides` / `_temp_reminders`
- `baozicode/hooks/audit.py` — 模块 docstring 修正默认路径描述
- `openspec/changes/archive/2026-07-09-v1-1-hooks-lifecycle/tasks.md` — 逐项打勾 `[x]`

**测试**:
- `tests/test_hooks_v11.py` — 新增 `test_execute_prompt_temp_slot_delivers_one_shot` / `test_clear_conversation_wipes_hook_state` / `test_audit_log_rotation_triggers_at_threshold` / `test_app_startup_systemexit_on_invalid_hook_config` / `test_pre_hook_overhead_under_150ms` / 等
- `tests/test_app_startup.py`(可能新建) — bootstrap → SystemExit 集成

**文档**:
- `docs/migrations/v1.0-to-v1.1.md` — `/clear` 段落补一句 "v1.1.1 起同时清 hook_prompt reminder / hook_overrides / temp_reminders"
- `openspec/specs/hooks-lifecycle/spec.md` — append 3 个新 requirement(`## ADDED Requirements` 段)
- `openspec/changes/archive/2026-07-09-v1-1-hooks-lifecycle/tasks.md` — 逐项打勾
- `README.md` — `/clear` 描述补一句(如有 v1.1 hook 描述)

**零 breaking change** — 全是兑现既有承诺,旧代码 / 配置 / 调用方式不变。