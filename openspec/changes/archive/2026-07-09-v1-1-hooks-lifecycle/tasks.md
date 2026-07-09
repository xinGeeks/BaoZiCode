# v1.1 Hooks Lifecycle — Tasks

## 1. Schema 层 (Pydantic 模型)

- [ ] 1.1 Create `baozicode/hooks/__init__.py` and `baozicode/hooks/schema.py`
- [ ] 1.2 Implement `EventName` Literal type (11 values: session.start/end, turn.start/end, message.received/sent, tool.pre/post, system.error/compaction/cancel)
- [ ] 1.3 Implement `HookDefYaml` Pydantic model (id / event / if_ alias "if" / actions / async_ alias "async" / timeout_seconds=30 / run_once=False)
- [ ] 1.4 Implement `ConditionYaml` (all: list / any: list, exactly one, both → validation error)
- [ ] 1.5 Implement `MatcherYaml` tagged union (tool: str exact OR arg.<name>: MatchValue)
- [ ] 1.6 Implement `MatchValue` tagged union (kind ∈ exact/glob/regex/not_exact/not_glob/not_regex + value)
- [ ] 1.7 Implement `ActionYaml` tagged union (4 kinds: shell / http / prompt / sub-agent, each with required fields)
- [ ] 1.8 Enforce shell/prompt reject `deny`/`deny_reason`/`parse_expr` at parse time
- [ ] 1.9 Enforce http/sub-agent require `parse_expr` for deny capability
- [ ] 1.10 Convert Pydantic ValidationError → HookValidationError carrying list of all errors
- [ ] 1.11 Add 18 unit tests covering each Literal branch, tag discriminator, required/optional field combinations

## 2. Condition 匹配器

- [ ] 2.1 Create `baozicode/hooks/condition.py`
- [ ] 2.2 Implement `match_exact(value, pattern) -> bool` (string equality)
- [ ] 2.3 Implement `match_glob(value, pattern) -> bool` via `fnmatch.fnmatch`
- [ ] 2.4 Implement `match_regex(value, pattern) -> bool` via `re.search` (substring match)
- [ ] 2.5 Implement `match_not_exact` / `match_not_glob` / `match_not_regex` (negation variants)
- [ ] 2.6 Implement `evaluate_condition(condition: ConditionYaml, call: ToolCall) -> bool` (None/{} → True; all/any combine; both present → error)
- [ ] 2.7 Handle missing arg.<name> in matchers (treat as False in AND, allow others in OR)
- [ ] 2.8 Coerce non-str arg values (list/dict) to str for matching
- [ ] 2.9 Add 12 unit tests covering 4 matchers + not_* variants + all/any combinations + missing-arg scenarios

## 3. Action 执行器

- [ ] 3.1 Create `baozicode/hooks/executor.py`
- [ ] 3.2 Implement `ActionResult` dataclass (deny / reason / enqueue_body / error)
- [ ] 3.3 Implement `execute_shell(action, ctx)` via `asyncio.create_subprocess_exec("bash", "-c", command)`
- [ ] 3.4 Auto-inject env vars in shell: $TOOL_NAME, $TOOL_CALL_ID, $ARG_<NAME>, $EVENT, $HOOK_ID
- [ ] 3.5 Shell exit_code ≠ 0 → deny with stdout first line as reason, empty stdout defaults to `"hook shell 拦截高危工具调用"`
- [ ] 3.6 Shell timeout (default 30s) → deny with reason `"hook shell 执行超时"`
- [ ] 3.7 Implement `execute_http(action, ctx)` via `aiohttp.ClientSession`
- [ ] 3.8 HTTP 4xx/5xx/connection error → deny=False (interface error ≠ active deny)
- [ ] 3.9 HTTP parse_expr via simpleeval on `res = SimpleNamespace(status, body)`; allow `res.deny` and `res.deny_reason` assignment
- [ ] 3.10 HTTP deny_reason missing when deny=True → HookParseError
- [ ] 3.11 Implement `execute_subagent(action, ctx)` (v1.1 placeholder: parse_expr only, log warning "sub-agent 执行器 v1.1 仅占位")
- [ ] 3.12 Implement `execute_prompt(action, ctx)` with 3 slots: sticky_reminder / stable_system / temp
- [ ] 3.13 Prompt default slot=sticky_reminder → call `agent.enqueue_reminder(kind="hook_prompt", body, ttl)`
- [ ] 3.14 Prompt slot=stable_system → `agent.set_dynamic_section("hook_overrides", content)` (with tool.* event guard)
- [ ] 3.15 Prompt slot=temp → attach to current turn context (one-shot)
- [ ] 3.16 Prompt enqueue=False explicit → `log.info("hook_prompt: %s", content)`
- [ ] 3.17 Add 18 unit tests covering 4 actions × happy / deny / timeout / parse fail / field missing

