"""v1.2 SubAgent Delegation — 两阶段加载的「可用 Agent 列表」section。

v1.2 行为:只列 name + 一句话说明 + 工具白名单 + 模型/permission-mode,
不输出 body(由 main Agent 调 `task(type="definition", role=name, prompt=...)`
按需加载,SOP 在 sub-Agent 自己的 system prompt 里)。

两阶段的好处:很多 role 时不撑爆 prompt,LLM 看到有合适的再主动 task。

回退路径:
- App 没挂 SubAgentManager(测试 / SubAgentsConfig.enabled=False)→ 返回空字符串
- 全 hidden → 返回空字符串
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baozicode.prompt.types import BuildContext

if TYPE_CHECKING:
    pass


def render(ctx: BuildContext) -> str:
    """两阶段 SubAgent 列表的「阶段一」section。

    返回空字符串 → PromptBuilder 跳过此 section(节省 token)。
    """
    registry = getattr(ctx, "agent_registry", None)
    if registry is None:
        return ""

    visible = registry.list_visible()
    if not visible:
        return ""

    lines: list[str] = ["## 可用 SubAgent(两阶段加载)\n"]
    lines.append(
        "以下 SubAgent 只列名字 + 一句话说明 + 工具 + 模型。要派任务请调 "
        "`task(type=\"definition\", role=<role>, prompt=<指令>, async=true)`。"
        "fork 模式调 `task(type=\"fork\", prompt=<指令>)` 复用主对话历史。"
    )
    lines.append("")
    for name, desc, source in visible:
        # 不存在的 list_visible 返回 (name, description, source) — 调 registry 看更深字段
        agent = registry.lookup(name)
        if agent is None:
            continue
        fm = agent.frontmatter
        # 工具集概览
        tools_part = ""
        if fm.tools:
            tools_part = f", tools={fm.tools}"
        elif fm.tools_deny:
            tools_part = f", 禁止 {fm.tools_deny}"
        else:
            tools_part = ", tools=全允许"
        # 模型 / mode / max-iter
        meta = []
        if fm.model:
            meta.append(f"model={fm.model}")
        if fm.permission_mode:
            meta.append(f"mode={fm.permission_mode}")
        if fm.max_iterations != 20:  # 不显式列默认值
            meta.append(f"max_iter={fm.max_iterations}")
        meta_part = f" [{', '.join(meta)}]" if meta else ""
        lines.append(
            f"- `{name}` (来源: {source}) — {desc}{tools_part}{meta_part}"
        )
    return "\n".join(lines)


__all__ = ["render"]
