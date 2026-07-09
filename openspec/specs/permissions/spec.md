# permissions Specification

## Purpose
TBD - created by archiving change v0-5-permissions. Update Purpose after archive.
## Requirements
### Requirement: Five-layer permission defense pipeline
The system MUST evaluate every `ToolCall` through a five-layer permission pipeline in fixed order: `L1 DangerousCommandBlacklist` → `L2 PathSandbox` → `L3 RuleEngine` → `L4 PermissionMode` → `L5 PermissionCallback`. The pipeline MUST short-circuit at the first layer that returns an explicit ALLOW or DENY decision. If no layer returns a decision, the pipeline MUST default to DENY.

#### Scenario: Layer 1 short-circuits with DENY
- **WHEN** L1 returns DENY for a `Bash` call with command `rm -rf /`
- **THEN** L2, L3, L4, L5 MUST NOT be evaluated
- **AND** the final decision returned by the pipeline MUST be DENY with `layer="L1_blacklist"`

#### Scenario: Layer 3 short-circuits with ALLOW
- **WHEN** L1 returns fallthrough, L2 returns fallthrough, L3 finds a matching rule with decision=allow
- **THEN** L4 and L5 MUST NOT be evaluated
- **AND** the final decision MUST be ALLOW with `layer="L3_rule"`

#### Scenario: All layers fallthrough defaults to DENY
- **WHEN** all five layers return fallthrough (no rules match, mode is not permissive, no user callback)
- **THEN** the pipeline MUST return DENY with `layer="none"`

### Requirement: PermissionDecision data structure
The system MUST represent the result of a permission check as a `PermissionDecision` dataclass with fields `decision: Literal["allow","deny","fallthrough"]`, `layer: Literal["L1_blacklist","L2_sandbox","L3_rule","L4_mode","L5_user","none"]`, `reason: str` (human-readable explanation, also fed back to LLM on DENY), `matched_pattern: str | None` (the rule pattern or regex that triggered the decision), and `scope: Literal["once","session","persistent"]` (default "once").

#### Scenario: PermissionDecision on deny has reason text
- **WHEN** a `Bash` call is denied by L1
- **THEN** the returned `PermissionDecision` MUST have `decision="deny"`, `layer="L1_blacklist"`, and `reason` containing the blacklist pattern name that matched

#### Scenario: PermissionDecision on allow has matched pattern
- **WHEN** a `Bash` call is allowed by a rule `Bash(git *)`
- **THEN** the returned `PermissionDecision` MUST have `decision="allow"`, `layer="L3_rule"`, and `matched_pattern="git *"`

### Requirement: Three-layer YAML configuration merge
The system MUST load permission rules from up to three YAML files in order of ascending priority: (1) `~/.config/baozicode/permissions.yaml` (user global), (2) `<project_root>/.baozicode/permissions.yaml` (project), (3) `<project_root>/.baozicode/permissions.local.yaml` (local). The system MUST merge rules from all present files into a single rule list, tagged with their source layer.

#### Scenario: Missing local file skips silently
- **WHEN** `<project_root>/.baozicode/permissions.local.yaml` does not exist
- **THEN** the loader MUST NOT raise an error
- **AND** only the user-global and project files contribute rules

#### Scenario: All three files present merge in priority order
- **WHEN** user-global has `Bash(rm *) → deny`, project has `Bash(git *) → allow`, local has `Bash(npm test) → allow`
- **THEN** the merged rule list MUST contain all three rules, each tagged with its source layer

#### Scenario: Malformed YAML file falls back to defaults
- **WHEN** `<project_root>/.baozicode/permissions.yaml` exists but contains invalid YAML syntax
- **THEN** the system MUST print a warning naming the offending file
- **AND** skip that file's rules
- **AND** continue with the remaining layers

### Requirement: Session rules are stored in memory only
The system MUST maintain a per-`Agent` in-memory list of `_session_rules: list[PermissionRule]` populated when the user selects "allow for this session" in the PermissionModal. Session rules MUST be evaluated with the highest priority (above local YAML). Session rules MUST be discarded when the Agent process exits.

