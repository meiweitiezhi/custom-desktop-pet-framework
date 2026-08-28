"""动作菜单构建器（本体右键 + 托盘共用）与 manifest v3 动作字段回归测试。

GUI 侧只造 QMenu 和假宿主对象（duck-typing），全程 offscreen、无网络；
manifest 回归直接读本仓库真实 assets/manifest.json。
"""
import json
import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from petfw.host import PetWindow  # noqa: E402

_APP = QApplication.instance() or QApplication([])

MANIFEST = (pathlib.Path(__file__).resolve().parents[1]
            / "assets" / "manifest.json")


class _FakeTimer:
    def isActive(self):
        return True


class _FakeWindow:
    """builder 只需要这些槽位与 states 花名册，不必拉起真 PetWindow。"""

    def __init__(self, loaded):
        self.states = {n: {} for n in loaded}
        self.reminder_timer = _FakeTimer()
        self.played = []
        self.dispatched = []
        self.hooks = []
        self.scanned = 0
        self.toggled = []
        self.quits = 0

    def play_action(self, name):
        self.played.append(name)

    def scan_growth(self):
        self.scanned += 1

    def dispatch(self, ev):
        self.dispatched.append(ev)

    def _on_hook(self, ev):
        self.hooks.append(ev)

    def _toggle_reminders(self, on):
        self.toggled.append(on)

    def quit_app(self):
        self.quits += 1


# 五态精简后的真实仓库现状：活动区=五件套+cheer（打气）；
# laugh/eat/hide/love/alien/blushmax/alien_suck 已入 manifest._disabled_states
LOADED = ["idle", "cheer", "sleep", "shock", "dance", "cry"]


def _texts(menu: QMenu) -> list:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


class TestActionsMenu(unittest.TestCase):
    def setUp(self):
        self.menu = QMenu()
        self.win = _FakeWindow(LOADED)
        PetWindow.build_actions_menu(self.menu, self.win)

    def test_emotion_and_fun_groups_list_only_loaded_states(self):
        texts = _texts(self.menu)
        # 五态精简：情绪组=发呆/打气/睡觉/惊讶/扭舞，整活组只剩哭唧唧
        for zh in ("发呆", "打气", "睡觉", "惊讶", "扭舞", "哭唧唧"):
            self.assertIn(zh, texts)
        # 禁用七态 + UFO 吸入必须整词缺席（数据在 _disabled_states 里）
        for zh in ("干饭", "笑哭", "生气", "缩帽躲", "比小心心", "外星吸人",
                   "羞耻爆炸", "UFO 吸入"):
            self.assertNotIn(zh, texts, f"禁用态「{zh}」不得出现在菜单")
        # 三段分组之间要有分隔线，且构建菜单本身不许顺带触发任何动作
        seps = [a for a in self.menu.actions() if a.isSeparator()]
        self.assertGreaterEqual(len(seps), 2)
        self.assertEqual(self.win.played, [], "构建菜单本身不许触发动作")

    def test_dance_entry_triggers_play_action(self):
        acts = {a.text(): a for a in self.menu.actions()
                if not a.isSeparator()}
        acts["扭舞"].trigger()
        self.assertEqual(self.win.played, ["dance"])

    def test_system_group_reuses_window_slots(self):
        acts = {a.text(): a for a in self.menu.actions()
                if not a.isSeparator()}
        acts["今日战报"].trigger()
        self.assertEqual(self.win.scanned, 1)
        # 主人拍板暂时下线：天气演示与模拟hook(edit) 词条整体消失
        # （weather 扩展本体与 bridge 事件通路保留，只是没有菜单入口）
        self.assertNotIn("天气演示", acts)
        for t in acts:
            self.assertFalse(t.startswith("模拟hook"),
                             "模拟hook(edit) 词条应已下线")
        rem = acts["健康提醒"]
        self.assertTrue(rem.isCheckable())
        self.assertTrue(rem.isChecked(), "开关初始态要镜像提醒定时器")
        rem.setChecked(False)     # 程序化切换同样会发 toggled 信号
        self.assertEqual(self.win.toggled, [False])
        acts["退出"].trigger()
        self.assertEqual(self.win.quits, 1)


class TestManifestV3Fields(unittest.TestCase):
    """manifest 回归：动作字段、idle 微幅化、禁用区条目完整性不得被误伤。"""

    def _manifest(self):
        return json.loads(MANIFEST.read_text(encoding="utf-8"))

    def _states(self):
        return self._manifest()["states"]

    def test_multi_frame_states_play_once_and_return_to_idle(self):
        multi = {k: v for k, v in self._states().items() if v.get("frames")}
        # 活动区带帧序列的恰为这五个（含 shock/cry/dance 的 _Q 压扁转场帧
        # 与 transition-v2 起改为 45 帧常驻循环档的 cheer）
        self.assertEqual(set(multi), {"sleep", "shock", "dance", "cry",
                                      "cheer"})
        for name, spec in multi.items():
            if name == "cheer":
                # cheer 是唯一例外：打气派对循环档（常驻搞笑，不谢幕）
                self.assertEqual(spec.get("play"), "loop",
                                 "cheer 必须是 loop 常驻循环")
                self.assertFalse(spec.get("pingpong"),
                                 "cheer 正向循环，不走乒乓")
                continue
            self.assertEqual(spec.get("play"), "once",
                             f"{name} 缺 play=once")
            self.assertEqual(spec.get("return_to", "idle"), "idle",
                             f"{name} 的 return_to 默认必须是 idle")

    def test_alien_suck_entry_shape_preserved_in_zone(self):
        """专属吸入动作入禁用区：39 帧 @33ms、once、回 idle、无尾部定格。"""
        zone = self._manifest()["_disabled_states"]
        spec = zone.get("alien_suck")
        self.assertIsNotNone(spec, "禁用区缺 alien_suck 条目")
        self.assertEqual(len(spec["frames"]), 26 + 13)
        self.assertEqual(spec["frame_ms"], 33)
        self.assertEqual(spec["play"], "once")
        self.assertEqual(spec.get("hold_tail_ms", 0), 0)
        self.assertEqual(spec.get("return_to", "idle"), "idle")
        # 帧文件名规约：states/alien_suck_F{idx:03d}.png 且首尾都在 frames 里
        self.assertEqual(spec["frames"][0], "states/alien_suck_F000.png")
        self.assertEqual(spec["frames"][-1], "states/alien_suck_F038.png")

    def test_idle_calm_and_angry_untouched(self):
        states = self._states()
        idle = states["idle"]
        self.assertEqual(idle.get("bob_amp"), 2, "平时安静：呼吸幅度回 2")
        self.assertEqual(idle.get("tilt_deg"), 0)
        # angry 本就缺图、如今整体入禁用区：单图现状原样冻结，随时可恢复
        angry = self._manifest()["_disabled_states"]["angry"]
        self.assertNotIn("frames", angry, "angry 保持单图现状")
        self.assertNotIn("play", angry)


if __name__ == "__main__":
    unittest.main()
