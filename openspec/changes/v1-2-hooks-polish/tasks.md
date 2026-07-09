# v1.2 Hooks Polish — Tasks

## 1. system.compaction / system.cancel fire

- [x] 1.1 在 `baozicode/context/orchestrator.py:maybe_compact` 入口 lazy import + fire `system.compaction`(payload: trigger + tokens_before),try/except 包,fail-open
- [x] 1.2 在 `baozicode/agent/loop.py:Agent.run` USER_CANCELLED 退出路径 fire `system.cancel`(payload: reason + iteration),try/except 包,fail-open
- [x] 1.3 跑 `tests/test_hooks_v11.py` 确认原有 48 个测试不挂

## 2. clear_sticky_reminders / clear_stable_system_overrides action

- [x] 2.1 在 `baozicode/hooks/schema.py` 新增 `_ClearStickyAction` + `_ClearStableAction` 两个 Pydantic model(仅 action 字段,无其他)
- [x] 2.2 把 `ActionYaml` discriminator 扩到 6 种(`_ShellAction` / `_HttpAction` / `_PromptAction` / `_SubAgentAction` / `_ClearStickyAction` / `_ClearStableAction`)
- [x] 2.3 在 `baozicode/hooks/executor.py` 新增 `execute_clear_sticky_reminders`(只清 `agent._pending_reminders`)+ `execute_clear_stable_overrides`(只清 `agent._hook_stable_overrides`)
- [x] 2.4 `execute_action` dispatch 加 2 个 isinstance 分支
- [x] 2.5 跑 `tests/test_hooks_v11.py` 确认原有 48 个测试不挂

## 3. TUI ToolResultCard 颜色按 execution_status

- [x] 3.1 在 `baozicode/tui/tool_card.py` 加 `EXEC_STATUS_CLASS` 映射 dict(5 种 status + None 兜底)
- [x] 3.2 `ToolResultCard.compose` 根据 `self.result.execution_status` 选 class,加到 widget classes
- [x] 3.3 在 `baozicode/tui/styles.tcss` 加 5 个 class 样式(`.tool-result-card.-block-l1` / `-block-hook-pre` / `-block-permission` / `-executed-success` / `-executed-failed`)+ 兜底默认
- [x] 3.4 跑 `tests/test_hooks_v11.py` 确认不挂(新测试不进 test_hooks_v11.py 的话需要单独建 test 文件)

## 4. 新增测试(`tests/test_hooks_v11.py` 追加)

- [x] 4.1 `test_system_compaction_event_fires_on_maybe_compact` — spy dispatcher + 调 maybe_compact,断言收到
- [x] 4.2 `test_system_cancel_event_fires_on_user_cancel` — Agent.run 触发 USER_CANCELLED,断言收到
- [x] 4.3 `test_clear_sticky_reminders_action_empties_pending_reminders` — 跑 action 后 _pending_reminders == []
- [x] 4.4 `test_clear_stable_system_overrides_action_empties_overrides` — 跑 action 后 _hook_stable_overrides == []
- [x] 4.5 `test_clear_actions_do_not_touch_other_hook_state` — 跑 clear_sticky 验证其他两个不动
- [x] 4.6 `test_tool_card_renders_color_per_execution_status` — 构造 5 种 ToolResult,渲染 widget,断言 classes 包含预期 class

## 5. 文档

- [x] 5.1 写 `docs/migrations/v1.1-to-v1.2.md`:新增 2 个 action kind + 2 个 system event 可 hook + TUI 颜色变化
- [x] 5.2 改 `README.md` Hooks 段补一句"v1.2 起 system.compaction / system.cancel 可 hook + 新增 2 个 clear control action"
- [x] 5.3 改 `config.example.yaml` 加 2 个新 action 示例(clear_sticky_reminders + clear_stable_system_overrides)

## 3. TUI ToolResultCard 颜色按 execution_status

- [ ] 3.1 在 `baozicode/tui/tool_card.py` 加 `EXEC_STATUS_CLASS` 映射 dict(5 种 status + None 兜底)
- [ ] 3.2 `ToolResultCard.compose` 根据 `self.result.execution_status` 选 class,加到 widget classes
- [ ] 3.3 在 `baozicode/tui/styles.tcss` 加 5 个 class 样式(`.tool-result-card.-block-l1` / `-block-hook-pre` / `-block-permission` / `-executed-success` / `-executed-failed`)+ 兜底默认
- [ ] 3.4 跑 `tests/test_hooks_v11.py` 确认不挂(新测试不进 test_hooks_v11.py 的话需要单独建 test 文件)

## 4. 新增测试(`tests/test_hooks_v11.py` 追加)

- [ ] 4.1 `test_system_compaction_event_fires_on_maybe_compact` — spy dispatcher + 调 maybe_compact,断言收到
- [ ] 4.2 `test_system_cancel_event_fires_on_user_cancel` — Agent.run 触发 USER_CANCELLED,断言收到
- [ ] 4.3 `test_clear_sticky_reminders_action_empties_pending_reminders` — 跑 action 后 _pending_reminders == []
- [ ] 4.4 `test_clear_stable_system_overrides_action_empties_overrides` — 跑 action 后 _hook_stable_overrides == []
- [ ] 4.5 `test_clear_actions_do_not_touch_other_hook_state` — 跑 clear_sticky 验证其他两个不动
- [ ] 4.6 `test_tool_card_renders_color_per_execution_status` — 构造 5 种 ToolResult,渲染 widget,断言 classes 包含预期 class

## 5. 文档

- [ ] 5.1 写 `docs/migrations/v1.1-to-v1.2.md`:新增 2 个 action kind + 2 个 system event 可 hook + TUI 颜色变化
- [ ] 5.2 改 `README.md` Hooks 段补一句"v1.2 起 system.compaction / system.cancel 可 hook + 新增 2 个 clear control action"
- [ ] 5.3 改 `config.example.yaml` 加 2 个新 action 示例(clear_sticky_reminders + clear_stable_system_overrides)

## 6. 验收 + commit + archive

- [ ] 6.1 跑 `pytest tests/test_hooks_v11.py` 全绿(预计 ~54 个测试)
- [ ] 6.2 跑完整 `pytest tests/`,确认全绿
- [ ] 6.3 `git diff --stat` 确认改动范围合理(预计 7-10 个文件)
- [ ] 6.4 `git commit` 标题 `feat(v1.2): system.compaction/cancel fire + 2 clear actions + TUI exec status colors`
- [ ] 6.5 `git push origin main` 推到远端
- [ ] 6.6 `openspec archive v1-2-hooks-polish --yes` 归档(4 个新 ADDED Requirements 合到 openspec/specs/hooks-lifecycle/spec.md)