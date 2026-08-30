"""命令与事件的纯数据定义。不依赖 Qt，核心逻辑可无头测试。"""
from dataclasses import dataclass, field

# 桌宠支持的状态词表。前四名是核心态：缺图直接启动失败；
# 其余是可选新态：缺图自动降级跳过（允许先注册名字、图片后补到位）。
# assets/manifest.json 由素材管线拥有，允许暂时落后于词表——一致性检查
# 只要求核心态必须已登记、且登记的名字不超出本词表（tests 有防漂移检查）。
STATES = ("idle", "cheer", "eat", "sleep",
          "laugh", "shock", "angry", "dance",
          "cry", "hide", "love", "alien", "blushmax", "vroom", "snotty")

# 核心四态：表情包形象的最低配置，缺任何一张都无法呈现角色
CORE_STATES = ("idle", "cheer", "eat", "sleep")


@dataclass
class SetState:
    """切换表情状态"""
    state: str

    def __post_init__(self):
        if self.state not in STATES:
            raise ValueError(f"未知状态: {self.state!r}，可选 {STATES}")


@dataclass
class Say:
    """冒泡说话，seconds 秒后消失"""
    text: str
    seconds: float = 6.0


@dataclass
class Hop:
    """原地蹦跶一下（短暂的弹跳动画加成）"""
    times: int = 1


Command = object  # 类型仅为文档用途：SetState | Say | Hop


def commands_from_dict(raw) -> list:
    """把 LLM / 外部 hook 给的宽松 dict 规整成命令列表。

    接受形如 {"state": "cheer", "text": "加油！"} 的输入；
    字段缺失、多余、类型不对都不报错，只提取合法部分。
    返回的命令按 [SetState, Say] 排序。
    """
    out = []
    if not isinstance(raw, dict):
        return out
    state = raw.get("state")
    if isinstance(state, str):
        state = state.strip().lower()
        if state in STATES:
            out.append(SetState(state))
    text = raw.get("text") or raw.get("say") or ""
    if isinstance(text, str) and text.strip():
        out.append(Say(text.strip()[:80]))
    return out


def describe_event(ev: dict) -> str:
    """把事件翻译成给大模型看的中文描述。"""
    t = ev.get("type")
    if t == "click":
        # 带了离开时长就是「回归彩蛋」：先军师一句处境，LLM 免费受益
        away = ev.get("away_seconds")
        if isinstance(away, (int, float)) and away > 0:
            away = int(away)
            if away < 60:
                return f"小主人刚刚回来了，他离开了大约 {away} 秒"
            minutes = max(1, round(away / 60))
            return f"小主人刚刚回来了，他离开了大约 {minutes} 分钟"
        return "小主人刚刚用鼠标戳了戳你"
    if t == "reminder":
        kind = ev.get("kind")
        if kind == "drink":
            return "健康提醒时间到了：该提醒小主人喝水啦"
        return "健康提醒时间到了：该提醒小主人站起来伸个懒腰啦"
    if t == "hook":
        mapping = {
            "start": "小主人在 ZCode 里开始干活了",
            "edit": "小主人正在改代码",
            "test": "小主人正在跑测试",
            "success": "任务成功了！",
            "error": "刚才出了个错误",
            "praise": "小主人夸了你一句，被夸得好开心",
            "kiss": "小主人亲了你一口，收到一颗小心心",
            "done": "收工了！",
        }
        base = mapping.get(ev.get("event"), f"收到一个外部信号：{ev.get('event')}")
        # 编译兴衰军师的判定结果：先交代连败/翻盘处境，再接原事件描述
        flourish = ev.get("flourish")
        streak = ev.get("streak", "?")
        if flourish == "doom":
            base = f"代码已经连败 {streak} 个回合，士气跌到谷底：" + base
        elif flourish == "comeback":
            base = f"苦战 {streak} 个回合后终于翻盘！" + base
        msg = ev.get("message")
        if msg:
            base += f"，备注：{msg}"
        return base
    if t == "idle":
        return f"你已经闲了好一会儿（{ev.get('seconds', '?')}秒），主动找点话说吧"
    if t == "growth":
        base = (f"小主人今天已经提交了 {ev.get('commits', '?')} 次代码，"
                f"当前称号「{ev.get('title', '?')}」")
        if ev.get("leveled_up"):
            base += "，刚刚升级了！好好夸夸他"
        return base
    if t == "weather":
        return f"窗外的天气变成了：{ev.get('condition', '未知')}"
    import json
    return f"发生了一些事情：{json.dumps(ev, ensure_ascii=False)[:200]}"
