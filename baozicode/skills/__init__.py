"""v1.0 Skills — 可复用 AI 操作的封装与按需激活。

公开 API:
- `SkillDef` / `SkillFrontmatter` — 元数据 dataclass + frontmatter Pydantic
- `parse_frontmatter(md_text)` — 抽 `---` 包围的 YAML,返回 (frontmatter, body)
- `SkillRegistry` — 3 级目录扫描 + 优先级合并
- `SkillActivation` — 运行时活跃 Skill 状态 + 动态 section 渲染 + 斜杠挂载
- `SkillLoader` — `load_skill(name, args)` 主入口 + load_skill tool + 占位符替换
- `SkillWhitelistFilter` + `validate_declared_tools` — 工具白名单双层防御
- `SkillExecutor` — 两种执行模式(shared / independent)

模块:
- `schema.py`     — SkillFrontmatter / SkillDef + parse_frontmatter
- `registry.py`   — SkillRegistry.scan / reload / list_visible
- `activation.py` — 活跃集合 + render_active_section + 斜杠 handler
- `loader.py`     — load_skill 双入口(tool + `/skill` slash) + 占位符替换
- `whitelist.py`  — Skill 白名单双层防御(L1 静态 / L2 动态)
- `execution.py`  — shared 追加 / independent 子 Agent
- `builtin/`      — 3 个样板 Skill(commit / review / test)

依赖方向(单向):
    skills/  ─→  tools/base.py              (ToolDefinition / tool_type 字段)
    skills/  ─→  commands/                  (slash 注册,import 留作调用方)
    skills/  ─→  conversation/manager.py    (snapshot 子 Agent)
    skills/  ─→  config/schema.py           (SkillsConfig)

skills/ 不 import textual(在 chat_screen 注入);不 import llm/ 后端
(模型选择由 config.skills.summary_model 决定,实际调 LLM 留作 execution)。
"""

from baozicode.skills.activation import ActiveSkill, SkillActivation
from baozicode.skills.execution import (
    IndependentRunner,
    SkillExecutionResult,
    SkillExecutor,
)
from baozicode.skills.loader import (
    LOAD_SKILL_TOOL,
    LoadSkillResult,
    SkillLoader,
    substitute_placeholders,
)
from baozicode.skills.registry import ScanError, SkillRegistry, emit_scan_warnings
from baozicode.skills.schema import (
    MAX_HISTORY_BUBBLES,
    SkillDef,
    SkillFrontmatter,
    parse_frontmatter,
)
from baozicode.skills.whitelist import (
    SkillWhitelistFilter,
    validate_declared_tools,
)

__all__ = [
    "ActiveSkill",
    "IndependentRunner",
    "LOAD_SKILL_TOOL",
    "LoadSkillResult",
    "MAX_HISTORY_BUBBLES",
    "ScanError",
    "SkillActivation",
    "SkillDef",
    "SkillExecutionResult",
    "SkillExecutor",
    "SkillFrontmatter",
    "SkillLoader",
    "SkillRegistry",
    "SkillWhitelistFilter",
    "emit_scan_warnings",
    "parse_frontmatter",
    "substitute_placeholders",
    "validate_declared_tools",
]
