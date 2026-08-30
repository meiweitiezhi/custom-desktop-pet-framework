"""五态精简回归：manifest 禁用区机制 + 全链路无禁用态活路径。

主人拍板（至高指令）：保留 idle/sleep/dance/shock/cry 五件套；
laugh/eat/love/hide/alien/blushmax 七态 + alien_suck 吸入演出整体移入
manifest 顶层 "_disabled_states" 禁用区——数据完整可恢复（把条目搬回
"states" 即视为恢复上线），loader 只读 "states"、显式忽略下划线保留键。
全程无 GUI、无网络。
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw import bus  # noqa: E402
from petfw.drivers.rule import RuleDriver  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "assets" / "manifest.json"

# 主人拍板的五件套；cheer（打气）不在禁用名单里，照常留在活动区。
# 禁用七件套（含本就缺图的 angry）+ alien_suck 吸入演出 = 禁用区八条。
KEEP_FIVE = ("idle", "sleep", "dance", "shock", "cry")
DISABLED_EIGHT = ("laugh", "eat", "love", "hide", "alien", "blushmax",
                  "angry", "alien_suck")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class TestDisabledZoneManifest(unittest.TestCase):
    """任务一：_disabled_states 禁用区在 manifest 层生效。"""

    def test_disabled_zone_holds_all_eight(self):
        m = _manifest()
        zone = m.get("_disabled_states")
        self.assertIsInstance(zone, dict, "manifest 顶层缺 _disabled_states")
        self.assertEqual(set(zone), set(DISABLED_EIGHT))
        # 禁用区条目数据完整：file/frames 都还在，随时可搬回 states 恢复
        for name, spec in zone.items():
            self.assertTrue(spec.get("file"), f"{name} 入区时丢了 file")

    def test_states_only_keeps_active_roster(self):
        states = _manifest()["states"]
        self.assertEqual(
            set(states),
            set(KEEP_FIVE) | {"cheer", "dance6", "vroom"},
            "活动区 = 五件套 + cheer（打气）+ dance6（六拍舞）"
            " + vroom（骑摩托）")
        for name in DISABLED_EIGHT:
            self.assertNotIn(name, states, f"{name} 必须只存在于禁用区")

    def test_loader_explicitly_ignores_underscore_keys(self):
        # loader 只认 "states"：顶层下划线键（禁用区/未来元数据）显式忽略，
        # 本测试锁死这一行为，防止将来有人把禁用区混进加载流程
        from petfw.host import active_states
        m = _manifest()
        self.assertEqual(set(active_states(m)), set(m["states"]))
        fake = {"states": {"idle": {"file": "states/idle.png"}},
                "_disabled_states": {"hide": {"file": "states/hide.png"}},
                "_future_meta": {"whatever": 1}}
        got = active_states(fake)
        self.assertEqual(list(got), ["idle"],
                         "下划线开头的顶层键一律不得泄进活动状态表")

    def test_zone_entries_survive_transition_bake_roundtrip(self):
        # 禁用区不是删数据：条目字段应与五态同构（frames 条目仍带 frame_ms）
        zone = _manifest()["_disabled_states"]
        for name in ("hide", "alien_suck"):
            spec = zone[name]
            self.assertEqual(spec.get("play"), "once", f"{name} 仍是 once 演出")
            self.assertTrue(spec.get("frames"), f"{name} 帧序列仍在")


class TestNoLivePathToDisabledStates(unittest.TestCase):
    """任务四冗余排查的可执行版：任何活代码路径都不得再发出禁用态。"""

    def setUp(self):
        self.active = set(_manifest()["states"])

    @staticmethod
    def _states(cmds):
        return [c.state for c in cmds if isinstance(c, bus.SetState)]

    def test_rule_driver_never_emits_disabled_states(self):
        d = RuleDriver()
        events = [{"type": "reminder", "kind": "drink"},
                  {"type": "reminder", "kind": "stretch"},
                  {"type": "idle", "seconds": 120},
                  {"type": "click"},
                  {"type": "??"}
                  ]
        events += [{"type": "hook", "event": e} for e in
                   ("edit", "success", "error", "test", "start", "done",
                    "praise", "kiss", "alien", "blushmax", "love", "hide")]
        events += [{"type": "hook", "event": "error", "flourish": "doom",
                    "streak": 3},
                   {"type": "hook", "event": "success", "flourish": "comeback",
                    "streak": 2}]
        events += [{"type": "weather", "condition": c} for c in
                   ("Clear", "Clouds", "Rain", "Snow", "Mist")]
        events += [{"type": "growth", "commits": 3, "title": "蛋",
                    "leveled_up": b} for b in (True, False)]
        for ev in events:
            for _ in range(6):   # 台词随机出池：多抽几次压住概率分支
                for st in self._states(d.react(ev)):
                    self.assertIn(st, self.active,
                                  f"事件 {ev} 发出了禁用态 {st}")

    def test_rule_tables_point_only_at_active_states(self):
        from petfw.drivers import rule
        for ev, (st, _lines) in rule.HOOK_LINES.items():
            self.assertIn(st, self.active, f"HOOK_LINES[{ev}] -> {st}")
        self.assertIn(rule.FLOURISH_COMEBACK[0], self.active,
                      "翻盘庆祝必须落在活动状态上")
        for attr in ("FLOURISH_DOOM", "ALIEN_LINES", "BLUSHMAX_LINES"):
            self.assertFalse(hasattr(rule, attr),
                             f"{attr} 应随禁用区整体注释保留，不再存活")

    def test_weather_mapping_points_only_at_active_states(self):
        from petfw.extensions.weather import CONDITION_TO_STATE
        for cond, st in CONDITION_TO_STATE.items():
            self.assertIn(st, self.active, f"weather {cond} -> {st}")

    def test_menu_roster_covers_only_active_states(self):
        from petfw.host import MENU_EMOTION, MENU_FUN, SIX_BEAT_STATE
        self.assertEqual(
            set(MENU_EMOTION) | set(MENU_FUN) | {SIX_BEAT_STATE},
            self.active,
            "菜单词条集合（含六拍舞专属词条）必须恰等于活动状态集合")


if __name__ == "__main__":
    unittest.main()
