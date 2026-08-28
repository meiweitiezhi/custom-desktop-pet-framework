"""大模型驱动：调 OpenAI 兼容网关让团子自己决定说什么+做什么表情。

- 大模型只负责「对话」：返回台词和表情，一切动画/提醒/hook 处理都在本地。
- 调用是纯同步的，宿主放到工作线程执行，UI 永不等网络。
- 没配置 / 断网 / 报错 -> 明确提示「需要接入自己的api」，同时规则脑无缝接管，
  所以离线或没有大模型时桌宠的一切其他功能照常可用。
"""
import json
import random
import re
import time
import urllib.error
import urllib.request

from .. import bus
from .base import Driver

# 说话失败提示的限频窗口（秒）：连续失败时不刷屏
ERR_NOTICE_COOLDOWN = 90.0
ERR_NOTICE_TEXT = "呜…说话失败了\n（需要接入自己的api）"

# 过审小剧场：模型台词命中 1/AUDIT_ONE_IN 概率时加盖的“审核通过”彩蛋章
AUDIT_ONE_IN = 6
AUDIT_SUFFIX = "\n（本句已过审，审核笑了%d分钟)"

SYSTEM_PROMPT_TEMPLATE = (
    "你是桌宠「{name}」，一只圆滚滚的白色小生物，性格软萌话痨但每次只说一句。"
    "根据【事件】用它的口吻说一句话，并选出最贴切的表情状态。\n"
    "state 可选值：idle=害羞发呆 cheer=举花球打气 eat=举着叉子干饭 sleep=犯困睡觉 "
    "laugh=笑哭 shock=惊讶 angry=生气 dance=扭舞\n"
    "只能输出一个 JSON 对象，格式严格为：{{\"state\":\"...\",\"text\":\"不超过18个字的台词\"}}\n"
    "不要输出 JSON 以外的任何字符。"
)

MISSING_KW = object()  # 内部标记：配置不全，无法发起对话


def audit_note(rng=None) -> str:
    """过审小剧场：以 1/AUDIT_ONE_IN 概率返回彩蛋后缀，未命中返回空串。

    rng 参数可注入 random.Random(42) 或脚本化假对象，让概率分支在测试里
    变成可断言的两条路径；生产路径传 None 直接用全局 random。
    """
    roller = rng if rng is not None else random
    if roller.randint(1, AUDIT_ONE_IN) != 1:
        return ""
    return AUDIT_SUFFIX % roller.randint(1, 7)


class LLMDriver(Driver):
    name = "llm"

    def __init__(self, cp, fallback: Driver, rng=None):
        super().__init__()
        from ..config import llm_kwargs
        self.kw = llm_kwargs(cp)
        self.fallback = fallback
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(name=self.pet_name)
        self._last_notice = 0.0
        self._rng = rng  # 可注入的随机源（audit_note 用）

    # 拆开两个方法是为了测试时可以替换 _call_api 而不发真实请求
    def _call_api(self, user_msg: str) -> str:
        if not all(self.kw.values()):
            raise RuntimeError("未配置 api_base/api_key/model")
        url = self.kw["api_base"].rstrip("/") + "/chat/completions"
        payload = json.dumps({
            "model": self.kw["model"],
            "messages": [{"role": "user", "content": user_msg}],
            "temperature": 0.9,
            "max_tokens": 120,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.kw['api_key']}",
            },
        )
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    def _notice_cmds(self) -> list:
        """失败提示按冷却时间限频；控制台始终记录完整原因。"""
        now = time.monotonic()
        out = []
        if now - self._last_notice > ERR_NOTICE_COOLDOWN:
            self._last_notice = now
            out.append(bus.Say(ERR_NOTICE_TEXT, seconds=8))
        return out

    def react(self, event: dict) -> list:
        if not all(self.kw.values()):
            return self.fallback.react(event) + self._notice_cmds()
        user_msg = f"【事件】{bus.describe_event(event)}"
        try:
            reply = self._call_api(user_msg)
            cmds = bus.commands_from_dict(parse_reply(reply))
            if cmds:
                return self._with_audit_note(cmds)
            print(f"[团子][llm] 回复解析不出命令: {reply[:120]!r}")
            return self.fallback.react(event)
        except Exception as exc:
            print(f"[团子][llm] 对话调用失败: {exc}")
            return self.fallback.react(event) + self._notice_cmds()

    def _with_audit_note(self, cmds: list) -> list:
        """给模型产出的第一条台词按概率盖上「过审」章；规则脑兜底不盖章。"""
        note = audit_note(self._rng)
        if not note:
            return cmds
        for c in cmds:
            if isinstance(c, bus.Say):
                c.text = c.text + note
                break
        return cmds


def parse_reply(text: str):
    """从模型回复里抠出 {"state","text"}；抠不出返回 None。

    模型经常不听话地输出 ```json 围栏或前后废话，这里做容错。
    """
    if not text:
        return None
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj
