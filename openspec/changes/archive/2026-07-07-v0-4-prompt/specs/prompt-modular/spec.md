# prompt-modular Specification

## Purpose
TBD - created by archiving change v0-4-prompt. Update Purpose after archive.

## ADDED Requirements

### Requirement: PromptBuilder assembles BuiltPrompt from 11 sections
The system MUST provide a `PromptBuilder` class that, when called with `build(config, plan_mode, tools)`, returns a `BuiltPrompt` containing:
- `stable_system`: a string assembled by concatenating the output of 7 fixed sections in priority order, followed by any non-empty optional sections, with each section separated from the next by a blank line (`\n\n`)
- `dynamic_messages`: a list of `Message(role="user", content=...)` instances, with one entry containing a `<system-reminder type="env">` block derived from the env_info section
- `augmented_tools`: a list of `ToolDefinition` instances, where each input tool's `description` field is potentially augmented with rule prefixes by the `RuleRegistry`
- `cache_breakpoints`: a list of at least two `CacheBreakpoint` entries, one with `location="system_start"` and one with `location="after_tools"`

#### Scenario: Stable system contains 7 fixed headings
- **WHEN** PromptBuilder.build() is called with a valid config
- **THEN** the `stable_system` string contains the headings `## 身份`, `## 系统约束`, `## 任务模式`, `## 动作执行`, `## 工具使用关键规则`, `## 语气风格`, and `## 文本输出`

#### Scenario: Optional sections excluded when empty
- **WHEN** config.custom_instructions is empty AND skills_dir does not exist or is empty AND memory_path does not exist or is empty
- **THEN** the headings `## 自定义指令`, `## 已激活 Skill`, and `## 长期记忆` do NOT appear in stable_system

#### Scenario: Sections separated by blank lines
- **WHEN** any two adjacent `## ...` headings appear in stable_system
- **THEN** the substring between them contains at least one `\n\n` sequence

### Requirement: Each section is a pure render function
The system MUST organize each of the 11 sections as a separate module under `baozicode.prompt.sections.<name>`, each exporting a `render(ctx: BuildContext) -> str` function that returns the section's text. An empty or zero-content section MUST return an empty string.

#### Scenario: Identity section includes default and user override
- **WHEN** config.system_prompt equals the default placeholder string
- **THEN** the identity section renders the hard-coded default "你是 BaoZiCode,v0.3 版本的 AI 编程助手。"
- **AND** does NOT echo back the user's placeholder

#### Scenario: Custom section returns empty when no custom_instructions
- **WHEN** config.custom_instructions is the empty string
- **THEN** the custom section's render() returns `""`
- **AND** the `## 自定义指令` heading does not appear in BuiltPrompt.stable_system

### Requirement: BuildContext carries config and environment data
The system MUST provide a `BuildContext` dataclass that contains at minimum: `config: AppConfig`, `rule_registry: RuleRegistry`, `plan_mode: bool`, `cwd: str`, `os_name: str`, `python_version: str`, `git_branch: str`, `git_commit: str`, `project_name: str`, and `now_iso: str`. Each section's `render()` function receives this context and reads only the fields it needs.

#### Scenario: Env info section reads cwd from context
- **WHEN** BuildContext.cwd equals `/tmp/project`
- **THEN** the env_info section's render() output contains the line `- cwd: /tmp/project`

#### Scenario: Tool usage section reads rules from context
- **WHEN** RuleRegistry has 7 default rules
- **THEN** the tool_usage section's render() output contains all 7 numbered rules (lines starting with "1." through "7.")

### Requirement: Tool descriptions are augmented with rule prefixes
The system MUST, for every `ToolDefinition` in the input `tools` list, prepend any matching rule's `tool_prefix` to the tool's `description` field via `dataclasses.replace()` (preserving frozenness). A rule matches a tool when its `applies_to` tuple contains the tool's name or contains `"*"`. Rules with empty `tool_prefix` (e.g., `error_then_decide`) MUST NOT alter the tool's description.

#### Scenario: Edit tool gets edit_requires_read prefix
- **WHEN** a tool named "Edit" is passed to PromptBuilder.build() and the default RuleRegistry is active
- **THEN** the augmented Edit tool's description starts with the string "【必读】"

#### Scenario: Edit tool's original ToolDefinition is not mutated
- **WHEN** a tool named "Edit" is augmented
- **THEN** the original ToolDefinition instance's description is unchanged
- **AND** a new ToolDefinition instance is returned with the augmented description

#### Scenario: Rules with empty tool_prefix don't pollute description
- **WHEN** RuleRegistry contains a rule with applies_to=("*",) and empty tool_prefix
- **THEN** that rule's text appears ONLY in the system prompt section
- **AND** NO tool's description is modified by that rule
