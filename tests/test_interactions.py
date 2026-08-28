"""四件套交互逻辑测试：别走别走梗（任务二）、编译兴衰军师（任务三）、
过审小剧场（任务四）。全程无 GUI、无网络。
"""
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw import bus  # noqa: E402
from petfw.drivers.llm import LLMDriver, audit_note  # noqa: E402
from petfw.drivers.rule import RuleDriver  # noqa: E402
from petfw.streaks import BuildStreak  # noqa: E402


def _cp(**brain):
    """构造内存 configparser，模拟 config.ini 的 brain 段。"""
    import configparser
    cp = configparser.ConfigParser()
    cp.add_section("brain")
    for k, v in brain.items():
        cp.set("brain", k, v)
    return cp


def _states(cmds):
    return [c.state for c in cmds if isinstance(c, bus.SetState)]


def _says(cmds):
    return [c.text for c in cmds if isinstance(c, bus.Say)]


# ------------------------------------------------ 任务二：点击接管与台词池迁移
# 单击行为已由宿主专属接管（固定句 + 专属音效 + shock 定格演出，hide 已入禁用区），
# 规则脑不再处理 click；原 CLICK_LINES 随机池整体迁移为 praise/kiss 的 PRAISE_LINES。
class TestClickTakenOverAndPraisePool(unittest.TestCase):
    def setUp(self):
        self.d = RuleDriver()

    def test_click_no_longer_dispatched_to_driver(self):
        # click 不再有专属分支：落进未知事件兜底（Hop），绝不冒随机台词
        for ev in ({"type": "click"},
                   {"type": "click", "away_seconds": 700},
                   {"type": "click", "away_seconds": 200},
                   {"type": "click", "away_seconds": 30}):
            cmds = self.d.react(ev)
            self.assertEqual(_states(cmds), [], f"ev={ev} 不许切表情")
            self.assertEqual(_says(cmds), [], f"ev={ev} 不许再说话")
            self.assertTrue(any(isinstance(c, bus.Hop) for c in cmds))

    def test_away_tiers_are_gone(self):
        # 「别走别走」三档随 click 分支一起退役：任何 away 值都走兜底
        from petfw.drivers import rule as rule_mod
        for attr in ("CLICK_LINES", "AWAY_LOST_SECONDS", "AWAY_SLACK_SECONDS",
                     "AWAY_LOST_LINES", "AWAY_SLACK_LINES", "_click_cmds"):
            self.assertFalse(hasattr(rule_mod, attr), f"{attr} 应已删除")

    def test_praise_pool_migrated_from_click_lines(self):
        from petfw.drivers.rule import PRAISE_LINES
        # 池子由原 CLICK_LINES 九句 + 原 praise/kiss 两句合并而来
        self.assertEqual(len(PRAISE_LINES), 11)
        for line in ("嘿嘿…被摸头了", "今天也要元气满满哦！", "再戳我就要炸毛啦！",
                     "嘿嘿…被夸得好开心嘛", "mua！收下我的小心心！"):
            self.assertIn(line, PRAISE_LINES)

    def test_praise_and_kiss_share_pool_with_dance(self):
        # love 已入禁用区（五态精简）：被夸/被亲改开心到跳舞，台词池保留
        from petfw.drivers.rule import PRAISE_LINES
        for ev in ("praise", "kiss"):
            for _ in range(20):
                cmds = self.d.react({"type": "hook", "event": ev})
                self.assertEqual(_states(cmds), ["dance"], f"event={ev}")
                for t in _says(cmds):
                    self.assertIn(t, PRAISE_LINES)

    def test_describe_reports_minutes_and_seconds(self):
        m = bus.describe_event({"type": "click", "away_seconds": 300})
        self.assertIn("回来了", m)
        self.assertIn("5 分钟", m)
        s = bus.describe_event({"type": "click", "away_seconds": 20})
        self.assertIn("回来了", s)
        self.assertIn("20 秒", s)

    def test_describe_plain_click_unchanged(self):
        # 老事件协议（无 away 字段）保持原样，已有文案不漂移（bus 未动）
        self.assertIn("戳", bus.describe_event({"type": "click"}))


# ------------------------------------------------------------ 任务三：兴衰军师
class TestBuildStreak(unittest.TestCase):
    def test_doom_at_three_errors(self):
        s = BuildStreak()
        self.assertEqual(s.update("error"), {"flourish": None})      # 1
        self.assertEqual(s.update("error"), {"flourish": None})      # 2
        v = s.update("error")
        self.assertEqual(v["flourish"], "doom")                      # 3 触发
        self.assertEqual(v["streak"], 3)

    def test_comeback_after_two_losses(self):
        s = BuildStreak()
        s.update("error")
        s.update("error")
        v = s.update("success")
        self.assertEqual(v["flourish"], "comeback")
        self.assertEqual(v["streak"], 2)

    def test_other_events_do_not_clear_the_count(self):
        s = BuildStreak()
        s.update("error")
        s.update("edit")     # 其它事件透传空字典
        s.update("test")
        self.assertEqual(s.update("noop"), {})
        v = s.update("error")        # 穿插其它事件没有清账：这才第 2 败
        self.assertEqual(v["flourish"], None)
        v = s.update("error")        # 第 3 败照常触发 doom@3
        self.assertEqual(v["flourish"], "doom")
        self.assertEqual(v["streak"], 3)

    def test_success_resets_counter(self):
        s = BuildStreak()
        s.update("error")
        self.assertEqual(s.update("success"), {"flourish": None})   # 连败1不算翻盘
        self.assertEqual(s.update("success"), {"flourish": None})   # 已归零
        self.assertEqual(s.update("error"), {"flourish": None})

    def test_unknown_events_pass_through_empty(self):
        s = BuildStreak()
        for name in ("edit", "done", "start", "", None):
            self.assertEqual(s.update(name), {})


