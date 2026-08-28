"""闲置自动入睡的判定纯函数：只算不改，宿主负责执行。

用户需求原话场景：小主人长时间没理桌宠，它自己安安静静睡着——
不发台词、不弹气泡、不惊扰。交互（点击/提醒/hook）会自然刷新宿主的
活动时钟并把状态切走，无需任何唤醒逻辑。
"""
from __future__ import annotations

# 闲置多少秒算"该睡了"：90 秒没有交互且正闲着，就悄然入睡
AUTO_SLEEP_SECONDS = 90.0


def should_auto_sleep(quiet_seconds, current: str,
                      bubble_visible: bool = False) -> bool:
    """闲置入睡三条件同时满足才返回 True：

    - quiet_seconds >= 90（持续没交互，边界值 90.0 含在内）；
    - current == "idle"（正闲着，没在表演/睡觉/其它状态）；
    - not bubble_visible（气泡没开着，台词展示期不打扰）。
    垃圾输入（None/乱码）一律 False，绝不抛错。
    """
    if current != "idle" or bubble_visible:
        return False
    try:
        quiet = float(str(quiet_seconds).strip())
    except (TypeError, ValueError):
        return False
    return quiet >= AUTO_SLEEP_SECONDS
