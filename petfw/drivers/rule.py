"""本地规则驱动：查表 + 随机，离线兜底，永远可用。"""
import random

from .. import bus
from ..extensions.weather import state_for as _weather_state_for
from .base import Driver

# 被夸/被亲的台词池：由原「左键单击随机池」整体迁移而来，并入了原
# praise/kiss 专属两句（单击行为已由宿主专属接管，不再 dispatch 给驱动）。
PRAISE_LINES = [
    "嘿嘿…被摸头了",
    "再戳我就要炸毛啦！",
    "痒痒的～",
    "今天也要元气满满哦！",
    "要抱抱？不给。",
    "我是团子，不是包子！",
    "再戳一下，我可能就熟了。",
    "我圆但我不滚——好吧，想推也可以。",
    "嘘…我在假装自己是个安静的鸡蛋。",
    "嘿嘿…被夸得好开心嘛",
    "mua！收下我的小心心！",
]

REMINDER_LINES = {
    "drink": [
        "咕嘟咕嘟——该喝水啦！",
        "水杯空了就去接满哦～",
        "喝口水，代码不苦！",
    ],
    "stretch": [
        "起来伸个懒腰吧～",
        "久坐屁股会扁掉的！",
        "扭一扭，晃一晃，动起来！",
    ],
}

HOOK_LINES = {
    # 写代码 = 骑上小摩托出发（主人拍板 2026-08）
    "edit": ("vroom", ["出发！写代码去咯！", "骑上小摩托，代码写不完！"]),
    "success": ("cheer", ["搞定！夸我夸我！", "成功啦！花球甩起来！"]),
    # cry 已入禁用区（主人拍板删除）：报错回归躺平睡觉
    "error": ("sleep", ["呜…先躺一会儿，别骂我",
                        "出错了嘛…我也很难过"]),
    "test": ("idle", ["测试跑着呢，我盯着呢"]),
    "start": ("cheer", ["开工！我给你举花球！"]),
    "done": ("cheer", ["收工！今晚吃叉子大餐！"]),
    # love 已入禁用区（五态精简）：被夸/被亲 = 开心到跳舞，台词池保留
    "praise": ("dance", PRAISE_LINES),
    "kiss": ("dance", PRAISE_LINES),
}

IDLE_HOP_LINES = ["嗯？什么声音", "(眨眼)", "zzang~"]

WEATHER_LINES = {
    "Clear": ("cheer", ["大晴天！花球甩起来！", "阳光正好，出去走走呀"]),
    "Clouds": ("idle", ["多云…适合安静地陪着你", "阴天也要元气满满哦"]),
    "Rain": ("sleep", ["滴答滴答…下雨天和睡觉更配 zzZ", "雨天路滑，早点回家哦"]),
    "Snow": ("sleep", ["下雪了！…好冷，抱紧自己睡"]),
}

GROWTH_UP_LINES = [
    "升级啦！今天也是闪闪发光的一天！",
    "叮——称号进化！快夸快夸！",
]

# 编译兴衰军师的判定台词：翻盘保留著名台词，状态改扭舞庆祝
# （原 love 映射已随五态精简入禁用区，被夸/翻盘 = 开心到跳舞）
FLOURISH_COMEBACK = ("dance", ["三十年河东 三十年河西！这不就翻盘了！"])
# 【禁用区】连败三次的 hide 归宿随 hide 态下线，整段注释保留、数据可恢复。
# TODO: 连败三次归宿待主人拍板：候选项 cry / shock / 恢复hide。
# 拍板前 doom 落回普通事件映射（error→cry）兜底，绝不指向禁用态。
# FLOURISH_DOOM = ("hide", ["让我在这顶帽子里反省一下人生",
#                           "世界暂时与我无关，勿cue"])

# 【禁用区】预留台词池随 alien（外星吸人）/ blushmax（羞耻爆炸）两态
# 整体注释保留，恢复状态时一并解封：
# ALIEN_LINES = ["哔哔——检测到非法可爱，强制吸收"]
# BLUSHMAX_LINES = ["不要再说了啦……(白眼翻向天花板)"]


class RuleDriver(Driver):
    name = "rule"

    def react(self, event: dict) -> list:
        t = event.get("type")
        cmds = []
        try:
            if t == "reminder":
                kind = event.get("kind") if event.get("kind") in REMINDER_LINES else "drink"
                cmds = [
                    # eat 已入禁用区（干饭被砍）：喝水提醒改为回发呆 + 台词
                    bus.SetState("idle"),
                    bus.Say(random.choice(REMINDER_LINES[kind]), seconds=10),
                ]
            elif t == "hook":
                flourish = event.get("flourish")
                if flourish == "comeback":
                    spec = FLOURISH_COMEBACK
                # 【禁用区】doom→hide 分支整段注释保留，新归宿待主人拍板：
                # 候选项 cry / shock / 恢复hide；当前落回普通事件映射兜底。
                # elif flourish == "doom":
                #     spec = FLOURISH_DOOM
                else:
                    spec = HOOK_LINES.get(event.get("event"))
                if spec is None:
                    spec = ("idle", IDLE_HOP_LINES)
                cmds = [bus.SetState(spec[0]),
                        bus.Say(random.choice(spec[1]))]
                if flourish == "comeback":  # 翻盘时刻加一段原地蹦跶庆祝
                    cmds.append(bus.Hop())
            elif t == "weather":
                cond = event.get("condition")
                spec = WEATHER_LINES.get(cond)
                if spec:
                    cmds = [bus.SetState(spec[0]),
                            bus.Say(random.choice(spec[1]), seconds=6)]
                elif isinstance(cond, str) and cond:
                    # 没配台词的天气按映射切表情，安静陪伴
                    cmds = [bus.SetState(_weather_state_for(cond)),
                            bus.Say("(陪你一起看天色)", seconds=4)]
                else:
                    cmds = [bus.Hop()]
            elif t == "growth":
                title = event.get("title", "")
                text = f"今日提交 {event.get('commits', '?')} 次｜称号:{title}"
                leveled_up = bool(event.get("leveled_up"))
                if leveled_up:
                    text += "\n" + random.choice(GROWTH_UP_LINES)
                # 升级时刻用扭舞庆祝，日常战报沿用打气
                cmds = [bus.SetState("dance" if leveled_up else "cheer"),
                        bus.Say(text, seconds=10 if leveled_up else 7)]
                if leveled_up:
                    cmds.append(bus.Hop())
            elif t == "idle":
                # 闲聊台词源：原挂在已退役的 CLICK_LINES 上，现改用闲聊池
                cmds = [bus.Say(random.choice(IDLE_HOP_LINES), seconds=5)]
            else:
                cmds = [bus.Hop()]
        except Exception:
            cmds = [bus.Hop()]
        return cmds
