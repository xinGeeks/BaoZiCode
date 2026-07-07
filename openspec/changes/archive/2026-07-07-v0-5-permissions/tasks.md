## 1. Module skeleton

- [x] 1.1 Create `baozicode/permissions/` package with `__init__.py` re-exporting public API
- [x] 1.2 Implement `baozicode/permissions/types.py` — `PermissionDecision`, `PermissionRule`, `RuleLayer`, `PermissionMode` dataclasses/literals; verify dataclass instantiation in a scratch test
- [x] 1.3 Add `baozicode/permissions/__init__.py` with stub `check(call, ctx) -> PermissionDecision` that returns `Decision(layer="none", decision="fallthrough")` so module imports cleanly

## 2. L1 DangerousCommandBlacklist

- [x] 2.1 Implement `baozicode/permissions/blacklist.py` — `TEXT_PATTERNS: list[re.Pattern]` constant covering rm -rf /, sudo rm/chmod/chown, chmod -R 7, dd if=..., mkfs, curl|sh, wget|sh, fork bomb, mv / /dev/null, /etc/passwd, /etc/shadow, ~/.ssh/authorized_keys, ~/.bashrc, **`bash\s+-c` / `sh\s+-c` 任何形式**(整条拒,不解析内部字符串)
- [x] 2.2 Add `DANGEROUS_TOKENS: set[str]` and `SUSPICIOUS_KEYWORDS: set[str]` constants in the same file
- [x] 2.3 Implement `DangerousCommandBlacklist.check(call) -> PermissionDecision` with text_scan → token_scan (shlex.split) two-layer logic
- [x] 2.4 Write `tests/test_permissions_blacklist.py` covering: rm -rf /, sudo rm, chmod 777, fork bomb, curl|sh, write /etc/passwd, safe commands pass through, echo of dangerous string is denied

## 3. L2 PathSandbox + ToolDefinition.path_args

- [x] 3.1 Add `path_args: list[str] = field(default_factory=list)` field to `ToolDefinition` in `baozicode/tools/base.py`
- [x] 3.2 Set `path_args=["file_path"]` in Read/Write/Edit `ToolDefinition` instances
- [x] 3.3 Set `path_args=[]` in Bash/Read/Grep/Glob/WebFetch as appropriate
- [x] 3.4 Implement `baozicode/permissions/sandbox.py` — `PathSandbox` class with `real_root: Path` resolved at construction; `is_inside(path) -> bool` using `Path.resolve().is_relative_to(real_root)`
- [x] 3.5 Implement `PathSandbox.check(call) -> PermissionDecision` — Read/Write/Edit check `file_path`; Bash extracts path literals via conservative regex `(?:^|[\s|&;>])(~?/[^\s|&;>\']+|\.{0,2}/[^\s|&;>\']+)`, denies on shell expansion markers ($VAR / ${VAR} / ~ / `cmd`)
- [x] 3.6 Write `tests/test_permissions_sandbox.py` covering: project-relative path allowed, /etc/passwd denied, .. traversal denied, symlink escape denied, $VAR expansion denied, multiple paths with one escape denied

## 4. L3 RuleEngine + three-layer YAML loader

- [x] 4.1 Implement `baozicode/permissions/persistence.py` — atomic write to `permissions.local.yaml` (read → modify → write to tmp → os.replace); dedup by `(tool, pattern, decision)` tuple
- [x] 4.2 Implement `baozicode/permissions/loader.py` — `load_permissions_layers(project_root) -> MergedPermissions` searching the three paths in priority order, parsing each with Pydantic schema, collecting `PermissionRule`s tagged with `RuleLayer`
- [x] 4.3 Add Pydantic schemas `PermissionRule` and `PermissionsV5` to `baozicode/config/schema.py`; add `permissions_v5: PermissionsV5 | None = None` field to `AppConfig`
- [x] 4.4 Implement `baozicode/permissions/engine.py` — `RuleEngine` class with `session_rules: list[PermissionRule]` (in-memory), `merged_rules: list[tuple[RuleLayer, PermissionRule]]` (from loader); `add_session_rule(rule)` and `check(call) -> PermissionDecision` methods
- [x] 4.5 Implement the evaluation algorithm in `RuleEngine.check`: iterate layers session → local → project → user-global, scan rules in declaration order, deny short-circuits immediately, allow is recorded as candidate and continues scanning for any deny
- [x] 4.6 Write `tests/test_permissions_engine.py` covering: layer merge priority, deny short-circuits over lower-layer allow, allow closest-layer wins, missing files skipped silently, malformed YAML skipped with warning

## 5. L4 PermissionMode

- [x] 5.1 Implement `baozicode/permissions/mode.py` — `PermissionMode` enum (strict/default/permissive); `apply(decision_so_far, mode) -> PermissionDecision` that maps fallthrough to DENY (strict) / unchanged (default) / ALLOW (permissive)
- [x] 5.2 Wire session-level mode override into `Agent.__init__` via a new optional parameter (default = YAML-declared mode); mode change via `/permissions mode` updates `App.session_mode` and takes effect only on the next `Agent` constructed for the next user message; running Agent keeps its captured mode
- [x] 5.3 Write `tests/test_permissions_mode.py` covering: strict denies without modal, permissive auto-allows, default falls through

## 6. L5 PermissionModal upgrade

