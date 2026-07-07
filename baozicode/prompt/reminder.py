"""Plan mode 提醒节奏控制。"""


class PlanModeReminder:
    """Plan mode 节奏控制:iteration 1 发一次,之后每 N 轮再发一次,中间不发。

    例:interval=5 → 1, 6, 11, 16, ... 发。
    """

    def __init__(self, plan_mode: bool, interval: int = 5) -> None:
        self._plan_mode = plan_mode
        self._interval = interval

    def should_emit(self, iteration: int) -> bool:
        if not self._plan_mode:
            return False
        if iteration == 1:
            return True
        # 之后每 interval 轮再发一次
        return (iteration - 1) % self._interval == 0


__all__ = ["PlanModeReminder"]