class TestRuleFlourish(unittest.TestCase):
    def test_comeback_dances_with_hop(self):
        # love 已入禁用区：翻盘庆祝改扭舞，Hop 蹦跶与著名台词都保留
        d = RuleDriver()
        cmds = d.react({"type": "hook", "event": "success",
                        "flourish": "comeback", "streak": 2})
        self.assertEqual(_states(cmds), ["dance"])
        self.assertTrue(any(isinstance(c, bus.Hop) for c in cmds))
        self.assertIn("翻盘", "".join(_says(cmds)))

    def test_doom_branch_retired_falls_back_to_error_mapping(self):
        # doom→hide 随 hide 入禁用区整段注释：连败归宿待主人拍板，
        # 当前 doom 落回普通事件映射（error→cry）兜底，绝不指向禁用态
        from petfw.drivers import rule as rule_mod
        self.assertFalse(hasattr(rule_mod, "FLOURISH_DOOM"),
                         "FLOURISH_DOOM 应整体注释保留而非存活")
        d = RuleDriver()
        for _ in range(20):
            cmds = d.react({"type": "hook", "event": "error",
                            "flourish": "doom", "streak": 3})
            self.assertEqual(_states(cmds), ["cry"],
                             "doom 兜底不得再演 hide")
            for t in _says(cmds):
                self.assertIn(t, rule_mod.HOOK_LINES["error"][1])

    def test_without_flourish_keeps_normal_table(self):
        d = RuleDriver()
        cmds = d.react({"type": "hook", "event": "success"})
        self.assertEqual(_states(cmds), ["cheer"])
        self.assertTrue(any("搞定" in t or "花球" in t for t in _says(cmds)))

    def test_describe_reports_streak_situation(self):
        doom = bus.describe_event({"type": "hook", "event": "error",
                                   "flourish": "doom", "streak": 4})
        self.assertIn("4", doom)
        self.assertIn("连败", doom)
        back = bus.describe_event({"type": "hook", "event": "success",
                                   "flourish": "comeback", "streak": 2})
        self.assertIn("翻盘", back)


# ------------------------------------------------------------ 任务四：过审小剧场
class _ScriptedRng:
    """按脚本吐 randint 结果，让概率分支变成可断言的两条路径。"""

    def __init__(self, *seq):
        self.seq = list(seq)
        self.i = 0

    def randint(self, a, b):
        v = self.seq[self.i % len(self.seq)]
        self.i += 1
        return v


class TestAuditNote(unittest.TestCase):
    HIT_SUFFIX_MARK = "本句已过审"

    def test_hit_path_appends_minutes(self):
        # 第 1 次 randint(1,6)=1 命中；第 2 次 randint(1,7)=5 分钟
        note = audit_note(_ScriptedRng(1, 5))
        self.assertIn(self.HIT_SUFFIX_MARK, note)
        self.assertIn("审核笑了5分钟", note)

    def test_miss_path_returns_empty(self):
        self.assertEqual(audit_note(_ScriptedRng(2)), "")

    def test_seeded_samples_are_stable(self):
        rng = random.Random(42)
        hits = sum(1 for _ in range(240) if audit_note(rng))
        # 固定种子下序列完全确定；只画统计范围，不钉死某个具体数字
        self.assertGreater(hits, 0, "240 个样本一个没命中，概率实现有问题")
        self.assertLess(hits, 240, "不可能全命中")
        self.assertLessEqual(hits / 240, 0.35)   # 名义 1/6 ≈ 0.167

    def test_llm_react_attaches_note_on_hit(self):
        d = LLMDriver(_cp(api_base="http://127.0.0.1:9", api_key="x",
                          model="test-model"),
                      fallback=RuleDriver(), rng=_ScriptedRng(1, 3))
        d._call_api = lambda msg: '{"state":"cheer","text":"成功啦"}'
        says = _says(d.react({"type": "hook", "event": "success"}))
        self.assertTrue(any(t.startswith("成功啦") and self.HIT_SUFFIX_MARK in t
                            for t in says))

    def test_llm_react_clean_when_missed(self):
        d = LLMDriver(_cp(api_base="http://127.0.0.1:9", api_key="x",
                          model="test-model"),
                      fallback=RuleDriver(), rng=_ScriptedRng(6))
        d._call_api = lambda msg: '{"state":"cheer","text":"成功啦"}'
        for t in _says(d.react({"type": "hook", "event": "success"})):
            self.assertNotIn(self.HIT_SUFFIX_MARK, t)

    def test_fallback_reply_never_gets_note(self):
        # 走规则脑兜底的回复是本地台词，不该被盖上“过审”章
        d = LLMDriver(_cp(), fallback=RuleDriver(), rng=_ScriptedRng(1, 1))
        for t in _says(d.react({"type": "hook", "event": "praise"})):
            self.assertNotIn(self.HIT_SUFFIX_MARK, t)


if __name__ == "__main__":
    unittest.main()