- [x] 6.1 Refactor `baozicode/tui/permission_modal.py` — change `dismiss` return type to `PermissionChoice` enum (ONCE / SESSION / PERSISTENT / DENY); button group becomes `[Y 仅本次] [A 本会话] [P 永久] [N 拒绝]`; add title suffix "(previously denied)" when `previously_denied=True`
- [x] 6.2 Update `ChatScreen._permission_callback` in `baozicode/tui/chat_screen.py` — handle `PermissionChoice.SESSION` by calling `engine.add_session_rule(rule)`; handle `PERSISTENT` by calling `persistence.append_rule_to_local_yaml(rule)`; for both, derive the pattern as **glob `first_token *`** (e.g., `npm test --coverage` → `npm test *`), NOT the exact full argument string; display the derived pattern in an info card so the user can verify what was granted
- [x] 6.3 Add `previously_denied` tracking to `ChatScreen` — keyed by `(tool_name, json.dumps(arguments, sort_keys=True))`; reset on session end

## 7. Pipeline integration into Agent Loop

- [x] 7.1 Implement `baozicode/permissions/__init__.py` `check(call, ctx) -> PermissionDecision` orchestrator that runs L1 → L2 → L3 → L4 → L5 in order and short-circuits
- [x] 7.2 Refactor `baozicode/agent/loop.py` executor closure — replace `_matches_deny` and `_is_auto_allowed` with a single `permissions.check(call)` call; on deny, synthesize `ToolResult(is_error=True, content=decision.reason)` and skip `execute_tool_call`
- [x] 7.3 Refactor `baozicode/agent/guards.py` — rename `record_denial` → `record_denial_warn`, replace `check_deny_threshold` → `should_inject_denial_reminder(tool_name, threshold) -> bool`; remove `DENIALS_EXCEEDED` from active termination paths but keep the enum value
- [x] 7.4 Add `denial_warn_threshold: int = 5` field to `AgentConfig` in `baozicode/config/schema.py`
- [x] 7.5 Wire `PlanModeReminder` injection in `Agent._inject_reminders` to also inject `<system-reminder type="denial_rate_limit">` when `should_inject_denial_reminder` returns true
- [x] 7.6 Remove `DENIALS_EXCEEDED` from `Agent.run()` termination switch in `loop.py`; keep import for backward compatibility

## 8. TUI integration

- [x] 8.1 Add `/permissions mode` slash command to `ChatScreen` — register in `SLASH_COMMANDS`; implement `_handle_permissions_mode()` to open a 3-option `ModeSelectScreen` modal
- [x] 8.2 Update `ChatScreen._show_permissions()` to display: current mode, three-layer YAML source paths (presence/absence), session rule count, top 10 rules grouped by tool, deny rule count
- [x] 8.3 Update `ChatScreen._show_status()` to display: current mode, `denial_warn_threshold`, current session consecutive denial count, session rule count
- [x] 8.4 Update `ChatScreen._update_status_bar()` to append mode segment (e.g., `default` after `auto`)
- [x] 8.5 Implement `ChatScreen._show_denial_for_retry(call)` — render a 🔧 card with "(previously denied)" suffix when LLM retries a denied call with same arguments

## 9. App startup + loader wiring

- [x] 9.1 In `baozicode/app.py`, add `permissions_v5: MergedPermissions | None` attribute initialized at startup
- [x] 9.2 Call `permissions.bootstrap(project_root, config)` in `BaoZiCodeApp.__init__` — resolves `real_root`, loads three-layer YAML, builds `RuleEngine`, returns `MergedPermissions`
- [x] 9.3 Pass `permissions_v5` into `Agent.__init__` constructor (new optional parameter) so executor closure can call `permissions.check`
- [x] 9.4 Update `baozicode/config/loader.py` — search for `permissions*.yaml` files alongside the main config search; merge into `AppConfig.permissions_v5`

## 10. Configuration examples + documentation

- [x] 10.1 Update `config.example.yaml` — add `permissions_v5: {mode: default, rules: [...]}` block with 5-10 example rules covering allow (git, npm test) and deny (rm, *.env)
- [x] 10.2 Update `README.md` — add a "Permissions" section explaining three-layer YAML paths, rule syntax, mode semantics, and the L1 hard blacklist caveat; recommend `.baozicode/permissions.local.yaml` in `.gitignore`
- [x] 10.3 Update `CLAUDE.md` — add `baozicode/permissions/` to module structure tree; add dependency direction note `permissions/ → config/ + tools/base.py`; add key-contracts section describing the five-layer pipeline and `PermissionDecision` schema
- [x] 10.4 Add `tests/test_permissions_persistence.py` — write/read/delete `permissions.local.yaml` roundtrip; atomic write doesn't corrupt on simulated crash; dedup correctly handles repeated additions

## 11. Integration tests + verification

- [x] 11.1 Write `tests/test_permissions_integration.py` — full Agent.run() end-to-end test with a mocked LLM that emits: (a) Bash(rm -rf /) → DENY by L1, is_error fed back, loop continues; (b) Bash(git status) → ALLOW by L3 rule, executes; (c) Bash with no matching rule and default mode → modal invoked, deny chosen, loop continues; (d) same call retried → modal re-shown with "(previously denied)"
- [x] 11.2 Write `tests/test_permissions_back_compat.py` — old `config.yaml:permissions: {auto_allow: [Read], deny: [Bash*]}` still works when no new YAML exists; deprecation warning logged when both old and new config coexist
- [x] 11.3 Run full test suite (`pytest`) and verify all existing v0.1-v0.4 tests still pass (zero regressions) — 344/344 passed
- [ ] 11.4 Manually launch BaoZiCode TUI in a test project, run through: `/permissions mode permissive` → verify status bar updates; trigger L1 deny → verify ✗ card + loop continues; select "Allow permanently" → verify `permissions.local.yaml` is created with the rule; restart and verify rule still applies
- [x] 11.5 Update `.gitignore` template (or add a snippet to README) recommending `.baozicode/permissions.local.yaml`