#### Scenario: Session rule applies across multiple tool calls
- **WHEN** user allows `Bash(npm test)` for the current session
- **AND** the LLM subsequently calls `Bash` with command `npm test` three times
- **THEN** all three calls MUST be allowed without re-prompting

#### Scenario: Session rule does not persist after restart
- **WHEN** the user exits BaoZiCode and restarts it
- **THEN** the previously added session rule MUST NOT apply
- **AND** the same tool call MUST require confirmation again

### Requirement: Rule matching uses fnmatch glob
The system MUST match a rule's `pattern` field against the tool call's argument values using `fnmatch.fnmatch` (POSIX-style globbing, `*` matches any characters, `?` matches one character). The rule matches when the tool name equals `rule.tool` AND at least one argument's string value fnmatch-matches `rule.pattern`.

#### Scenario: Glob pattern matches git command
- **WHEN** a rule is `Bash(git *)` with decision allow
- **AND** the tool call is `Bash(command="git commit -m 'foo'")`
- **THEN** the rule MUST match

#### Scenario: Glob pattern does not match unrelated command
- **WHEN** a rule is `Bash(git *)` with decision allow
- **AND** the tool call is `Bash(command="npm install")`
- **THEN** the rule MUST NOT match

#### Scenario: Multiple arguments, one matches
- **WHEN** a rule is `Write(*.env)` with decision deny
- **AND** the tool call is `Write(file_path="prod.env", content="KEY=VALUE")`
- **THEN** the rule MUST match (because `file_path` matches `*.env`)

### Requirement: Rule evaluation semantics — deny short-circuits, closest allow wins
The system MUST evaluate rules from highest to lowest priority layer (session → local → project → user-global). Within each layer, rules MUST be evaluated in declaration order. The first matching rule in the highest-priority layer MUST decide: if DENY, the pipeline returns DENY immediately; if ALLOW, the pipeline records this as the candidate ALLOW but continues scanning lower layers for any DENY. After all layers are scanned, if any DENY was found, return DENY; otherwise return the candidate ALLOW (closest to the call); if neither, return fallthrough.

#### Scenario: User-global deny beats project allow
- **WHEN** user-global has `Bash(rm *) → deny` and project has `Bash(rm *) → allow`
- **AND** the tool call is `Bash(command="rm old.txt")`
- **THEN** the pipeline MUST return DENY (project allow does not override user-global deny)

#### Scenario: Local allow beats project allow
- **WHEN** project has `Bash(npm *) → allow` and local has `Bash(npm install) → allow`
- **AND** the tool call is `Bash(command="npm install")`
- **THEN** the pipeline MUST use the local rule's ALLOW (closest layer wins)

#### Scenario: Higher-layer deny short-circuits lower-layer allow
- **WHEN** local has `Write(.env) → deny` and project has `Write(*.env) → allow`
- **AND** the tool call is `Write(file_path="prod.env")`
- **THEN** the pipeline MUST return DENY (deny short-circuits regardless of layer)

### Requirement: Permission mode controls fallthrough behavior
The system MUST support three permission modes: `strict`, `default`, `permissive`. The mode MUST only affect what happens when layers L1-L3 all return fallthrough. In `strict` mode, fallthrough returns DENY without invoking L5. In `default` mode, fallthrough falls through to L5 (PermissionCallback / modal). In `permissive` mode, fallthrough returns ALLOW (the call proceeds if L1 and L2 passed).

#### Scenario: Strict mode skips modal
- **WHEN** mode is `strict` and L1/L2/L3 all return fallthrough
- **THEN** the pipeline MUST return DENY with `layer="L4_mode"`
- **AND** the PermissionModal MUST NOT be displayed

#### Scenario: Default mode falls through to modal
- **WHEN** mode is `default` and L1/L2/L3 all return fallthrough
- **THEN** the pipeline MUST invoke L5 (PermissionCallback)
- **AND** the final decision depends on the callback's return value

