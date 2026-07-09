## MODIFIED Requirements

### Requirement: independent mode — sub-Agent delegation via SubAgentManager
In `mode: independent`, when the user invokes `/<name>` (or the LLM invokes
`run_skill(name)`), the Skill body (post-placeholder substitution) is
delegated to `SubAgentManager.dispatch(type="definition", role=skill_name,
prompt=<body>, async_=True)` instead of the v1.0 stub
`IndependentRunner` closure. The sub-Agent runs to completion
(`COMPLETED` / `MAX_ITERATIONS_REACHED` / `FAILED` / `CANCELED`),
and the final assistant text is used as the summary.

The v1.2 sub-Agent is spawned with:

- `role_def` derived from the Skill's name (treated as if a built-in
  `AgentDef` exists with the Skill body as the system prompt identity
  section)
- `tools` derived from the Skill's `allowed-tools` field (if set) plus
  any active Skill whitelist intersection
- `model` and other AgentFrontmatter fields are inherited from the
  Skill's frontmatter (model can be specified on Skills too, from
  v1.0)
- `async_=True` (background) by default — the `/<name>` invocation
  blocks on `await task_info.done_event` until the sub-Agent completes,
  then returns the summary synchronously to the user

The user-visible behavior is identical to v1.0: the user invokes
`/<name>`, the Skill runs, and the summary comes back. The
implementation is now backed by `SubAgentManager` instead of a stub
runner.

#### Scenario: /review via SubAgent
- **WHEN** Skill `review` is active with `mode=independent`,
  `allowed-tools=[Bash, Read, Grep]`, `history-bubbles=3`
- **AND** user types `/review --since="5 轮前"`
- **THEN** `SubAgentManager.dispatch(type="definition", role="review",
  prompt=<body with {since}=5 轮前>, async_=True)` is called
- **AND** the sub-Agent runs with `tools=[Bash, Read, Grep]` and
  `model=<skill.model or inherit>`
- **AND** the sub-Agent runs to completion
- **AND** the final assistant text is returned as
  `SkillExecutionResult.summary`
- **AND** the user sees the summary as if v1.0 ran

#### Scenario: Sub-Agent fails — SkillExecutionResult reflects failure
- **WHEN** Skill `test` is active with `mode=independent`
- **AND** the sub-Agent terminates with `STREAM_ERROR` (LLM call failed)
- **THEN** `SkillExecutionResult` has `ok=False`
- **AND** `summary` contains the error message and `task_id`
- **AND** the user sees a failure notification (not a silent success)

#### Scenario: IndependentRunner type alias removed
- **WHEN** v1.2 code is imported
- **THEN** `baozicode.skills.execution.IndependentRunner` is NOT defined
- **AND** `SkillExecutor.__init__` does NOT accept `independent_runner`
  parameter
- **AND** `SkillExecutor.__init__` does accept `subagent_manager`
  parameter

#### Scenario: Skill with no SubAgentManager injection fails gracefully
- **WHEN** `SkillExecutor` is constructed with `subagent_manager=None`
- **AND** a Skill with `mode: independent` is executed
- **THEN** `SkillExecutionResult` has `ok=False`
- **AND** `summary` is "Skill '<name>' 需要独立模式执行,但
  subagent_manager 未注入;在 BaoZiCodeApp.__init__ 装配时传
  subagent_manager=self.subagents"

## REMOVED Requirements

### Requirement: IndependentRunner closure-based sub-Agent execution
The v1.0 `IndependentRunner` type alias and `SkillExecutor.independent_runner`
constructor parameter are REMOVED in v1.2. The closure-based injection
mechanism is replaced by `SubAgentManager.dispatch(...)`. Any v1.0 caller
that injected an `IndependentRunner` closure MUST be updated to inject
`subagent_manager` instead.

#### Scenario: No more IndependentRunner parameter
- **WHEN** `SkillExecutor(loader, activation)` is called without
  `independent_runner`
- **THEN** v1.0 would raise `TypeError: missing required argument`
- **AND** v1.2 succeeds (the parameter is gone)
