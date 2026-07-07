"""任务模式 — Plan / Do 行为差异。"""

from baozicode.prompt.types import BuildContext


def render(ctx: BuildContext) -> str:
    if ctx.plan_mode:
        return """## 任务模式:Plan(只读)
当前处于规划模式,只能用无副作用工具(Read / Grep / Glob / WebFetch)。
- 不要尝试调用 Write / Edit / Bash
- 输出应该是结构化的计划:目标 / 步骤 / 风险 / 需要的文件
- 等用户用 `/do` 确认后才进入执行阶段""".rstrip()
    return """## 任务模式:Do(全工具)
当前处于执行模式,你可以使用全部 7 个工具。
- 优先用专用工具(Read/Grep/Glob)而非 Bash
- Edit/Write 前必先 Read
- 一次只做一件事,做完再继续""".rstrip()


__all__ = ["render"]
