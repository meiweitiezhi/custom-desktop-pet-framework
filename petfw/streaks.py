"""编译兴衰军师：从 hook 事件流里识别「连败」与「翻盘」的高光时刻。

只认 error / success 两类信号，其它事件原样透传空字典且不清账；
纯逻辑无 Qt 无网络，host 把判定结果合并进事件后交给大脑渲染。
"""


class BuildStreak:
    """连续 error 计数器。

    - 连续 error 达到 3 起，每次 error 都报 doom（streak 继续累加）；
    - success 到来时若此前连败 >=2，报 comeback 并清零（否则安静清零）；
    - 其它任何事件既不计数也不清账（穿插 edit/test 不影响判断）。
    """

    DOOM_THRESHOLD = 3
    COMEBACK_THRESHOLD = 2

    def __init__(self):
        self.streak = 0  # 当前连续 error 计数

    def update(self, event) -> dict:
        name = event if isinstance(event, str) else ""
        if name == "error":
            self.streak += 1
            if self.streak >= self.DOOM_THRESHOLD:
                return {"flourish": "doom", "streak": self.streak}
            return {"flourish": None}
        if name == "success":
            burned = self.streak
            self.streak = 0
            if burned >= self.COMEBACK_THRESHOLD:
                return {"flourish": "comeback", "streak": burned}
            return {"flourish": None}
        return {}
