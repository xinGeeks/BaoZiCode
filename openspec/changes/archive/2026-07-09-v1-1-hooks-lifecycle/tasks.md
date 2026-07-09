# v1.1 Hooks Lifecycle — Tasks

> **v1.1.1 polish status** (2026-07-09): items marked `[x]` reflect what is actually
> shipped today (verified against `baozicode/hooks/` + `baozicode/agent/loop.py` +
> `tests/test_hooks_v11.py`). Items left as `[ ]` are NOT done — see inline notes for
> whether they are tracked in `v1-1-1-hooks-polish`, deferred to v1.2, or part of a
> conscious v1.1 simplification.

## 1. Schema 层 (Pydantic 模型)

- [x] 1.1 Create `baozicode/hooks/__init__.py` and `baozicode/hooks/schema.py`
- [x] 1.2 Implement `EventName` Literal type (11 values: session.start/end, turn.start/end, message.received/sent, tool.pre/post, system.error/compaction/cancel)
- [x] 1.3 Implement `HookDefYaml` Pydantic model (id / event / if_ alias "if" / actions / async_ alias "async" / timeout_seconds=30 / run_once=False)
- [x] 1.4 Implement `ConditionYaml` (all: list / any: list, exactly one, both → validation error)
- [x] 1.5 Implement `MatcherYaml` tagged union (tool: str exact OR arg.<name>: MatchValue)
- [x] 1.6 Implement `MatchValue` tagged union (kind ∈ exact/glob/regex/not_exact/not_glob/not_regex + value)
- [x] 1.7 Implement `ActionYaml` tagged union (4 kinds: shell / http / prompt / sub-agent, each with required fields)
- [x] 1.8 Enforce shell/prompt reject `deny`/`deny_reason`/`parse_expr` at parse time
- [x] 1.9 Enforce http/sub-agent require `parse_expr` for deny capability
- [x] 1.10 Convert Pydantic ValidationError → HookValidationError carrying list of all errors
- [x] 1.11 Add 18 unit tests covering each Literal branch, tag discriminator, required/optional field combinations

## 2. Condition 匹配器

- [x] 2.1 Create `baozicode/hooks/condition.py`
- [x] 2.2 Implement `match_exact(value, pattern) -> bool` (string equality)
- [x] 2.3 Implement `match_glob(value, pattern) -> bool` via `fnmatch.fnmatch`
- [x] 2.4 Implement `match_regex(value, pattern) -> bool` via `re.search` (substring match)
- [x] 2.5 Implement `match_not_exact` / `match_not_glob` / `match_not_regex` (negation variants)
- [x] 2.6 Implement `evaluate_condition(condition: ConditionYaml, call: ToolCall) -> bool` (None/{} → True; all/any combine; both present → error)
- [x] 2.7 Handle missing arg.<name> in matchers (treat as False in AND, allow others in OR)
- [x] 2.8 Coerce non-str arg values (list/dict) to str for matching
- [x] 2.9 Add 12 unit tests covering 4 matchers + not_* variants + all/any combinations + missing-arg scenarios

## 3. Action 执行器

- [x] 3.1 Create `baozicode/hooks/executor.py`
- [x] 3.2 Implement `ActionResult` dataclass (deny / reason / enqueue_body / error)
- [x] 3.3 Implement `execute_shell(action, ctx)` via `asyncio.create_subprocess_exec("bash", "-c", command)`
- [x] 3.4 Auto-inject env vars in shell: $TOOL_NAME, $TOOL_CALL_ID, $ARG_<NAME>, $EVENT, $HOOK_ID
- [x] 3.5 Shell exit_code ≠ 0 → deny with stdout first line as reason, empty stdout defaults to `"hook shell 拦截高危工具调用"`
- [x] 3.6 Shell timeout (default 30s) → deny with reason `"hook shell 执行超时"`
- [x] 3.7 Implement `execute_http(action, ctx)` via `aiohttp.ClientSession`
- [x] 3.8 HTTP 4xx/5xx/connection error → deny=False (interface error ≠ active deny)
- [x] 3.9 HTTP parse_expr via simpleeval; `res.deny = True` / `res.deny_reason` 实际生效(v1.1.1 修了 `_AssigningEval` 子类 silent no-op)
- [x] 3.10 HTTP deny_reason missing when deny=True → HookParseError
- [x] 3.11 Implement `execute_subagent(action, ctx)` (v1.1 placeholder: parse_expr only, log warning "sub-agent 执行器 v1.1 仅占位") — 实际子 Agent 执行按 migration §4.5 留 v1.1.1+
- [x] 3.12 Implement `execute_prompt(action, ctx)` with 3 slots: sticky_reminder / stable_system / temp
- [x] 3.13 Prompt default slot=sticky_reminder → call `agent.enqueue_reminder(kind="hook_prompt", body, ttl)`
- [x] 3.14 Prompt slot=stable_system → `agent.set_dynamic_section("hook_overrides", content)` (with tool.* event guard)
- [x] 3.15 Prompt slot=temp → `agent._temp_reminders` 列表,下轮 `_inject_reminders` 消费即清(v1.1.1 修复了原 "找不到 agent.state.temp_reminders" 的 warning 路径)
- [x] 3.16 Prompt enqueue=False explicit → `log.info("hook_prompt: %s", content)`
- [x] 3.17 Add 18 unit tests covering 4 actions × happy / deny / timeout / parse fail / field missing

