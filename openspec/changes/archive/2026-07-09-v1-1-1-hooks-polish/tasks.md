# v1.1.1 Hooks Polish — Tasks

## 1. 代码改动(兑现 v1.1 spec 承诺)

- [ ] 1.1 在 `baozicode/agent/loop.py:Agent.__init__` 加 `self._temp_reminders: list[str] = []`
- [ ] 1.2 在 `baozicode/agent/loop.py:Agent._inject_reminders` 末尾消费 `_temp_reminders`(append 到 reminders,然后清空列表)
- [ ] 1.3 改 `baozicode/hooks/executor.py:execute_prompt` slot=temp 分支:`temp_list = getattr(ctx.agent, "_temp_reminders", None)`(对齐字段名)
- [ ] 1.4 改 `baozicode/tui/chat_screen.py:_clear_conversation`,在现有清空后补 3 行:清 `agent._pending_reminders` / `agent._hook_stable_overrides` / `agent._temp_reminders`
- [ ] 1.5 修 `baozicode/hooks/audit.py` 模块 docstring:`<project>/.baozicode/audit.log` → `<project>/.baozicode/hooks/<session_id>.audit.jsonl`,加 per-session 说明
- [ ] 1.6 跑 `tests/test_hooks_v11.py` 确认 38 个原有测试不挂(回归保护)

## 2. 新增测试(`tests/test_hooks_v11.py` 追加)

- [ ] 2.1 `test_execute_prompt_temp_slot_appends_to_agent_temp_reminders` — executor 写 _temp_reminders
- [ ] 2.2 `test_inject_reminders_consumes_temp_reminders_once` — 消费即清
- [ ] 2.3 `test_clear_conversation_wipes_pending_reminders_and_stable_overrides` — /clear 路径
- [ ] 2.4 `test_clear_conversation_wipes_temp_reminders` — /clear 清 temp
- [ ] 2.5 `test_audit_log_rotation_triggers_at_threshold` — 100MB rotate,rename + 新文件
- [ ] 2.6 `test_audit_log_path_is_per_session_jsonl` — 路径契约
- [ ] 2.7 `test_app_startup_systemexit_on_invalid_hook_config` — bootstrap 抛 HookValidationError → app __init__ SystemExit
- [ ] 2.8 `test_pre_hook_overhead_under_300ms_with_three_shell_hooks` — 性能 smoke(soft 阈值,见 design R4)
- [ ] 2.9 跑完整 `pytest tests/` 确认全绿

## 3. 文档 / process

- [ ] 3.1 读 `openspec/changes/archive/2026-07-09-v1-1-hooks-lifecycle/tasks.md`,对照当前代码 / 测试状态逐项打勾;无法勾的项加注释说明(留 v1.2 / v1.1.1 处理)
- [ ] 3.2 改 `docs/migrations/v1.0-to-v1.1.md` `/clear` 段落补一句 "v1.1.1 起同时清 hook_prompt reminder / hook_overrides / temp_reminders"
- [ ] 3.3 改 `README.md` `/clear` 描述(如有 v1.1 hook 描述)补一句

## 4. 验收 + commit

- [ ] 4.1 跑 `pytest tests/` 全绿(预期 ~46 个测试)
- [ ] 4.2 跑 `git diff --stat` 确认改动范围合理(预计 4-6 个文件)
- [ ] 4.3 `git commit` 标题 `fix(v1.1.1): slot=temp + /clear hook state + audit rotate tests`
- [ ] 4.4 `git push origin main` 推到远端
- [ ] 4.5 跑 `openspec archive v1-1-1-hooks-polish --yes` 归档 change(spec 自动 merge 到 openspec/specs/hooks-lifecycle/spec.md)