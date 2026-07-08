"""v1.0 Skills — 两阶段加载的「可用 Skill 列表」section。

v0.4 行为:扫 `skills_dir/*.md`,把全文拼到 system prompt。
v1.0 行为:只列 name + 一句话说明,正文(SOP)由 `load_skill` tool 按需加载。
两阶段的好处:Skill 多时不撑爆 prompt,LLM 看到有合适的再主动 load。

激活后的完整 body 由 `SkillActivation.render_active_section()` 输出为
`<system-reminder type="active_skills">` 块,每轮 `_inject_reminders` 重
建,优先级高于本 section。

回退路径:
- App 没挂 Skill 系统(测试 / SkillsConfig.enabled=False)→ 走 v0.4 旧路
  (扫 `skills_dir` 下的 `*.md`,无文件返回空字符串)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from baozicode.prompt.types import BuildContext

if TYPE_CHECKING:
    pass


def render(ctx: BuildContext) -> str:
    """两阶段 Skill 列表的「阶段一」section。

    返回空字符串 → PromptBuilder 跳过此 section(节省 token)。
    """
    # 1. v1.0:优先用 SkillRegistry(由 App 注入到 ctx)
    registry = getattr(ctx, "skill_registry", None)
    if registry is not None:
        visible = registry.list_visible()
        if not visible:
            return ""
        lines: list[str] = ["## 可用 Skill(两阶段加载)\n"]
        lines.append(
            "以下 Skill 只列名字 + 一句话说明,要看完整 SOP 请调 "
            "`load_skill(name=<skill>, args={<var>: <value>})` 加载。"
        )
        lines.append("")
        for name, desc, source in visible:
            lines.append(f"- `{name}` (来源: {source}) — {desc}")
        return "\n".join(lines)

    # 2. v0.4 回退路径:扫 `skills_dir/*.md`(保留兼容老配置)
    skills_dir = getattr(ctx.config, "skills_dir", None)
    if skills_dir is None:
        return ""
    path = Path(skills_dir)
    if not path.exists() or not path.is_dir():
        return ""
    files = sorted(path.glob("*.md"))
    if not files:
        return ""
    blocks: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8").strip()
        if text:
            blocks.append(f"### {f.stem}\n{text}")
    if not blocks:
        return ""
    return "## 已激活 Skill\n\n" + "\n\n".join(blocks)


__all__ = ["render"]
