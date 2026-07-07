# v0.4 Prompt — Implementation Tasks

> 详细 TDD 步骤见 `docs/superpowers/plans/2026-07-07-v0-4-prompt.md`（2217 行 plan，含每个 step 的代码、命令、预期输出）。本文件是 OpenSpec apply 阶段要解析的高层 checklist。

## 1. Phase 1 — 拼装骨架 (Skeleton)

- [x] 1.1 Create `baozicode/prompt/__init__.py` re-exporting public API
- [x] 1.2 Create `baozicode/prompt/types.py` with `BuiltPrompt`, `BuildContext`, `CacheBreakpoint`, `SystemReminder` dataclasses
- [x] 1.3 Create `baozicode/prompt/rules.py` with `Rule`, `RuleRegistry`, and 7 `DEFAULT_RULES`
- [x] 1.4 Create `baozicode/prompt/reminder.py` with `PlanModeReminder` class
- [x] 1.5 Create 7 fixed section renderers in `baozicode/prompt/sections/`: `identity`, `constraints`, `task_mode`, `action_exec`, `tool_usage`, `tone_style`, `text_output`
- [x] 1.6 Create 4 dynamic/optional section renderers: `env_info`, `custom`, `skills`, `memory`
- [x] 1.7 Create `baozicode/prompt/builder.py` with `PromptBuilder.build()` assembling all 11 sections
- [x] 1.8 Write unit tests in `tests/test_prompt_modules.py`, `tests/test_rule_registry.py`, `tests/test_plan_reminder.py`, `tests/test_prompt_builder.py`
- [x] 1.9 Verify existing 67 v0.3 tests still pass; total new tests ≥ 21

## 2. Phase 2 — Agent 集成 (Agent Integration)

- [ ] 2.1 Extend `baozicode/config/schema.py`: add `custom_instructions`, `skills_dir`, `memory_path`, `plan_reminder_interval` to `AppConfig`; add `enable_system_reminders` and `rules: RulesConfig` to `AgentConfig`; add new `RulesConfig` class with 7 boolean fields
- [ ] 2.2 Update `config.example.yaml` with v0.4 fields and `agent:` block
- [ ] 2.3 Modify `baozicode/agent/loop.py`: replace `system_prompt: str` parameter with `config: AppConfig`; call `PromptBuilder.build()` once in `__init__`; add `_inject_reminders` method
- [ ] 2.4 Migrate existing tests in `tests/test_agent_loop.py` and `tests/test_plan_mode.py` from `system_prompt=...` to `config=AppConfig(...)` constructor
- [ ] 2.5 Add `tests/test_agent_with_prompt.py` with 8 integration tests capturing system/tools/messages/breakpoints
- [ ] 2.6 Verify total tests ≥ 100; existing 67 + new 21 (Phase 1) + new 8 (Phase 2)

## 3. Phase 3 — 缓存接口 + 后端兼容 (Cache Interface)

- [ ] 3.1 Modify `baozicode/llm/base.py` to add `cache_breakpoints` keyword-only parameter to `LLMClient.stream`
- [ ] 3.2 Update 4 backend `stream` methods (`anthropic.py`, `openai.py`, `minimax.py`, `deepseek.py`) to accept and ignore `cache_breakpoints`
- [ ] 3.3 Modify `baozicode/tui/chat_screen.py` `/status` command to display `cache_read`, `cache_write`, and `hit_rate` lines
- [ ] 3.4 Add `tests/test_cache_strategy.py` with 3 tests verifying `BuiltPrompt.cache_breakpoints` and `UsageStats` cache fields
- [ ] 3.5 Add `tests/test_llm_interface_extension.py` with 2 tests verifying `cache_breakpoints` is keyword-only and accepted by 4 backends
- [ ] 3.6 Add `tests/test_prompt_scenarios.py` with 3 scenario tests (Grep-not-Bash, unknown_tool guard, plan_mode locks writes)
- [ ] 3.7 Verify total tests ≥ 108; update README/CLAUDE.md to document v0.4 features

## 4. Documentation & Finalization

- [ ] 4.1 Update `README.md` v0.4 section: 11 modules, `<system-reminder>` mechanism, RulesConfig, cache_breakpoints interface
- [ ] 4.2 Update `CLAUDE.md` to add `baozicode/prompt/` to module structure; update dependency direction diagram
- [ ] 4.3 Run `pytest tests/ -v` and verify ≥ 108 tests pass with exit code 0
- [ ] 4.4 Run `python -m baozicode --help` to verify CLI is unchanged
- [ ] 4.5 Commit each phase as a separate commit with conventional commit messages
