## Why

v1.1.1 polish 收尾后,7 个留 v1.2 的项里用户选了 4 项关键(sytem.compaction/cancel fire +
两个 clear action + TUI 颜色),要在 v1.2 这一轮交付。其余 3 项(fsync interval / final
pipeline invocation / warn-log block_* + denied_by=None / LLM cache test)继续留,
按 process 推到后续 polish 版本。

scope 仍然在 hooks 系统内,**不引入新 capability**(只有 spec 增量 — 修改现有
`hooks-lifecycle` capability),不动其他模块架构。

## What Changes

**功能兑现(spec → 实现)**:

- **system.compaction fire** — `baozicode/context/orchestrator.py:maybe_compact` 进入时 fire `system.compaction` 事件(若有 hook dispatcher)。spec 提到 reserved 但 v1.1 没真 fire;现在接通让用户能 hook 进压缩时机做"压缩前先存档 / 压缩后重新拉记忆索引"之类自动化。

- **system.cancel fire** — `Agent.run` 主循环检测到 `USER_CANCELLED` 时 fire `system.cancel`。让用户能 hook 进"用户取消时清理 in-flight task、关掉外部资源"。

- **`clear_sticky_reminders` action** — 新增 ActionYaml kind,运行时清空 `Agent._pending_reminders`(stiky hook_prompt 那批)。语义独立:**不动** `_hook_stable_overrides` 也不动 `_temp_reminders`(后者本意 turn-scoped 自动清)。

- **`clear_stable_system_overrides` action** — 新增 ActionYaml kind,运行时清空 `Agent._hook_stable_overrides`(钉在 stable_system 末尾的 `## Hook Overrides` 段)。语义独立:**不动** 其他两个。

- **TUI `tool_card` 颜色按 execution_status** — `baozicode/tui/tool_card.py` 渲染 ToolResult 时按 `execution_status` 上色:L1=红 / hook_pre=黄 / L2-L5=橙 / executed_success=绿 / executed_failed=红(execution_status=None 用默认色,向后兼容)。让用户在 TUI 一眼区分被哪一层拒 / 是否成功。

## Capabilities

### New Capabilities

无 — 全是 `hooks-lifecycle` capability 内部的增量需求。

### Modified Capabilities

- **`hooks-lifecycle`**:新增 3 个 requirement + 修改 1 个 requirement
  - 新增 `system.compaction event fires on context compaction` requirement(进入 maybe_compact 时 fire)
  - 新增 `system.cancel event fires on user cancel` requirement(USER_CANCELLED 设置时 fire)
  - 新增 `Clear-control hook actions (clear_sticky_reminders / clear_stable_system_overrides)` requirement(2 个新 ActionYaml kind,各自独立清一类状态)
  - 修改 `ToolResult rendering colors per execution_status` requirement(原 spec §13 / README 没有 TUI 颜色约束,这里首次补上)

## Impact

**代码**:
- `baozicode/context/orchestrator.py:maybe_compact` — 进入时 lazy import `baozicode.hooks.dispatcher`,fire `system.compaction` 事件(若有 dispatcher)
- `baozicode/agent/loop.py:Agent.run` 主循环 — 检测到 USER_CANCELLED 路径时 fire `system.cancel`(注意 dispatcher 可能为 None,防御)
- `baozicode/hooks/schema.py` — 新增 `_ClearStickyAction` + `_ClearStableAction` 两个 Pydantic model;`ActionYaml` discriminator 扩到 6 种
- `baozicode/hooks/executor.py` — 新增 `execute_clear_sticky_reminders` + `execute_clear_stable_overrides` 两个 executor;`execute_action` dispatch 扩到 6 种
- `baozicode/agent/loop.py` — `enqueue_reminder` 等已有 `setattr` 暴露;清空逻辑直接调现有 helper 或新加
- `baozicode/tui/tool_card.py` — 渲染分支按 `execution_status` 选 CSS class

**测试**(`tests/test_hooks_v11.py` 追加 ~6 个):
- `test_system_compaction_event_fires_on_maybe_compact`
- `test_system_cancel_event_fires_on_user_cancel`
- `test_clear_sticky_reminders_action_empties_pending_reminders`
- `test_clear_stable_system_overrides_action_empties_overrides`
- `test_clear_actions_do_not_touch_other_hook_state`(交叉隔离)
- `test_tool_card_renders_color_per_execution_status`(TUI snapshot 或 class 断言)

**文档**:
- `openspec/specs/hooks-lifecycle/spec.md` — append 4 个新 / 修改 requirement
- `docs/migrations/v1.1-to-v1.2.md` — 新增迁移指南
- `README.md` Hooks 段补一句"system 事件已可 hook"
- `config.example.yaml` 加 2 个新 action 示例

**零 breaking change**:
- 新增 2 个 action kind 不影响现有 4 种(shell/http/prompt/sub-agent)
- 新增 2 个 system event fire 不改 dispatcher 既有行为
- TUI 加色向后兼容(`execution_status=None` 走默认色)

## 留 v1.2 后续 polish 的项

- 6.6 fsync interval(HookAuditLog 1s fsync)
- 6.8 final `action_kind="pipeline"` invocation
- 8.7 warn-log block_* + denied_by=None
- 12.13 LLM cache byte-identical prefix 测试
- 12.14 ~~TUI 颜色~~(本轮做)
- 9.16/9.17 ~~system.compaction/cancel fire~~(本轮做)
- 11.14/11.15 ~~clear action~~(本轮做)