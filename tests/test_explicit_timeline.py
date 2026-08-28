"""显式三段拼接时间线（v4）：ActionPlayer 分段 + 宿主秒表保险丝 + manifest。

主人最高指令：回发呆逻辑必须是「记秒数 + if 判断」的直白形态——表演段
（frames 播一轮）→ 定格段（hold_seconds 秒停末帧）→ 转场拼接段
（transition_frames 压扁回弹帧播一轮）→ 谢幕回 return_to；宿主另备一道
独立秒表保险丝，超 max_seconds 强制回发呆，防任何原因卡死。
全程无 GUI（宿主用例 offscreen）、无网络。
"""
import configparser
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from petfw.action_player import (  # noqa: E402
    ActionPlayer,
    action_duration_seconds,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "assets" / "manifest.json"
STATES_DIR = ROOT / "assets" / "states"

ONCE_STATES = ("shock", "cry", "dance")


def _v4_spec(n=3, frame_ms=100, hold=1.2, trans=2, play="once"):
    """v4 形态的标准夹具：表演 n 帧 + hold 秒定格 + trans 张转场帧。"""
    return {"frames": [f"f{i}" for i in range(n)],
            "transition_frames": [f"q{i}" for i in range(trans)],
            "frame_ms": frame_ms,
            "hold_seconds": hold,
            "max_seconds": 99.0,
            "play": play,
            "return_to": "idle"}


# ================================================================ 任务一
class TestExplicitSegments(unittest.TestCase):
    """ActionPlayer：start 组装显式段列表，tick 记秒数 + if 判断切段。"""

    def test_start_assembles_explicit_three_segments(self):
        p = ActionPlayer()
        p.start(_v4_spec(n=3, frame_ms=100, hold=1.2, trans=2))
        self.assertEqual([name for name, _ in p.segments],
                         ["perform", "hold", "transition"])
        durs = {name: dur for name, dur in p.segments}
        self.assertAlmostEqual(durs["perform"], 0.3, places=9)
        self.assertAlmostEqual(durs["hold"], 1.2, places=9)
        self.assertAlmostEqual(durs["transition"], 0.2, places=9)

    def test_zero_length_segments_are_skipped(self):
        p = ActionPlayer()
        p.start(_v4_spec(n=2, frame_ms=50, hold=0.0, trans=0))
        self.assertEqual([(n, round(d, 9)) for n, d in p.segments],
                         [("perform", 0.1)],
                         "0 长段不许出现在时间线上")

    def test_elapsed_seconds_records_every_tick(self):
        p = ActionPlayer()
        p.start(_v4_spec())
        p.tick(0.1)
        p.tick(0.25)
        self.assertAlmostEqual(p.elapsed_seconds, 0.35, places=9,
                               msg="秒表必须显式记下每一拍 dt")

    def test_hold_boundary_at_exact_seconds(self):
        # 主人给的样例口径：定格 1.2 秒，第 1.19s 仍在 hold、1.21s 已进 transition
        spec = _v4_spec(n=1, frame_ms=10, hold=1.2, trans=3)
        p = ActionPlayer()
        p.start(spec)
        self.assertAlmostEqual(p.tick(0.011), 0)          # 表演段（1 帧）
        self.assertAlmostEqual(p.tick(1.179), 0,          # 1.19s：仍在定格段
                               msg="1.19s 必须还停在末帧定格")
        self.assertEqual(p.segment, "hold")
        self.assertEqual(p.tick(0.02), 0,                 # 1.21s：已进转场段
                         "1.21s 之后必须已经切进转场段")
        self.assertEqual(p.segment, "transition")

    def test_transition_plays_then_curtain_falls(self):
        # 表演 0.3s → 定格 1.2s → 转场 0.2s → None；注入 0.05s 拍子精确对账
        # （转场帧 frame_ms=100，每帧驻留两拍：0,0,1,1）
        p = ActionPlayer()
        p.start(_v4_spec(n=3, frame_ms=100, hold=1.2, trans=2))
        seq = []
        for _ in range(40):
            seq.append(p.tick(0.05))
            if seq[-1] is None:
                break
        self.assertEqual(seq[:5], [0, 1, 1, 2, 2], "表演段按 frame_ms 推进")
        self.assertEqual(seq[5:29], [2] * 24, "定格段恒亮末帧（1.2 秒=24 拍）")
        self.assertEqual(seq[29:33], [0, 0, 1, 1],
                         "转场段推进 transition_frames 下标")
        self.assertIsNone(seq[33], "三段全走完必须谢幕")
        self.assertFalse(p.alive)
        self.assertTrue(p.done)

    def test_loop_has_no_hold_or_transition_segments(self):
        p = ActionPlayer()
        p.start(_v4_spec(n=3, frame_ms=50, hold=1.2, trans=2, play="loop"))
        self.assertEqual(p.segments, [], "loop 永续循环，没有三段概念")
        seq = [p.tick(0.05) for _ in range(400)]
        self.assertTrue(all(s is not None for s in seq), "loop 不许谢幕")
        self.assertTrue(set(seq) <= {0, 1, 2}, "loop 只在表演帧里打转")

    def test_duration_counts_three_segments(self):
        total = action_duration_seconds(_v4_spec(n=3, frame_ms=100,
                                                 hold=1.2, trans=2))
        self.assertAlmostEqual(total, 0.3 + 1.2 + 0.2, places=9)

    def test_tick_reports_current_segment_name(self):
        p = ActionPlayer()
        p.start(_v4_spec(n=3, frame_ms=100, hold=1.2, trans=2))
        p.tick(0.05)
        self.assertEqual(p.segment, "perform")
        for _ in range(6):        # 走满 0.3s 进定格
            p.tick(0.05)
        self.assertEqual(p.segment, "hold")
        for _ in range(24):       # 走满 1.2s 进转场
            p.tick(0.05)
        self.assertEqual(p.segment, "transition")


# ================================================================ 任务二
class TestHostFuse(unittest.TestCase):
    """宿主独立秒表保险丝：action_overtime 纯判定 + play_action 上弦接线。"""

    @staticmethod
    def _window():
        from PySide6.QtWidgets import QApplication
        from petfw.host import PetWindow
        QApplication.instance() or QApplication([])
        cp = configparser.ConfigParser()
        cp.add_section("pet")
        cp.set("pet", "display_size", "64")
        cp.add_section("bridge")
        cp.set("bridge", "enabled", "false")
        return PetWindow(cp)

    def test_action_overtime_judgement(self):
        from petfw.host import action_overtime
        self.assertFalse(action_overtime(0.0, 4.114), "刚上场绝不超时")
        self.assertFalse(action_overtime(4.1, 4.114), "没过线不触发")
        self.assertTrue(action_overtime(4.2, 4.114), "过线必须触发")

    def test_action_overtime_zero_or_garbage_disables(self):
        from petfw.host import action_overtime
        for bad in (0, 0.0, None, "abc", -1):
            self.assertFalse(action_overtime(999.0, bad),
                             f"max_seconds={bad!r} 视为不设防")

    def test_play_action_arms_stopwatch_with_manifest_max(self):
        win = self._window()
        spec = json.loads(MANIFEST.read_text(encoding="utf-8"))["states"]
        try:
            self.assertTrue(win.play_action("shock"))
            self.assertIsNotNone(win.action)
            self.assertAlmostEqual(win._action_max,
                                   float(spec["shock"]["max_seconds"]),
                                   "保险丝上限必须显式来自 manifest")
            self.assertGreater(win._action_started, 0.0)
            # 预算内不许误杀
            win._tick()
            self.assertIsNotNone(win.action, "预算内的正常表演不许被掐")
        finally:
            win.quit_app()

    def test_loop_mode_fuse_disabled(self):
        win = self._window()
        try:
            self.assertTrue(win.play_action("cheer", play="loop"))
            self.assertEqual(win._action_max, 0.0, "loop 档不设防")
        finally:
            win.quit_app()

    def test_overtime_forces_return_to_idle_and_disarms(self):
        win = self._window()
        try:
            self.assertTrue(win.play_action("shock"))
            # 把秒表拨回 99 秒之前：下一拍必然超时
            win._action_started -= win._action_max + 99.0
            win._tick()
            self.assertIsNone(win.action, "超时后必须强制谢幕")
            self.assertEqual(win.current, "idle", "保险丝强制回发呆")
            self.assertEqual(win._action_max, 0.0, "谢幕即撤防")
        finally:
            win.quit_app()


# ================================================================ 任务三
class TestManifestV4(unittest.TestCase):
    """真实 manifest 的 v4 schema：once 三字段齐备、转场剥离、引用不回潮。"""

    @classmethod
    def setUpClass(cls):
        cls.states = json.loads(
            MANIFEST.read_text(encoding="utf-8"))["states"]

    def test_once_states_carry_v4_fields(self):
        for name in ONCE_STATES:
            spec = self.states[name]
            self.assertEqual(spec.get("play"), "once", f"{name} 必须 once")
            self.assertIn("transition_frames", spec,
                          f"{name} 必须显式声明转场段（允许空列表）")
            self.assertIsInstance(spec["transition_frames"], list)
            self.assertIn("hold_seconds", spec, f"{name} 必须显式声明定格秒数")
            self.assertGreaterEqual(float(spec["hold_seconds"]), 0.0)
            self.assertIn("max_seconds", spec, f"{name} 必须显式声明保险丝上限")
            self.assertGreater(float(spec["max_seconds"]), 0.0)
        # 主人拍板的定格现值：shock/cry 定格 1.2 秒，dance 不定格
        self.assertEqual(self.states["shock"]["hold_seconds"], 1.2)
        self.assertEqual(self.states["cry"]["hold_seconds"], 1.2)
        self.assertEqual(self.states["dance"]["hold_seconds"], 0.0)

    def test_frames_stripped_of_transition_and_legacy_refs(self):
        for name in ONCE_STATES:
            spec = self.states[name]
            for rel in spec["frames"]:
                stem = pathlib.Path(rel).stem
                self.assertFalse(stem.startswith(f"{name}_T"),
                                 f"{name} 的 frames 不得再混 _T 转场帧")
                self.assertFalse(stem.startswith(f"{name}_Q"),
                                 f"{name} 的 frames 不得再混 _Q 转场帧")
                self.assertFalse("_S" in stem,
                                 f"{name} 的 frames 不得回潮引用旧 _S 插帧")
            # 剥离后的表演帧数锁死（实际磁盘形态：28/28/61 张表演帧）
            expect = {"shock": 28, "cry": 28, "dance": 61}
            self.assertEqual(len(spec["frames"]), expect[name],
                             f"{name} 表演段帧数必须恰为 {expect[name]}")
            tag = "F" if name == "dance" else "D"
            self.assertEqual(
                spec["frames"],
                [f"states/{name}_{tag}{i:03d}.png" for i in range(expect[name])],
                f"{name} 表演帧必须是本状态的连续编号帧")

    def test_transition_frames_reference_real_files(self):
        for name in ONCE_STATES:
            rels = self.states[name]["transition_frames"]
            self.assertTrue(rels, f"{name} 必须有转场拼接帧（压扁回弹 _Q）")
            for rel in rels:
                p = STATES_DIR / pathlib.Path(rel).name
                self.assertTrue(p.exists(), f"{name} 转场帧缺图: {rel}")
                stem = pathlib.Path(rel).stem
                self.assertTrue(stem.startswith(f"{name}_Q"),
                                f"{name} 转场帧必须是 _Q 序列: {rel}")

    def test_max_seconds_covers_timeline_plus_grace(self):
        for name in ONCE_STATES:
            spec = self.states[name]
            ms = float(spec["frame_ms"])
            timeline = (len(spec["frames"]) * ms
                        + float(spec["hold_seconds"]) * 1000.0
                        + len(spec["transition_frames"]) * ms) / 1000.0
            max_s = float(spec["max_seconds"])
            self.assertGreaterEqual(max_s, timeline + 1.0 - 1e-9,
                                    f"{name} 保险丝必须盖住全时间线+1 秒宽限")
            self.assertLessEqual(max_s, timeline + 1.5,
                                 f"{name} 保险丝不许宽到失去意义")


class TestIndependentTransitionBake(unittest.TestCase):
    """烘焙升级：bake_squash_return 输出独立 transition_frames，不再追加 frames。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)
        self.states = self.td / "states"
        self.states.mkdir()
        im = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse([0, 8, 47, 47], fill=(190, 50, 35, 255))
        for i in range(6):
            im.save(self.states / f"shock_D{i:03d}.png")
        im.save(self.states / "idle.png")
        self.entry = {"file": "states/shock_D000.png",
                      "frames": [f"states/shock_D{i:03d}.png" for i in range(6)],
                      "frame_ms": 33, "play": "once", "return_to": "idle",
                      "hold_seconds": 1.2}

    def tearDown(self):
        self._tmp.cleanup()

    def test_bake_outputs_transition_frames_field(self):
        frag = _bake(self.entry, self.states,
                     self.states / "idle.png")
        self.assertEqual(frag["frames"],
                         [f"states/shock_D{i:03d}.png" for i in range(6)],
                         "frames 必须保持纯表演帧，不许追加转场")
        self.assertEqual(frag["transition_frames"],
                         [f"states/shock_Q{i:03d}.png" for i in range(30)],
                         "转场帧必须独立成 transition_frames 字段")
        self.assertAlmostEqual(frag["hold_seconds"], 1.2)

    def test_bake_computes_max_seconds_with_grace(self):
        frag = _bake(self.entry, self.states, self.states / "idle.png")
        # 表演 6*33 + 定格 1200 + 转场 30*33 + 宽限 1000 = 3388ms
        self.assertAlmostEqual(frag["max_seconds"], 3.388, places=9)

    def test_bake_without_hold_defaults_zero(self):
        entry = dict(self.entry)
        entry.pop("hold_seconds")
        frag = _bake(entry, self.states, self.states / "idle.png")
        self.assertEqual(frag["hold_seconds"], 0.0)


def _bake(entry, states_dir, idle_path):
    import prep_assets
    return prep_assets.bake_squash_return(entry, states_dir, idle_path,
                                          total_frames=30, name="shock")


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
