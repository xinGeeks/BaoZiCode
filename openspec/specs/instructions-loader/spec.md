# instructions-loader Specification

## Purpose
Three-tier loading of `BaoZiCode.md` instruction files (user_global → project_local → project_root), with `@include` resolution that enforces depth limits, cycle prevention, and path escaping. The concatenated result is injected at the top of the system prompt.

## Requirements

### Requirement: Three-tier scan and concatenation order
The system MUST scan three candidate paths at startup, in the following order, and concatenate their contents (with `@include` directives resolved) into a single string injected at the top of the system prompt:
1. `~/.baozicode/BaoZiCode.md` (user-global layer)
2. `<project_root>/.baozicode/BaoZiCode.md` (project-local layer)
3. `<project_root>/BaoZiCode.md` (project-root layer)

The system MUST skip any missing file silently (no error). If all three files are missing, the system MUST print a single stderr banner line `未找到 BaoZiCode.md,建议创建项目根目录文件` and proceed with an empty instruction set.

#### Scenario: Empty project, no instruction files
- **WHEN** startup is invoked in a project with no `BaoZiCode.md` at any of the three tiers
- **THEN** the system does NOT raise an error
- **AND** prints the banner line to stderr
- **AND** no instruction content is prepended to the system prompt

#### Scenario: Single project-root file present
- **WHEN** only `<project_root>/BaoZiCode.md` exists with content `# Project Rules\n\n- Use type hints`
- **THEN** the system loads that file's content (with `@include` resolved)
- **AND** the system prompt's leading section begins with the resolved content

#### Scenario: All three tiers present, concatenated in order
- **WHEN** user-global has `user-rule-1`, project-local has `local-rule-2`, project-root has `project-rule-3`
- **THEN** the concatenated string is `user-rule-1\n\n---\n\nlocal-rule-2\n\n---\n\nproject-rule-3`
- **AND** the user-global section appears FIRST in the system prompt
- **AND** the project-root section appears LAST (so the LLM treats later content as overriding when conflicts arise)

### Requirement: @include directive resolution with three guards
The system MUST support the syntax `@include <relative-or-absolute-path>` (one per line). When the resolver encounters an `@include` line, it MUST resolve the path, read the referenced file, and recursively resolve `@include` directives inside it. The system MUST enforce three guards during recursion:
1. **Depth limit**: maximum 5 levels of recursion; deeper includes are silently skipped with a warning.
2. **Cycle prevention**: any path already in the visited set (keyed by `Path.resolve().as_posix()`) is skipped with a warning.
3. **Path whitelist**: the resolved absolute path MUST be inside either `<project_root>` OR `~/.baozicode/`; any path outside these roots is rejected with a warning.

#### Scenario: Relative path resolves from current file's parent
- **WHEN** `<project_root>/BaoZiCode.md` contains `@include snippets/python.md`
- **THEN** the resolver reads `<project_root>/snippets/python.md`
- **AND** resolves any nested `@include` directives relative to `snippets/` directory

#### Scenario: Absolute path rejected
- **WHEN** any `@include` directive specifies an absolute path like `/etc/passwd` or `/tmp/foo.md`
- **THEN** the include is rejected with a warning logged
- **AND** the rest of the file continues parsing

#### Scenario: Path escaping project root rejected
- **WHEN** an `@include` directive resolves to `<project_root>/../outside.md` (sibling directory)
- **THEN** the path is normalized to `<parent_dir>/outside.md` via `Path.resolve()`
- **AND** `is_relative_to(project_root)` returns False
- **AND** the include is rejected with a warning

#### Scenario: Cycle A→B→A detected
- **WHEN** `A.md` contains `@include B.md` AND `B.md` contains `@include A.md`
- **THEN** the resolver detects `A.md` is already in the visited set on the second visit
- **AND** the recursive include is skipped with a warning
- **AND** no infinite recursion occurs

#### Scenario: Depth limit at 5
- **WHEN** a chain of 6 nested `@include` directives is constructed (A→B→C→D→E→F→G)
- **THEN** levels 1-5 are resolved normally
- **AND** level 6 (G) is skipped with a warning `depth limit 5 exceeded at <path>`
- **AND** resolution completes within finite time

#### Scenario: Missing included file
- **WHEN** `@include nonexistent.md` is encountered
- **THEN** a warning is logged with the missing path
- **AND** the rest of the parent file continues parsing
- **AND** other includes are still processed

### Requirement: Existing BaoZiCode.md file NOT read by this loader
The system MUST NOT read the existing `<project_root>/BaoZiCode.md` file. This loader reads only `BaoZiCode.md` files. The two files coexist independently — `BaoZiCode.md` is the BaoZiCode-specific instruction file; the existing `BaoZiCode.md` remains for other tools (e.g., Claude Code) to read.

#### Scenario: BaoZiCode.md present alongside BaoZiCode.md
- **WHEN** both `<project_root>/BaoZiCode.md` and `<project_root>/BaoZiCode.md` exist
- **THEN** the instructions loader reads `BaoZiCode.md` only
- **AND** the system prompt includes the contents of `BaoZiCode.md`
- **AND** the contents of `BaoZiCode.md` are NOT included
- **AND** `BaoZiCode.md` is left untouched on disk

### Requirement: Concatenated output injected at top of system prompt
The system MUST inject the concatenated and `@include`-resolved instructions as the LEADING section of the system prompt, before any of the 7 fixed sections (identity / constraints / task_mode / action_exec / tool_usage / tone_style / text_output) defined in v0.4 PromptBuilder. If the concatenated string is empty (no files present), the system MUST skip injection entirely (no empty `## 项目指令` section).

#### Scenario: Instructions appear before identity section
- **WHEN** the loader produces `## 项目指令\n- Always use type hints`
- **THEN** the resulting `stable_system` begins with `## 项目指令\n- Always use type hints`
- **AND** is followed by the v0.4 fixed sections in their existing order

#### Scenario: Empty instructions, no section injected
- **WHEN** all three BaoZiCode.md files are missing
- **THEN** `stable_system` is identical to v0.7 behavior (begins with the identity section)
- **AND** no empty `## 项目指令` placeholder appears

### Requirement: Configurable enable/disable
The system MUST honor `config.instructions.enabled: bool` (default True). When False, the loader is skipped entirely and no instruction content is injected. The default True preserves the v0.8 behavior described above.

#### Scenario: Disabled via config
- **WHEN** `config.instructions.enabled = False` is set in YAML
- **THEN** the loader does NOT scan or read any files
- **AND** the banner line is NOT printed
- **AND** no instruction content is injected