# permissions Specification (v1.1 deltas)

## Purpose
Modifications to the v0.5 five-layer permission defense pipeline to make room for the
v1.1 hook.pre layer between L1 and L2-L5, and to clarify the agent's responsibility
for L1 evaluation. The internal L1-L5 logic of `permissions.check()` is unchanged; the
v1.1 delta only specifies how the Agent wraps it.

## MODIFIED Requirements

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

## ADDED Requirements

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