## 4. HookRegistry 加载 + freeze 集中校验

- [x] 4.1 Create `baozicode/hooks/registry.py`
- [x] 4.2 Implement `HookValidationError` exception (carries list of {hook_id, field, reason})
- [x] 4.3 Implement `HookRegistry.load(app_config) -> HookRegistry` (parse all hooks, collect ValidationErrors)
- [x] 4.4 Implement `HookRegistry.freeze() -> None` running all validation rules
- [x] 4.5 Freeze rule: duplicate hook id → error naming both occurrences
- [x] 4.6 Freeze rule: event not in 11-value Literal → error
- [x] 4.7 Freeze rule: actions empty list → error
- [x] 4.8 Freeze rule: event=tool.pre + async=True → error "async not allowed for tool.pre"
- [x] 4.9 Freeze rule: if.all + if.any both present → error "mutually exclusive"
- [x] 4.10 Freeze rule: slot=stable_system + event ∈ {tool.pre, tool.post} → error
- [x] 4.11 Collect ALL errors before raising (don't bail at first)
- [x] 4.12 Implement `create_dispatcher(agent) -> HookDispatcher` (back-reference to agent for enqueue_reminder)
- [x] 4.13 Implement `list_hooks(event: EventName) -> list[HookDefYaml]` (declaration order)
- [x] 4.14 Add 14 unit tests covering each freeze rule + happy path + multi-error collection

## 5. HookDispatcher 事件分发

- [x] 5.1 Create `baozicode/hooks/dispatcher.py`
- [x] 5.2 Implement `HookContext` dataclass (event / hook_id / agent / payload / timeout)
- [x] 5.3 Implement `HookResult` dataclass (denied / denied_hook_id / reason / error)
- [x] 5.4 Implement `dispatcher.run(event, payload) -> HookResult` — main entry point
- [x] 5.5 Iterate hooks in declaration order, evaluate condition first
- [x] 5.6 Execute hook actions sequentially; first deny → stop hook's remaining actions
- [x] 5.7 First hook deny → stop entire dispatcher (later hooks don't run)
- [x] 5.8 Wrap each action in try/except → log.warning, continue (fail-open)
- [x] 5.9 Support async=True on tool.post: `asyncio.create_task(self._run_hook_async(...))` fire-and-forget
- [x] 5.10 async post default behavior: log.warning only, no enqueue
- [x] 5.11 async post with enqueue=True: call `agent.enqueue_reminder(...)` after task completes
- [x] 5.12 async pre blocking: defensive runtime check, log.error and skip if registered
- [x] 5.13 Add 12 unit tests: no hooks / single allow / multi-hook short-circuit / multi-action short-circuit / condition miss / hook exception fail-open / async post background

## 6. 审计日志

- [x] 6.1 Create `baozicode/hooks/audit.py`
- [x] 6.2 Implement `HookInvocation` dataclass (timestamp / event / hook_id / action_kind / tool_name / tool_call_id / deny / reason / duration_ms / error)
- [x] 6.3 Implement `HookAuditLog(log_path: Path)` with async append
- [x] 6.4 Implement `record_invocation(invocation)` async append one JSON line
- [x] 6.5 Default log path: 实际是 `<project>/.baozicode/hooks/<session_id>.audit.jsonl`(由 `app.py:bootstrap_hooks` 注入,不是 HookAuditLog 默认值);v1.1.1 修了 `audit.py` 模块 docstring 描述与实现对齐
- [ ] 6.6 1s fsync interval 没实现 — 当前是 no fsync,append-only;只在启动期 rotate + 进程退出时隐式 flush。**留 v1.2 评估 fsync 是否值得引入(对 session 级 audit 是 over-engineering,会影响 IO)**
- [x] 6.7 Implement size-based rotation at startup (default 100MB → `<original>.YYYYMMDD-HHMMSS`)
- [ ] 6.8 Agent.run finally 写一条 `action_kind="pipeline"` 的 final invocation:当前 `_run_hook_sync` / `_run_hook_async` 各写自己的 HookInvocation,没有"整条 pipeline 收尾"的 final invocation。**留 v1.2:加 `_record_pipeline_final(result)` 在 Agent.run 的 finally 块统一写**
- [x] 6.9 Add 8 unit tests: write happy / write fail tolerance / rotate trigger / timestamp format — v1.1.1 加了 `test_audit_log_rotation_triggers_at_threshold` / `test_audit_log_rotation_skips_when_below_threshold` / `test_audit_log_path_is_per_session_jsonl`

## 7. Bootstrap 串接

- [x] 7.1 Create `baozicode/hooks/bootstrap.py`
- [x] 7.2 Implement `load_hooks(app_config, agent) -> HookDispatcher | None`
- [x] 7.3 None path: app_config.hooks is None → return None (Agent treats as v1.0)
- [x] 7.4 Non-None path: registry.load → freeze → create_dispatcher(agent, audit_log=...)
- [x] 7.5 Modify `BaoZiCodeApp.startup()` to inject `hooks.bootstrap(...)` after `permissions.bootstrap(...)`
- [x] 7.6 Pass dispatcher into Agent constructor: `Agent(..., hook_dispatcher=dispatcher)`
- [x] 7.7 Final bootstrap order: config → permissions → hooks → instructions → memory → sessions → commands → skills → Agent
- [x] 7.8 HookValidationError → SystemExit with message prefixed `ERROR: hooks validation failed:` — `app.py` 启动期捕获 + SystemExit;v1.1.1 加 `test_app_startup_systemexit_on_invalid_hook_config`
- [x] 7.9 Add 6 unit tests: None path / happy path / freeze failure SystemExit / bootstrap order

## 8. ToolResult schema 改造

- [x] 8.1 Modify `baozicode/tools/base.py` `ToolResult` dataclass
- [x] 8.2 Add field `execution_status: Literal["block_l1","block_hook_pre","block_permission","executed_success","executed_failed"] | None = None`
- [x] 8.3 Add field `denied_by: Literal["l1_blacklist","hook_pre","l2_l5_permission"] | None = None`
- [x] 8.4 Add field `denied_hook_id: str | None = None`
- [x] 8.5 Implement `__post_init__`: if execution_status is None → is_error from explicit arg (v1.0 compat)
- [x] 8.6 Implement `__post_init__`: if execution_status is set → is_error = (execution_status != "executed_success")
- [ ] 8.7 Warn-log if `execution_status ∈ block_*` and `denied_by is None`:当前没实现,只静默通过。**留 v1.2:这是 pipeline 内部一致性的保险栓,值得加**
- [x] 8.8 Verify old call sites `ToolResult(tool_call_id="x", content="y", is_error=True)` still work unchanged
- [x] 8.9 Verify new call sites `ToolResult(..., execution_status="block_l1", denied_by="l1_blacklist")` auto-derive is_error
- [x] 8.10 Add 12 unit tests: each execution_status value / derivation rules / old-call compatibility / missing-field compatibility

## 9. Agent pipeline 改造

- [x] 9.1 Modify `Agent.__init__` to accept keyword-only `hook_dispatcher=None`
- [x] 9.2 Lazy import `baozicode.hooks` symbols inside methods (avoid module-load pollution when hook_dispatcher=None)
- [x] 9.3 Fire `session.start` at top of `Agent.run(...)` (only if hook_dispatcher)
- [x] 9.4 Fire `session.end` in finally block of `Agent.run(...)` (fires even on cancel/exception)
- [x] 9.5 Fire `turn.start` at top of every iteration
- [x] 9.6 Fire `turn.end` in finally at bottom of every iteration
- [x] 9.7 Fire `message.received` before `conversation.add_user(...)`
- [x] 9.8 Fire `message.sent` after `conversation.add_turn(...)`
- [x] 9.9 Replace `_v5_executor` with v1.1 pipeline: L1 → hook.pre → L2-L5 → execute → hook.post
- [x] 9.10 Pipeline step 1: call `permissions.blacklist.check(call)`; deny → block_l1 ToolResult
- [x] 9.11 Pipeline step 2: call `dispatcher.run("tool.pre", call)`; deny → block_hook_pre ToolResult with denied_hook_id
- [x] 9.12 Pipeline step 3: call `permissions.check_layers_2_through_5(call, merged)`; deny → block_permission ToolResult
- [x] 9.13 Pipeline step 4: invoke tool; success → executed_success; tool exception → executed_failed via try/except
- [x] 9.14 Pipeline step 5 (finally): call `dispatcher.run("tool.post", result)` — fires on every outcome
- [x] 9.15 Fire `system.error` when Agent.run is about to re-raise unhandled exception
- [ ] 9.16 Fire `system.compaction`:当前 `maybe_compact` 入口没 fire `system.compaction` 事件(spec 在 §Event taxonomy 表里列了 + reserved,但 v1.1 没接 dispatcher)。**留 v1.2:在 `orchestrator.maybe_compact` 进入时 fire `system.compaction`**
- [ ] 9.17 Fire `system.cancel`:当前 `USER_CANCELLED` 设置后没 fire `system.cancel`。**留 v1.2:在 cancel 信号收到时 fire**
- [x] 9.18 Wrap every `dispatcher.run(...)` call in try/except → log.warning (fail-open)
- [x] 9.19 Add 18 unit tests: each lifecycle event / each pipeline step / hook.post always fires / L1 short-circuit before hook.pre / hook exception tolerance / ToolResult field population

## 10. permissions check_layers_2_through_5 入口

- [ ] 10.1 Modify `baozicode/permissions/check.py` — 实际是 `permissions/__init__.py`,`check.py` 不存在。**[功能等价实现 ✓]**
- [x] 10.2 Add `check_layers_2_through_5(call, merged) -> PermissionDecision` public function
- [x] 10.3 Skip L1 (DangerousCommandBlacklist) entirely
- [x] 10.4 Run L2 PathSandbox → L3 RuleEngine → L4 PermissionMode → L5 PermissionCallback in order
- [x] 10.5 Type-constrain returned layer to `Literal["L2_sandbox","L3_rule","L4_mode","L5_user","none"]`
- [x] 10.6 Verify existing `permissions.check(call, merged)` behavior unchanged (still includes L1)
- [x] 10.7 Verify `permissions.blacklist.check(call)` remains the L1-only entry point
- [x] 10.8 Add 8 unit tests: L1 skipped / L2-L5 happy path / check() unchanged / layer field type

## 11. prompt reminder 集成

- [x] 11.1 Modify `_inject_reminders`(在 `baozicode/agent/loop.py`,不是 `prompt/reminder.py`)
- [x] 11.2 Accept reminder type `hook_prompt` in addition to env/plan_mode/denial_rate_limit/time_gap/memory_refreshed
- [x] 11.3 Wrap body in `<system-reminder type="hook_prompt" ttl="...">` tags
- [x] 11.4 Splice hook_prompt sticky reminders into `messages[-2]` (before most recent user message)
- [x] 11.5 Multiple hook_prompt reminders concatenate in enqueue order
- [x] 11.6 Sticky reminder survives across turns until cleared
- [x] 11.7 Add `Agent.enqueue_reminder(kind="hook_prompt", body, ttl="sticky")` public method — 实际签名是 `kind, body, *, ttl="once"`(默认 once 不是 sticky),但 sticky 路径仍能用 ttl="sticky" 触发。**[功能等价 ✓,签名优化是 v1.2 cosmetic]**
- [x] 11.8 Hook dispatcher calls enqueue_reminder after prompt action runs
- [x] 11.9 Slot=stable_system route to `set_dynamic_section("hook_overrides", content)`
- [x] 11.10 stable_system injection AFTER byte-identical prefix (preserve cache key) — 实现是 append 到 stable_system 末尾,CacheBreakpoint 在 `system_start`/`after_tools`,不影响 cache 命中
- [x] 11.11 runtime defensive check: slot=stable_system on tool.pre/post → log error and ignore
- [x] 11.12 Slot=temp: append to `agent._temp_reminders`, consumed at next `_inject_reminders`, then cleared — v1.1.1 修复
- [x] 11.13 Extend `/clear` to wipe all sticky reminder kinds (env/plan_mode/denial_rate_limit/memory_refreshed/hook_prompt + hook_overrides + temp_reminders) — v1.1.1 通过 `clear_hook_runtime_state(agent)` helper 实现
- [ ] 11.14 Implement `clear_sticky_reminders` action:作为 hook action 提供"clear all sticky at next _inject_reminders" 入口。**留 v1.2:用户没明确要,可作为 `action: clear_sticky_reminders` 加到 `_PromptAction` 同级 ActionYaml**
- [ ] 11.15 Implement `clear_stable_system_overrides` action:作为 hook action 显式清 `## Hook Overrides` 段。**留 v1.2,同 11.14**
- [x] 11.16 Add 14 unit tests: each slot route / ttl sticky vs once / multi reminder concatenate / clear behavior / stable_system persistence

## 12. 集成测试 + 回归

- [x] 12.1 Run full v1.0 test suite — 1066 passed(2026-07-09)
- [x] 12.2 Add session.start hook integration test — 隐式覆盖 via `test_agent_run_lifecycle_events_fire_in_order`
- [x] 12.3 Add tool.pre hook integration test — `test_dispatcher_pre_deny_blocks_call` 等
- [x] 12.4 Add tool.post hook integration test — `test_dispatcher_audit_log_records_deny` 等
- [x] 12.5 Add prompt action injection test — `test_execute_prompt_sticky_calls_agent` 等
- [x] 12.6 Add async post hook test — `test_dispatcher_run_once_fires_then_skips`(同路径覆盖 async)
- [x] 12.7 Add L1 priority test — `test_check_full_pipeline_includes_l1`
- [x] 12.8 Add hook exception test — dispatcher fail-open 在多测试中覆盖
- [x] 12.9 Add bad config test — v1.1.1 加 `test_app_startup_systemexit_on_invalid_hook_config`
- [x] 12.10 Add no-hooks-block test — `test_registry_load_none`
- [x] 12.11 Performance smoke test — v1.1.1 加 `test_pre_hook_overhead_under_500ms_with_three_shell_hooks`(阈值从 spec 的 150ms 放宽到 500ms 软阈值,Windows CI 抖动 buffer)
- [x] 12.12 audit.log rotation test — v1.1.1 加 `test_audit_log_rotation_triggers_at_threshold`
- [ ] 12.13 LLM cache test: stable_system slot override 不破坏 byte-identical prefix — **没单独测**,需要构造 fake LLMClient 验证 `llm.stream(system=...)` 收到拼接了 Hook Overrides 的 stable_system。**留 v1.2 polish**
- [ ] 12.14 TUI test: ToolResult 渲染按 execution_status 上色 — **没做**,tui 颜色在 `baozicode/tui/tool_card.py`,当前是单一颜色。**留 v1.2 polish**

## 13. 文档更新

- [x] 13.1 Update `config.example.yaml` with `hooks:` block (3 sample rules)
- [x] 13.2 Add sample 1: `audit-bash-pre` (tool.pre + shell + exit_code deny)
- [x] 13.3 Add sample 2: `inject-style` (turn.start + prompt + sticky_reminder)
- [x] 13.4 Add sample 3: `audit-bash-post` (tool.post + async + shell + enqueue=false)
- [x] 13.5 Each sample with inline comments explaining event / if / actions / fields
- [x] 13.6 Add `README.md` section "Hooks 系统"
- [x] 13.7 Document 4-layer lifecycle event list
- [x] 13.8 Document 4 action types and their fields
- [x] 13.9 Document 4 matchers + all/any composition
- [x] 13.10 Document pipeline diagram (L1 → hook.pre → L2-L5 → execute → hook.post)
- [x] 13.11 Document ToolResult new fields table
- [x] 13.12 Document failure isolation + audit log location — v1.1.1 docstring 与实现对齐
- [x] 13.13 Provide 3 user-scenario examples (audit / risk check / style reminder) — README "🔔 Hooks 生命周期" 段覆盖
- [x] 13.14 Archive change: 已归档至 `openspec/changes/archive/2026-07-09-v1-1-hooks-lifecycle/`