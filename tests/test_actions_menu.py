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


# 真实仓库现状：angry 无素材图缺席；其余十二态均已出图
LOADED = ["idle", "cheer", "eat", "sleep", "laugh", "shock",
          "dance", "cry", "hide", "love", "alien", "blushmax"]


def _texts(menu: QMenu) -> list:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


class TestActionsMenu(unittest.TestCase):
    def setUp(self):
        self.menu = QMenu()
        self.win = _FakeWindow(LOADED)
        PetWindow.build_actions_menu(self.menu, self.win)

    def test_emotion_and_fun_groups_list_only_loaded_states(self):
        texts = _texts(self.menu)
        # 八正态：生气(angry)没素材必须缺席隐藏；其余直呼其字全部在列
        for zh in ("发呆", "打气", "干饭", "睡觉", "笑哭", "惊讶", "扭舞"):
            self.assertIn(zh, texts)
        self.assertNotIn("生气", texts, "缺图状态绝不能出现在菜单里")
        for zh in ("哭唧唧", "缩帽躲", "比小心心", "外星吸人", "羞耻爆炸"):
            self.assertIn(zh, texts)
        # 三段分组之间要有分隔线，且构建菜单本身不许顺带触发任何动作
        seps = [a for a in self.menu.actions() if a.isSeparator()]
        self.assertGreaterEqual(len(seps), 2)
        self.assertEqual(self.win.played, [], "构建菜单本身不许触发动作")

    def test_system_group_reuses_window_slots(self):
        acts = {a.text(): a for a in self.menu.actions()
                if not a.isSeparator()}
        acts["今日战报"].trigger()
        self.assertEqual(self.win.scanned, 1)
        hook_act = next(t for t in acts if t.startswith("模拟hook"))
        acts[hook_act].trigger()
        self.assertEqual(self.win.hooks,
                         [{"type": "hook", "event": "edit"}])
        wx = acts["天气演示"].menu()
        self.assertIsNotNone(wx, "天气演示应保留四档子菜单")
        wx.actions()[0].trigger()
        self.assertEqual(self.win.dispatched,
                         [{"type": "weather", "condition": "Clear"}])
        rem = acts["健康提醒"]
        self.assertTrue(rem.isCheckable())
        self.assertTrue(rem.isChecked(), "开关初始态要镜像提醒定时器")
        rem.setChecked(False)     # 程序化切换同样会发 toggled 信号
        self.assertEqual(self.win.toggled, [False])
        acts["退出"].trigger()
        self.assertEqual(self.win.quits, 1)


class TestManifestV3Fields(unittest.TestCase):
    """任务二回归：动作字段、idle 微幅化、angry 单图现状不得被误伤。"""

    def _states(self):
        return json.loads(
            MANIFEST.read_text(encoding="utf-8"))["states"]

    def test_multi_frame_states_play_once_and_return_to_idle(self):
        multi = {k: v for k, v in self._states().items() if v.get("frames")}
        self.assertGreaterEqual(len(multi), 10, "至少十个状态带帧序列")
        for name, spec in multi.items():
            self.assertEqual(spec.get("play"), "once",
                             f"{name} 缺 play=once")
            self.assertEqual(spec.get("return_to", "idle"), "idle",
                             f"{name} 的 return_to 默认必须是 idle")

    def test_idle_calm_and_angry_untouched(self):
        states = self._states()
        idle = states["idle"]
        self.assertEqual(idle.get("bob_amp"), 2, "平时安静：呼吸幅度回 2")
        self.assertEqual(idle.get("tilt_deg"), 0)
        angry = states["angry"]
        self.assertNotIn("frames", angry, "angry 保持单图现状")
        self.assertNotIn("play", angry)


if __name__ == "__main__":
    unittest.main()
