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


# ------------------------------------------------------------ 任务二：回归彩蛋
class TestAwayReturn(unittest.TestCase):
    def setUp(self):
        self.d = RuleDriver()

    def test_short_or_no_away_keeps_random_pool(self):
        # 没带 away_seconds、太短、拖拽误触的负数：全部走原随机池（不能切表情）
        for away in (None, 0, -3, 59):
            ev = {"type": "click"}
            if away is not None:
                ev["away_seconds"] = away
            cmds = self.d.react(ev)
            self.assertEqual(_states(cmds), [], f"away={away} 不该切表情")
            self.assertTrue(_says(cmds))

    def test_medium_absence_laugh(self):
        for away in (180, 300, 599):   # 边界：180 秒起算「懒得理我」档
            cmds = self.d.react({"type": "click", "away_seconds": away})
            self.assertEqual(_states(cmds), ["laugh"], f"away={away}")
            joined = "".join(_says(cmds))
            self.assertIn("总算舍得回来", joined)

    def test_long_absence_shock(self):
        for away in (600, 3600):       # 边界：600 秒起算「离家出走」档
            cmds = self.d.react({"type": "click", "away_seconds": away})
            self.assertEqual(_states(cmds), ["shock"], f"away={away}")
            joined = "".join(_says(cmds))
            self.assertIn("别走别走", joined)
            self.assertIn("你去哪了", joined)

    def test_describe_reports_minutes_and_seconds(self):
        m = bus.describe_event({"type": "click", "away_seconds": 300})
        self.assertIn("回来了", m)
        self.assertIn("5 分钟", m)
        s = bus.describe_event({"type": "click", "away_seconds": 20})
        self.assertIn("回来了", s)
        self.assertIn("20 秒", s)

    def test_describe_plain_click_unchanged(self):
        # 老事件协议（无 away 字段）保持原样，已有文案不漂移
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
    def test_comeback_loves_with_hop(self):
        d = RuleDriver()
        cmds = d.react({"type": "hook", "event": "success",
                        "flourish": "comeback", "streak": 2})
        self.assertEqual(_states(cmds), ["love"])
        self.assertTrue(any(isinstance(c, bus.Hop) for c in cmds))
        self.assertIn("翻盘", "".join(_says(cmds)))

    def test_doom_hides(self):
        from petfw.drivers.rule import FLOURISH_DOOM
        d = RuleDriver()
        for _ in range(20):  # 台词随机出池：只断言状态恒为 hide、话出自池子
            cmds = d.react({"type": "hook", "event": "error",
                            "flourish": "doom", "streak": 3})
            self.assertEqual(_states(cmds), ["hide"])
            for t in _says(cmds):
                self.assertIn(t, FLOURISH_DOOM[1])

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
        for t in _says(d.react({"type": "click"})):
            self.assertNotIn(self.HIT_SUFFIX_MARK, t)


if __name__ == "__main__":
    unittest.main()
