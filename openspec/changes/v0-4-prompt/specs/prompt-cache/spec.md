# prompt-cache Specification

## Purpose
TBD - created by archiving change v0-4-prompt. Update Purpose after archive.

## ADDED Requirements

### Requirement: CacheBreakpoint models a cacheable region boundary
The system MUST provide a `CacheBreakpoint` frozen dataclass with two fields: `location: Literal["system_start", "system_end", "after_tools", "before_user"]` and `priority: int` (range 0-100, higher = more important to cache).

#### Scenario: CacheBreakpoint construction
- **WHEN** `CacheBreakpoint(location="system_start", priority=100)` is constructed
- **THEN** the dataclass is frozen (assignment to fields raises)
- **AND** `bp.location == "system_start"` and `bp.priority == 100`

### Requirement: BuiltPrompt always declares at least two cache breakpoints
The system MUST, for every `PromptBuilder.build()` call, populate `BuiltPrompt.cache_breakpoints` with at least these two entries: `CacheBreakpoint("system_start", priority=100)` and `CacheBreakpoint("after_tools", priority=80)`. Additional breakpoints MAY be added in future versions.

#### Scenario: Default cache breakpoints present
- **WHEN** PromptBuilder.build() is called with any valid config
- **THEN** `bp.cache_breakpoints` has length >= 2
- **AND** contains a CacheBreakpoint with `location="system_start"` and priority >= 90
- **AND** contains a CacheBreakpoint with `location="after_tools"`

### Requirement: LLMClient.stream accepts cache_breakpoints keyword-only
The `LLMClient.stream` abstract method MUST accept a keyword-only parameter `cache_breakpoints: list[CacheBreakpoint] | None = None` as its fourth parameter (after `messages`, `system`, `tools`). All four concrete backends (Anthropic, OpenAI, MiniMax, DeepSeek) MUST accept this parameter in their `stream` method signature but MAY ignore its value in v0.4.

#### Scenario: cache_breakpoints is keyword-only
- **WHEN** `LLMClient.stream` signature is inspected
- **THEN** the `cache_breakpoints` parameter has `kind == inspect.Parameter.KEYWORD_ONLY`

#### Scenario: Backends accept cache_breakpoints kwarg
- **WHEN** `AnthropicBackend.stream(messages, system, tools, cache_breakpoints=[...])` is called
- **THEN** no `TypeError: unexpected keyword argument` is raised
- **AND** in v0.4 the backend MAY ignore the value (no `cache_control` markers added)

#### Scenario: Minimal backend stub accepts the parameter
- **WHEN** a custom backend subclass implements `stream` with `*, cache_breakpoints=None` and is called with `cache_breakpoints=[CacheBreakpoint("system_start", 100)]`
- **THEN** the call succeeds and the backend receives the parameter value

### Requirement: Stable system string is byte-identical across LLM calls in a single Agent run
The system MUST ensure that within a single `Agent.run` invocation, every call to `llm.stream()` receives the same `system` string byte-for-byte. This is a precondition for prompt caching to be effective.

#### Scenario: Two calls in one run have identical system
- **WHEN** Agent.run processes a multi-turn request that invokes llm.stream twice
- **THEN** `llm.stream.calls[0].system == llm.stream.calls[1].system` (byte-equal)

#### Scenario: cache_breakpoints passed on every call
- **WHEN** Agent.run invokes llm.stream multiple times
- **THEN** each invocation receives the same `cache_breakpoints` argument from `self._prompt.cache_breakpoints`

### Requirement: /status command shows cache statistics
The system MUST extend the TUI `/status` command to display three additional lines below the existing input/output totals: `cache_read: <N> tokens`, `cache_write: <N> tokens`, and `hit_rate: <PCT>%` where `PCT = round(cache_read / (cache_read + input) * 100, 1)`. When `cache_read + input == 0`, hit_rate MUST be `0.0`.

#### Scenario: /status shows cache fields after a turn
- **WHEN** the user runs `/status` after at least one LLM call
- **THEN** the conversation area shows lines containing `cache_read:`, `cache_write:`, and `hit_rate:`
- **AND** the hit rate percentage reflects the session's accumulated usage
