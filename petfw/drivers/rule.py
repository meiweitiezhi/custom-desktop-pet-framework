"""本地规则驱动：查表 + 随机，离线兜底，永远可用。"""
import random

from .. import bus
from ..extensions.weather import state_for as _weather_state_for
from .base import Driver

CLICK_LINES = [
    "嘿嘿…被摸头了",
    "再戳我就要炸毛啦！",
    "痒痒的～",
    "今天也要元气满满哦！",
    "要抱抱？不给。",
    "我是团子，不是包子！",
    "再戳一下，我可能就熟了。",
    "我圆但我不滚——好吧，想推也可以。",
    "嘘…我在假装自己是个安静的鸡蛋。",
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
    "edit": ("eat", ["让我看看改了啥好吃的", "键盘真香…"]),
    "success": ("cheer", ["搞定！夸我夸我！", "成功啦！花球甩起来！"]),
    # 报错不再躺平：当场哭唧唧，委屈值拉满
    "error": ("cry", ["呜哇——又挂了…",
                      "别骂了别骂了，我自己知道错了",
                      "哇的一声哭出来"]),
    "test": ("idle", ["测试跑着呢，我盯着呢"]),
    "start": ("cheer", ["开工！我给你举花球！"]),
    "done": ("cheer", ["收工！今晚吃叉子大餐！"]),
    # 外部程序可直接发自定义事件（bridge 不限词表）：被夸/被亲 → 比小心心
    "praise": ("love", ["嘿嘿…被夸得好开心嘛", "mua！收下我的小心心！"]),
    "kiss": ("love", ["嘿嘿…被夸得好开心嘛", "mua！收下我的小心心！"]),
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

# 别走别走梗：按离开时长分档（秒）。600 起算「离家出走」，180 起算「偷懒摸鱼」
AWAY_LOST_SECONDS = 600
AWAY_SLACK_SECONDS = 180
AWAY_LOST_LINES = ["别走别走！我班呢！你去哪了嘛…"]
AWAY_SLACK_LINES = ["总算舍得回来了？"]

# 编译兴衰军师的判定台词：翻盘庆祝 / 连败哀叹
# 翻盘保留著名台词，状态改比小心心并附带蹦跶；连败缩进帽子里躲着反省
FLOURISH_COMEBACK = ("love", ["三十年河东 三十年河西！这不就翻盘了！"])
FLOURISH_DOOM = ("hide", ["让我在这顶帽子里反省一下人生",
                          "世界暂时与我无关，勿cue"])

# 预留台词池：alien（外星吸人）/ blushmax（羞耻爆炸）暂无自动触发，
# 状态本身已合法可托盘手选，这里先备好文案留给未来钩子接入。
ALIEN_LINES = ["哔哔——检测到非法可爱，强制吸收"]
BLUSHMAX_LINES = ["不要再说了啦……(白眼翻向天花板)"]


class RuleDriver(Driver):
    name = "rule"

    def react(self, event: dict) -> list:
        t = event.get("type")
        cmds = []
        try:
            if t == "click":
                cmds = self._click_cmds(event)
            elif t == "reminder":
                kind = event.get("kind") if event.get("kind") in REMINDER_LINES else "drink"
                cmds = [
                    bus.SetState("eat" if kind == "drink" else "sleep"),
                    bus.Say(random.choice(REMINDER_LINES[kind]), seconds=10),
                ]
            elif t == "hook":
                flourish = event.get("flourish")
                if flourish == "comeback":
                    spec = FLOURISH_COMEBACK
                elif flourish == "doom":
                    spec = FLOURISH_DOOM
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
                cmds = [bus.Say(random.choice(CLICK_LINES), seconds=5)]
            else:
                cmds = [bus.Hop()]
        except Exception:
            cmds = [bus.Hop()]
        return cmds

    def _click_cmds(self, event: dict) -> list:
        """点击分支：带 away_seconds 时升级成回归彩蛋，否则原随机池。"""
        away = event.get("away_seconds")
        if not isinstance(away, (int, float)) or away <= 0:
            return [bus.Say(random.choice(CLICK_LINES)), bus.Hop()]
        if away >= AWAY_LOST_SECONDS:      # 「离家出走」档：惊讶 + 兴师问罪
            return [bus.SetState("shock"),
                    bus.Say(random.choice(AWAY_LOST_LINES))]
        if away >= AWAY_SLACK_SECONDS:     # 摸鱼档：笑哭 + 嘲讽
            return [bus.SetState("laugh"),
                    bus.Say(random.choice(AWAY_SLACK_LINES))]
        return [bus.Say(random.choice(CLICK_LINES)), bus.Hop()]
