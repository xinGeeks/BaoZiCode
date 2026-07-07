"""动作执行约定 — 通用执行流程。"""

from baozicode.prompt.types import BuildContext


def render(ctx: BuildContext) -> str:
    return """## 动作执行
- 先想后做:每次响应前先想清楚当前进度、缺口、下一步
- 一次只调一轮工具:等结果回来再决定下一步,不要预测结果
- 错误处理:工具返回 is_error=true 时,先分析再决定(重试/换工具/放弃)
- 长任务分步:超过 3 步的任务,先在文本里列计划再开始执行
- 不重复劳动:已成功的步骤不需要再确认""".rstrip()


__all__ = ["render"]