#### Scenario: Permissive mode auto-allows on fallthrough
- **WHEN** mode is `permissive` and L1/L2/L3 all return fallthrough
- **THEN** the pipeline MUST return ALLOW with `layer="L4_mode"`
- **AND** the tool MUST proceed to execution

#### Scenario: Mode does not affect L1/L2/L3 results
- **WHEN** L1 returns DENY for a command
- **AND** mode is `permissive`
- **THEN** the pipeline MUST still return DENY (L1 is not overridable by mode)

### Requirement: Permissions module is dependency-clean
The `baozicode/permissions/` module MUST NOT import from `baozicode/agent/`, `baozicode/tui/`, `baozicode/llm/`, `baozicode/conversation/`, or any tool implementation module. The module MAY import from `baozicode/tools/base.py` (for `ToolCall` type) and `baozicode/config/schema.py` (for `PermissionsV5` type).

#### Scenario: Permissions module loads without agent
- **WHEN** `baozicode.permissions.check(call)` is invoked from a unit test without instantiating `Agent`
- **THEN** the import succeeds and the function returns a `PermissionDecision` correctly

### Requirement: Backward compatibility with v0.2 Permissions fields
The system MUST continue to support the v0.2 `Permissions.auto_allow` and `Permissions.deny` fields. When a project has no `<project>/.baozicode/permissions*.yaml` file, the system MUST convert `auto_allow` into session-wide allow rules and `deny` into L3 deny rules, preserving v0.2 behavior.

#### Scenario: Old config without new YAML works
- **WHEN** `config.yaml` contains `permissions: {auto_allow: [Read, Grep], deny: [Bash*]}`
- **AND** no `.baozicode/permissions*.yaml` exists
- **THEN** `Read` and `Grep` MUST be auto-allowed without modal
- **AND** `Bash` calls whose arguments match `Bash*` glob MUST be denied

#### Scenario: New YAML takes precedence over old fields
- **WHEN** both `config.yaml:permissions.auto_allow: [Read]` and `.baozicode/permissions.yaml:rules: [...]` exist
- **THEN** the new YAML rules MUST be used
- **AND** a deprecation warning MUST be logged about `auto_allow` being ignored

## v1.1 deltas

Modifications to the v0.5 five-layer permission defense pipeline to make room for the
v1.1 hook.pre layer between L1 and L2-L5, and to clarify the agent's responsibility
for L1 evaluation. The internal L1-L5 logic of `permissions.check()` is unchanged; the
v1.1 delta only specifies how the Agent wraps it.

### Requirement: Five-layer permission defense pipeline
The system MUST evaluate every `ToolCall` through a five-layer permission pipeline in fixed order: `L1 DangerousCommandBlacklist` → `L2 PathSandbox` → `L3 RuleEngine` → `L4 PermissionMode` → `L5 PermissionCallback`. The pipeline MUST short-circuit at the first layer that returns an explicit ALLOW or DENY decision. If no layer returns a decision, the pipeline MUST default to DENY.

In v1.1, the Agent pipeline wraps `permissions.check()` between L1 and L2-L5: the
Agent invokes `permissions.blacklist.check(call)` first (L1-only, before hook.pre),
then dispatches `tool.pre` hooks, then invokes `permissions.check_layers_2_through_5(call)`
(which runs L2-L5 only). The full Agent pipeline is therefore
`L1 → hook.pre → L2-L5 → execute → hook.post`, with L1 always preceding hook.pre
(hard-wall guarantee).

The L1 black list (`rm -rf /` / `sudo` / `chmod 777` / `dd` / `mkfs` / `curl|sh` /
fork bomb / `/etc/passwd` / `bash -c`) MUST always run before any user-extensible
hook.pre rule, so a malicious or buggy hook.allow can never bypass L1.