## 4. HookRegistry 加载 + freeze 集中校验

- [ ] 4.1 Create `baozicode/hooks/registry.py`
- [ ] 4.2 Implement `HookValidationError` exception (carries list of {hook_id, field, reason})
- [ ] 4.3 Implement `HookRegistry.load(app_config) -> HookRegistry` (parse all hooks, collect ValidationErrors)
- [ ] 4.4 Implement `HookRegistry.freeze() -> None` running all validation rules
- [ ] 4.5 Freeze rule: duplicate hook id → error naming both occurrences
- [ ] 4.6 Freeze rule: event not in 11-value Literal → error
- [ ] 4.7 Freeze rule: actions empty list → error
- [ ] 4.8 Freeze rule: event=tool.pre + async=True → error "async not allowed for tool.pre"
- [ ] 4.9 Freeze rule: if.all + if.any both present → error "mutually exclusive"
- [ ] 4.10 Freeze rule: slot=stable_system + event ∈ {tool.pre, tool.post} → error
- [ ] 4.11 Collect ALL errors before raising (don't bail at first)
- [ ] 4.12 Implement `create_dispatcher(agent) -> HookDispatcher` (back-reference to agent for enqueue_reminder)
- [ ] 4.13 Implement `list_hooks(event: EventName) -> list[HookDefYaml]` (declaration order)
- [ ] 4.14 Add 14 unit tests covering each freeze rule + happy path + multi-error collection

## 5. HookDispatcher 事件分发

- [ ] 5.1 Create `baozicode/hooks/dispatcher.py`
- [ ] 5.2 Implement `HookContext` dataclass (event / hook_id / agent / payload / timeout)
- [ ] 5.3 Implement `HookResult` dataclass (denied / denied_hook_id / reason / error)
- [ ] 5.4 Implement `dispatcher.run(event, payload) -> HookResult` — main entry point
- [ ] 5.5 Iterate hooks in declaration order, evaluate condition first
- [ ] 5.6 Execute hook actions sequentially; first deny → stop hook's remaining actions
- [ ] 5.7 First hook deny → stop entire dispatcher (later hooks don't run)
- [ ] 5.8 Wrap each action in try/except → log.warning, continue (fail-open)
- [ ] 5.9 Support async=True on tool.post: `asyncio.create_task(self._run_hook_async(...))` fire-and-forget
- [ ] 5.10 async post default behavior: log.warning only, no enqueue
- [ ] 5.11 async post with enqueue=True: call `agent.enqueue_reminder(...)` after task completes
- [ ] 5.12 async pre blocking: defensive runtime check, log.error and skip if registered
- [ ] 5.13 Add 12 unit tests: no hooks / single allow / multi-hook short-circuit / multi-action short-circuit / condition miss / hook exception fail-open / async post background

## 6. 审计日志

- [ ] 6.1 Create `baozicode/hooks/audit.py`
- [ ] 6.2 Implement `HookInvocation` dataclass (timestamp / event / hook_id / action_kind / tool_name / tool_call_id / deny / reason / duration_ms / error)
- [ ] 6.3 Implement `HookAuditLog(log_path: Path)` with aiofiles append
- [ ] 6.4 Implement `record_invocation(invocation)` async append one JSON line
- [ ] 6.5 Default log path `<project>/.baozicode/audit.log`, auto-create parent dirs
- [ ] 6.6 Append-only, no fsync (caller controls 1s fsync interval)
- [ ] 6.7 Implement size-based rotation at startup (default 100MB → `audit.log.YYYYMMDD-HHMMSS`)
- [ ] 6.8 Agent.run finally block: write final invocation with action_kind="pipeline" reflecting final ToolResult.execution_status
- [ ] 6.9 Add 8 unit tests: write happy / write fail tolerance / rotate trigger / timestamp format

## 7. Bootstrap 串接

- [ ] 7.1 Create `baozicode/hooks/bootstrap.py`
- [ ] 7.2 Implement `load_hooks(app_config, agent) -> HookDispatcher | None`
- [ ] 7.3 None path: app_config.hooks is None → return None (Agent treats as v1.0)
- [ ] 7.4 Non-None path: registry.load → freeze → create_dispatcher(agent)
- [ ] 7.5 Modify `BaoZiCodeApp.startup()` to inject `hooks.bootstrap(...)` after `permissions.bootstrap(...)`
- [ ] 7.6 Pass dispatcher into Agent constructor: `Agent(..., hook_registry=dispatcher)`
- [ ] 7.7 Final bootstrap order: config → permissions → hooks → instructions → memory → sessions → commands → skills → Agent
- [ ] 7.8 HookValidationError → SystemExit with message prefixed `ERROR: hooks validation failed:`
- [ ] 7.9 Add 6 unit tests: None path / happy path / freeze failure SystemExit / bootstrap order

## 8. ToolResult schema 改造

- [ ] 8.1 Modify `baozicode/tools/base.py` `ToolResult` dataclass
- [ ] 8.2 Add field `execution_status: Literal["block_l1","block_hook_pre","block_permission","executed_success","executed_failed"] | None = None`
- [ ] 8.3 Add field `denied_by: Literal["l1_blacklist","hook_pre","l2_l5_permission"] | None = None`
- [ ] 8.4 Add field `denied_hook_id: str | None = None`
- [ ] 8.5 Implement `__post_init__`: if execution_status is None → is_error from explicit arg (v1.0 compat)
- [ ] 8.6 Implement `__post_init__`: if execution_status is set → is_error = (execution_status != "executed_success")
- [ ] 8.7 Implement `__post_init__`: warn-log if execution_status ∈ block_* and denied_by is None
- [ ] 8.8 Verify old call sites `ToolResult(tool_call_id="x", content="y", is_error=True)` still work unchanged
- [ ] 8.9 Verify new call sites `ToolResult(..., execution_status="block_l1", denied_by="l1_blacklist")` auto-derive is_error
- [ ] 8.10 Add 12 unit tests: each execution_status value / derivation rules / old-call compatibility / missing-field compatibility

## 9. Agent pipeline 改造

- [ ] 9.1 Modify `Agent.__init__` to accept keyword-only `hook_registry=None`
- [ ] 9.2 Lazy import `baozicode.hooks` symbols inside methods (avoid module-load pollution when hook_registry=None)
- [ ] 9.3 Fire `session.start` at top of `Agent.run(...)` (only if hook_registry)
- [ ] 9.4 Fire `session.end` in finally block of `Agent.run(...)` (fires even on cancel/exception)
- [ ] 9.5 Fire `turn.start` at top of every iteration
- [ ] 9.6 Fire `turn.end` in finally at bottom of every iteration
- [ ] 9.7 Fire `message.received` before `conversation.add_user(...)`
- [ ] 9.8 Fire `message.sent` after `conversation.add_turn(...)`
- [ ] 9.9 Replace `_v5_executor` with v1.1 pipeline: L1 → hook.pre → L2-L5 → execute → hook.post
- [ ] 9.10 Pipeline step 1: call `permissions.blacklist.check(call)`; deny → block_l1 ToolResult
- [ ] 9.11 Pipeline step 2: call `dispatcher.run("tool.pre", call)`; deny → block_hook_pre ToolResult with denied_hook_id
- [ ] 9.12 Pipeline step 3: call `permissions.check_layers_2_through_5(call, merged)`; deny → block_permission ToolResult
- [ ] 9.13 Pipeline step 4: invoke tool; success → executed_success; tool exception → executed_failed via try/except
- [ ] 9.14 Pipeline step 5 (finally): call `dispatcher.run("tool.post", result)` — fires on every outcome
- [ ] 9.15 Fire `system.error` when Agent.run is about to re-raise unhandled exception
- [ ] 9.16 Fire `system.compaction` at v0.7 compaction entry point
- [ ] 9.17 Fire `system.cancel` after USER_CANCELLED is set, before exit
- [ ] 9.18 Wrap every `dispatcher.run(...)` call in try/except → log.warning (fail-open)
- [ ] 9.19 Add 18 unit tests: each lifecycle event / each pipeline step / hook.post always fires / L1 short-circuit before hook.pre / hook exception tolerance / ToolResult field population

## 10. permissions check_layers_2_through_5 入口

- [ ] 10.1 Modify `baozicode/permissions/check.py`
- [ ] 10.2 Add `check_layers_2_through_5(call, merged) -> PermissionDecision` public function
- [ ] 10.3 Skip L1 (DangerousCommandBlacklist) entirely
- [ ] 10.4 Run L2 PathSandbox → L3 RuleEngine → L4 PermissionMode → L5 PermissionCallback in order
- [ ] 10.5 Type-constrain returned layer to `Literal["L2_sandbox","L3_rule","L4_mode","L5_user","none"]`
- [ ] 10.6 Verify existing `permissions.check(call, merged)` behavior unchanged (still includes L1)
- [ ] 10.7 Verify `permissions.blacklist.check(call)` remains the L1-only entry point
- [ ] 10.8 Add 8 unit tests: L1 skipped / L2-L5 happy path / check() unchanged / layer field type

## 11. prompt reminder 集成

- [ ] 11.1 Modify `baozicode/prompt/reminder.py` `_inject_reminders`
- [ ] 11.2 Accept reminder type `hook_prompt` in addition to env/plan_mode/denial_rate_limit/time_gap/memory_refreshed
- [ ] 11.3 Wrap body in `<system-reminder type="hook_prompt" ttl="sticky">` tags
- [ ] 11.4 Splice hook_prompt sticky reminders into `messages[-2]` (before most recent user message)
- [ ] 11.5 Multiple hook_prompt reminders concatenate in enqueue order
- [ ] 11.6 Sticky reminder survives across turns until cleared
- [ ] 11.7 Add `Agent.enqueue_reminder(kind="hook_prompt", body, ttl="sticky")` public method
- [ ] 11.8 Hook dispatcher calls enqueue_reminder after prompt action runs
- [ ] 11.9 Slot=stable_system route to `set_dynamic_section("hook_overrides", content)`
- [ ] 11.10 stable_system injection AFTER byte-identical prefix (preserve cache key)
- [ ] 11.11 runtime defensive check: slot=stable_system on tool.pre/post → log error and ignore
- [ ] 11.12 Slot=temp: attach to `agent_state.temp_reminders`, consumed at turn end (one-shot)
- [ ] 11.13 Extend `/clear` to wipe all sticky reminder kinds (env/plan_mode/denial_rate_limit/memory_refreshed/hook_prompt)
- [ ] 11.14 Implement `clear_sticky_reminders` action: clear all sticky at next _inject_reminders
- [ ] 11.15 Implement `clear_stable_system_overrides` action: clear `hook_overrides` section
- [ ] 11.16 Add 14 unit tests: each slot route / ttl sticky vs once / multi reminder concatenate / clear behavior / stable_system persistence

## 12. 集成测试 + 回归

- [ ] 12.1 Run full v1.0 test suite — expect 100% pass
- [ ] 12.2 Add session.start hook integration test (audit log written, no main-flow impact)
- [ ] 12.3 Add tool.pre hook integration test (deny Bash, LLM receives reason, adjusts next call)
- [ ] 12.4 Add tool.post hook integration test (audit written for every tool_call including denied ones)
- [ ] 12.5 Add prompt action injection test (sticky reminder reaches LLM next turn)
- [ ] 12.6 Add async post hook test (does not block next LLM call)
- [ ] 12.7 Add L1 priority test (blacklist denies even when hook.allow would have allowed)
- [ ] 12.8 Add hook exception test (Agent continues, log.warning only)
- [ ] 12.9 Add bad config test (HookValidationError → SystemExit)
- [ ] 12.10 Add no-hooks-block test (config without `hooks:` produces byte-identical v1.0 behavior)
- [ ] 12.11 Performance smoke test: pre hook N=3, each shell 30ms, total overhead < 150ms per tool_call
- [ ] 12.12 audit.log rotation test: 100MB threshold triggers rotate
- [ ] 12.13 LLM cache test: stable_system slot override does not break byte-identical prefix
- [ ] 12.14 TUI test: ToolResult rendered with color per execution_status (L1 red / hook_pre yellow / L2-L5 orange / success green / failed red)

## 13. 文档更新

- [ ] 13.1 Update `config.example.yaml` with `hooks:` block (3 sample rules)
- [ ] 13.2 Add sample 1: `audit-bash-pre` (tool.pre + shell + exit_code deny)
- [ ] 13.3 Add sample 2: `inject-style` (turn.start + prompt + sticky_reminder)
- [ ] 13.4 Add sample 3: `audit-bash-post` (tool.post + async + shell + enqueue=false)
- [ ] 13.5 Each sample with inline comments explaining event / if / actions / fields
- [ ] 13.6 Add `README.md` section "Hooks 系统"
- [ ] 13.7 Document 4-layer lifecycle event list
- [ ] 13.8 Document 4 action types and their fields
- [ ] 13.9 Document 4 matchers + all/any composition
- [ ] 13.10 Document pipeline diagram (L1 → hook.pre → L2-L5 → execute → hook.post)
- [ ] 13.11 Document ToolResult new fields table
- [ ] 13.12 Document failure isolation + audit log location
- [ ] 13.13 Provide 3 user-scenario examples (audit / risk check / style reminder)
- [ ] 13.14 Archive change: move v1-1-hooks-lifecycle to openspec/changes/archive/2026-07-09-v1-1-hooks-lifecycle/
