# prompt-modular Specification

## Purpose
Modifications to v0.4 `prompt/sections/memory.py` to read from two `MEMORY.md` files (user-global and project-local) instead of the single deprecated `config.memory_path` file.

## Requirements

### Requirement: Memory section reads from two MEMORY.md indices
The system MUST extend `prompt/sections/memory.py` `render(ctx: BuildContext) -> str` to read from TWO new `BuildContext` fields:
- `ctx.memory_index_user: str | None` — contents of `~/.baozicode/memory/MEMORY.md` (or fallback to deprecated `memory_path`)
- `ctx.memory_index_project: str | None` — contents of `<project_root>/.baozicode/memory/MEMORY.md`

The system MUST render one section per non-empty index, using headers `## 长期记忆 (用户级)` and `## 长期记忆 (项目级)` respectively. User-global MUST appear first (consistent with the v0.8 instruction file priority order).

#### Scenario: Both indices present, two sections rendered
- **WHEN** `memory_index_user = "## [user-pref] no-emoji — User dislikes emoji"` and `memory_index_project = "## [project] uses-uv — Project uses uv"`
- **THEN** the rendered memory section text is:
  ```
  ## 长期记忆 (用户级)
  ## [user-pref] no-emoji — User dislikes emoji

  ## 长期记忆 (项目级)
  ## [project] uses-uv — Project uses uv
  ```

#### Scenario: Only user index present
- **WHEN** `memory_index_user = "..."` and `memory_index_project = None` (empty)
- **THEN** only `## 长期记忆 (用户级)` section is rendered
- **AND** no empty `## 长期记忆 (项目级)` placeholder

#### Scenario: Both empty, no section rendered
- **WHEN** both `memory_index_user` and `memory_index_project` are empty or None
- **THEN** `render()` returns empty string
- **AND** no memory section is appended to the system prompt

### Requirement: New BuildContext fields
The system MUST extend `baozicode/prompt/types.py` `BuildContext` dataclass with:
- `memory_index_user: str | None = None`
- `memory_index_project: str | None = None`

The system MUST NOT remove any existing `BuildContext` fields.

#### Scenario: Defaults preserve v0.7 behavior
- **WHEN** `PromptBuilder.build()` is called without explicitly setting the new fields
- **THEN** `memory_index_user` and `memory_index_project` default to None
- **AND** if no memory stores are configured, the memory section renders empty

### Requirement: PromptBuilder reads indices from stores
The system MUST extend `PromptBuilder.build()` to read indices from the two `MemoryStore` instances. The system MUST accept these stores via constructor parameter (default None) and inject the index text into `BuildContext` via `dataclasses.replace`.

#### Scenario: Stores passed at build time
- **WHEN** `PromptBuilder(user_store, project_store).build(...)` is called
- **THEN** `memory_index_user = user_store.read_index().format_for_prompt()`
- **AND** `memory_index_project = project_store.read_index().format_for_prompt()`

#### Scenario: Stores not passed (v0.7 compat)
- **WHEN** `PromptBuilder().build(...)` is called with no stores
- **THEN** `memory_index_user` and `memory_index_project` are None
- **AND** the memory section renders empty

### Requirement: Deprecated memory_path fallback
The system MUST, when `config.memory_path` is set to a non-default value AND both new memory stores are empty (no `MEMORY.md` files), read the deprecated single file's content and inject it as `memory_index_user`.

**Implementation responsibility:** The fallback read MUST be performed in `PromptBuilder.build()`, NOT in `prompt/sections/memory.py`. The `sections/memory.py` reads only `BuildContext.memory_index_user` / `memory_index_project` (which are populated strings); the loader responsibility for opening the deprecated file is on `PromptBuilder`. This separation lets `sections/memory.py` stay pure (no I/O).

#### Scenario: Fallback used
- **WHEN** `~/.baozicode/memory/MEMORY.md` does not exist
- **AND** `<project>/.baozicode/memory/MEMORY.md` does not exist
- **AND** `config.memory_path` points to an existing file
- **THEN** `memory_index_user` is set to the contents of `config.memory_path`
- **AND** the system prompt includes `## 长期记忆 (用户级)` with that content

#### Scenario: New dirs take priority
- **WHEN** `~/.baozicode/memory/MEMORY.md` exists with content X
- **AND** `config.memory_path` exists with content Y
- **THEN** `memory_index_user` is set to X (new dir wins)
- **AND** the deprecated file is ignored

### Requirement: Index formatting for prompt
The system MUST define `MemoryIndex.format_for_prompt() -> str` that returns the index as a compact markdown string suitable for the system prompt. The format MUST be:
```
## [<type>] <slug> — <title>
<one_liner>

## [<type>] <slug2> — <title2>
<one_liner2>
```

#### Scenario: Format with 3 entries
- **WHEN** the index has 3 entries
- **THEN** `format_for_prompt()` returns a string with 3 `## [...]` headers, each followed by a one_liner and a blank line
- **AND** total size is bounded by the index's `total_bytes` field