#### Scenario: Layer 1 short-circuits with DENY
- **WHEN** L1 returns DENY for a `Bash` call with command `rm -rf /`
- **THEN** L2, L3, L4, L5 MUST NOT be evaluated
- **AND** the final decision returned by the pipeline MUST be DENY with `layer="L1_blacklist"`
- **AND** in v1.1 Agent pipeline, hook.pre MUST NOT fire (L1 short-circuits before hook.pre)

#### Scenario: Layer 3 short-circuits with ALLOW
- **WHEN** L1 returns fallthrough, L2 returns fallthrough, L3 finds a matching rule with decision=allow
- **THEN** L4 and L5 MUST NOT be evaluated
- **AND** the final decision MUST be ALLOW with `layer="L3_rule"`
- **AND** in v1.1 Agent pipeline, this means hook.pre allowed AND L2-L5 found an L3 allow

#### Scenario: All layers fallthrough defaults to DENY
- **WHEN** all five layers return fallthrough (no rules match, mode is not permissive, no user callback)
- **THEN** the pipeline MUST return DENY with `layer="none"`

#### Scenario: v1.1 Agent pipeline order is L1 → hook.pre → L2-L5
- **WHEN** the Agent processes a tool_call
- **THEN** it MUST invoke L1 first via `permissions.blacklist.check(call)`
- **AND** if L1 allows, it MUST dispatch `tool.pre` hooks
- **AND** if hook.pre allows, it MUST invoke `permissions.check_layers_2_through_5(call)`
- **AND** if L2-L5 denies, the Agent returns `ToolResult(execution_status="block_permission", denied_by="l2_l5_permission")`

#### Scenario: L1 is hard-wall and cannot be bypassed by hook.allow
- **WHEN** L1 denies `Bash("rm -rf /")` AND a registered hook.pre rule would have allowed it
- **THEN** the Agent returns `ToolResult(execution_status="block_l1", denied_by="l1_blacklist")`
- **AND** hook.pre is NOT invoked for this call
- **AND** hook.post still fires on the L1-denied result

### Requirement: permissions.check_layers_2_through_5 is a public entry point
The system MUST expose `permissions.check_layers_2_through_5(call: ToolCall, merged: MergedPermissions) -> PermissionDecision` as a public function (alongside the existing
`permissions.check(call, merged)`). This function MUST run only L2 PathSandbox → L3
RuleEngine → L4 PermissionMode → L5 PermissionCallback, skipping L1. It MUST return
`PermissionDecision` with `layer` constrained to `Literal["L2_sandbox", "L3_rule",
"L4_mode", "L5_user", "none"]` (never `L1_blacklist`).

The existing `permissions.check(call, merged)` MUST remain as the canonical "all 5
layers" entry point — it MUST still call L1 first internally, preserving back-compat
for any direct caller outside the Agent.

#### Scenario: check_layers_2_through_5 skips L1
- **WHEN** `Bash("rm -rf /")` is evaluated by `check_layers_2_through_5`
- **THEN** L1 is NOT evaluated (rm -rf / does NOT trigger any L2-L5 deny)
- **AND** the returned `PermissionDecision.layer` is `"none"` (or another L2-L5 layer,
  never `"L1_blacklist"`)
- **NOTE**: this means the Agent MUST always invoke L1 separately before calling
  `check_layers_2_through_5`; relying on this entry point alone is unsafe

#### Scenario: check still includes L1
- **WHEN** `Bash("rm -rf /")` is evaluated by the existing `permissions.check()`
- **THEN** L1 fires and returns DENY
- **AND** the returned `PermissionDecision` has `layer="L1_blacklist"`
- **AND** L2-L5 are NOT evaluated (L1 short-circuits)

#### Scenario: v1.1 Agent uses check_layers_2_through_5, not check
- **WHEN** the Agent reaches step 3 of `_v5_executor` (after hook.pre allowed)
- **THEN** it calls `permissions.check_layers_2_through_5(call, self._merged)`
- **AND** it does NOT call `permissions.check()` (which would re-run L1)
