"""程序剪纸六拍舞（six_beat 配方 + dance6 常驻循环档）单元测试。

千问阅舞分析钦定的六拍循环：1垂臂起拍 2斜上大划弧 3回拳卡重拍 4挥臂蓄力
5双臂展开 6高举过头顶；循环点在第 6 拍后。测试锁定：
- 配方注册与 KNOWN_OPS 新 op（sweep 扫摆 / stretch 拉伸脉冲）；
- 帧数公式（fps=30、cycles=3 → 108 帧）、同 seed 逐字节确定性；
- 六拍关键姿态存在（倾角>15°、回拳 0.85、横向>1.1、纵向拉伸+上移）；
- 弧线残影与拍点粒子；第 6 拍末回第 1 拍姿态的无缝循环点；
- 烘焙产物 dance6_D*.png 与 manifest dance6 词条合法性；
- 配乐文件（抽音轨 m4a）与解析纯函数；宿主菜单与配乐接线。
姿态断言走 _Plan 白盒（compose 的纯逻辑层），帧图断言全用程序合成
替身，绝不触碰真实表情包图片。全程无 GUI 无网络。
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from petfw.cutout_anim import (  # noqa: E402
    KNOWN_OPS,
    RECIPES,
    Recipe,
    _Plan,
    compose,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
MANIFEST = ASSETS / "manifest.json"

# 六拍帧窗边界（fps=30 → 1 拍 = beats 帧；单循环 37 帧）
BEAT_EDGES = (0, 7, 17, 20, 23, 29, 37)
SIX_BEAT = RECIPES["six_beat"]
TOTAL = 111  # 37 帧/循环 × 3 循环


def _dummy_base(w=145, h=152):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((w // 4, h // 5, 3 * w // 4, 4 * h // 5),
              fill=(250, 210, 90, 255))
    return im


def _poses(recipe=None, total=TOTAL):
    plan = _Plan(recipe or SIX_BEAT, seed=42)
    return [plan.state_at(f) for f in range(plan.total)]


class TestSixBeatRecipe(unittest.TestCase):
    """配方注册、新 op 白名单与六拍分镜结构。"""

    def test_recipe_registered_with_new_ops(self):
        self.assertIn("six_beat", RECIPES)
        self.assertIn("sweep", KNOWN_OPS, "缺扫摆 op")
        self.assertIn("stretch", KNOWN_OPS, "缺拉伸脉冲 op")
        SIX_BEAT.validate()

    def test_beat_sequence_is_six_ops(self):
        names = [op["op"] for op in SIX_BEAT.steps]
        self.assertEqual(names, ["squash", "sweep", "squash",
                                 "wiggle", "stretch", "stretch"],
                         "六拍分镜顺序不许漂移")
        beats = [op["beats"] for op in SIX_BEAT.steps]
        self.assertEqual(beats, [7, 10, 3, 3, 6, 8])
        self.assertEqual(SIX_BEAT.fps, 30)
        self.assertEqual(SIX_BEAT.cycles, 3)
        self.assertLessEqual(SIX_BEAT.steps[1]["deg"], -20,
                             "拍2 大划弧至少扫出 -20°")
        self.assertGreaterEqual(SIX_BEAT.steps[4]["sx"], 1.15,
                                "拍5 展开横向至少 1.15")
        self.assertGreaterEqual(SIX_BEAT.steps[5]["sy"], 1.15,
                                "拍6 高举纵向至少 1.15")

    def test_frame_count_111(self):
        self.assertEqual(len(compose(SIX_BEAT, _dummy_base(), seed=42)),
                         TOTAL, "帧数 = 37 帧/循环 × 3 循环 @30fps")
        loop_s = TOTAL * 33 / 1000.0
        self.assertGreaterEqual(loop_s, 3.0, "循环须在 3~4 秒")
        self.assertLessEqual(loop_s, 4.0, "循环须在 3~4 秒")

    def test_same_seed_identical_bytes(self):
        base = _dummy_base()
        f1 = compose(SIX_BEAT, base, seed=42)
        f2 = compose(SIX_BEAT, base, seed=42)
        self.assertEqual(len(f1), len(f2))
        for a, b in zip(f1, f2):
            self.assertEqual(a.tobytes(), b.tobytes())


class TestSixBeatPoses(unittest.TestCase):
    """六拍关键姿态存在性与循环点衔接（_Plan 白盒）。"""

    @classmethod
    def setUpClass(cls):
        cls.poses = _poses()

    def _window(self, beat, cycle=0):
        a, b = BEAT_EDGES[beat - 1], BEAT_EDGES[beat]
        off = cycle * BEAT_EDGES[-1]
        return range(a + off, b + off)

    def test_beat2_big_arc_tilt_over_15deg(self):
        rots = [abs(self.poses[f]["rot"]) for f in self._window(2)]
        self.assertGreater(max(rots), 15.0, "拍2 必须有大角度斜线（>15°）")

    def test_beat1_squat_and_beat3_punch_squash(self):
        squat = min(self.poses[f]["sy"] for f in self._window(1))
        self.assertLessEqual(squat, 0.89, "拍1 垂臂下蹲要有明显压扁")
        punch = min(self.poses[f]["sy"] for f in self._window(3))
        self.assertLessEqual(punch, 0.86, "拍3 回拳卡点要 squash 到 0.85 档")

    def test_beat5_spread_wide(self):
        sx = max(self.poses[f]["sx"] for f in self._window(5))
        self.assertGreater(sx, 1.1, "拍5 双臂展开横向须放大 >1.1")

    def test_beat6_raise_high(self):
        sy = max(self.poses[f]["sy"] for f in self._window(6))
        dy = min(self.poses[f]["dy"] for f in self._window(6))
        self.assertGreater(sy, 1.1, "拍6 高举纵向须拉伸 >1.1")
        self.assertLessEqual(dy, -8, "拍6 要有可见上移")

    def test_loop_seam_returns_to_neutral(self):
        """第 6 拍末尾自然衔接回第 1 拍姿态：每个循环缝上全部归中。"""
        seam = BEAT_EDGES[-1]
        for f in (seam - 1, seam, 2 * seam - 1, 2 * seam):
            st = self.poses[f]
            self.assertAlmostEqual(st["rot"], 0.0, places=6, msg=f)
            self.assertAlmostEqual(st["sx"], 1.0, places=6, msg=f)
            self.assertAlmostEqual(st["sy"], 1.0, places=6, msg=f)
            self.assertEqual(st["dy"], 0, msg=f)


class TestTrailAndParticles(unittest.TestCase):
    """弧线残影与拍点星粒：sweep.trail 与 stretch 的 symbol 粒子。"""

    def test_sweep_mid_frame_has_ghosts(self):
        plan = _Plan(SIX_BEAT, seed=42)
        mid = plan.state_at(10)      # 拍2（6..15）中段
        self.assertTrue(mid.get("ghosts"), "大划弧中段必须有残影队列")
        for ang, strength in mid["ghosts"]:
            self.assertGreater(abs(ang), 2.0, "残影角度太近等于没有")
            self.assertTrue(0.0 < strength < 1.0)
        edge = plan.state_at(6)      # 拍2 起点不给残影
        self.assertFalse(edge.get("ghosts"))

    def test_trail_renders_different_bytes(self):
        base = _dummy_base()
        with_trail = compose(SIX_BEAT, base, seed=42)
        steps = [dict(op) for op in SIX_BEAT.steps]
        steps[1] = dict(steps[1], trail=False)
        plain = compose(Recipe(state="six_beat", fps=30, cycles=3,
                               steps=steps), base, seed=42)
        self.assertEqual(len(with_trail), len(plain))
        self.assertNotEqual(with_trail[10].tobytes(), plain[10].tobytes(),
                            "残影必须在画面上留下可见差异")

    def test_stretch_star_particles_synced_to_beats(self):
        plan = _Plan(SIX_BEAT, seed=42)
        specs = plan.particle_specs(plan.total, (0, 0, 145, 152))
        stars = [p for p in specs if p["symbol"] == "star"]
        self.assertEqual(len(stars), 18, "每循环 2+4 颗星 × 3 循环")
        for p in stars:
            self.assertTrue(0 <= p["birth"] < plan.total)

    def test_star_pixels_visible_in_beat5_6(self):
        frames = compose(SIX_BEAT, _dummy_base(), seed=42)
        found = 0
        for f in frames[22:36]:
            px = f.load()
            for y in range(0, f.height, 2):
                for x in range(0, f.width, 2):
                    r, g, b, a = px[x, y][:4]
                    if a > 180 and r > 230 and 170 < g < 235 and b < 130:
                        found += 1
        self.assertGreaterEqual(found, 8, "拍5/拍6 的星粒必须真的画出来")


class TestDance6ManifestAndAssets(unittest.TestCase):
    """烘焙产物与 manifest dance6 词条（loop 常驻循环档）。"""

    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            MANIFEST.read_text(encoding="utf-8"))
        cls.spec = cls.manifest["states"]["dance6"]

    def test_dance6_entry_is_resident_loop(self):
        self.assertEqual(self.spec["play"], "loop",
                         "dance6 是常驻可点播的循环舞")
        self.assertFalse(self.spec.get("pingpong"), "正向循环不走乒乓")
        self.assertEqual(int(self.spec["frame_ms"]), 33)
        self.assertNotIn("max_seconds", self.spec,
                         "loop 常驻档不设保险丝")
        self.assertNotIn("transition_frames", self.spec,
                         "循环舞没有回程转场段")

    def test_baked_frames_exist_and_counted(self):
        frames = self.spec["frames"]
        self.assertTrue(90 <= len(frames) <= 120, "烘焙帧数须在 90~120")
        self.assertEqual(self.spec["file"], frames[0], "file 必须指向首帧")
        self.assertEqual(
            frames,
            [f"states/dance6_D{i:03d}.png" for i in range(len(frames))],
            "烘焙帧必须是 dance6_D 连续编号")
        for rel in frames:
            p = ASSETS / rel
            self.assertTrue(p.exists(), f"缺烘焙帧: {rel}")
            with Image.open(p) as im:
                self.assertEqual(im.mode, "RGBA")
                self.assertLessEqual(im.height, 256)
        loop_s = len(frames) * 33 / 1000.0
        self.assertGreaterEqual(loop_s, 3.0, "循环时长须在 3~4 秒")
        self.assertLessEqual(loop_s, 4.0, "循环时长须在 3~4 秒")


class TestDance6Bgm(unittest.TestCase):
    """配乐：抽音轨落盘 + 解析纯函数。"""

    def test_bgm_audio_file_exists(self):
        m4a = ROOT / "assets" / "local" / "dance6_bgm.m4a"
        self.assertTrue(m4a.is_file(), "缺抽好的音轨 dance6_bgm.m4a")
        self.assertGreater(m4a.stat().st_size, 100_000,
                           "23 秒音轨不该只有几十 KB")
        src = ROOT / "assets" / "local" / "source" / "跳舞结算_30到53秒.mp4"
        self.assertTrue(src.is_file(), "源视频必须仍在（回滚与配乐兜底）")

    def test_resolve_prefers_m4a_then_mp4_then_none(self):
        from petfw.song_flow import resolve_dance6_bgm
        hit = resolve_dance6_bgm((ROOT,))
        self.assertIsNotNone(hit)
        self.assertEqual(hit.suffix.lower(), ".m4a",
                         "抽好的音轨必须优先于源视频")
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        local = tmp / "assets" / "local" / "source"
        local.mkdir(parents=True)
        shutil.copy(ROOT / "assets" / "local" / "source"
                    / "跳舞结算_30到53秒.mp4", local / "跳舞结算_30到53秒.mp4")
        fall = resolve_dance6_bgm((tmp,))
        self.assertIsNotNone(fall, "缺音轨时必须回落源视频")
        self.assertEqual(fall.suffix.lower(), ".mp4")
        self.assertIsNone(resolve_dance6_bgm((tmp / "nowhere",)),
                          "全缺时安静返回 None")


class _FakeMusic:
    def __init__(self, playing=False):
        self.play_calls = []
        self.stop_calls = 0
        self._playing = playing

    def is_playing(self):
        return self._playing

    def play(self, path, volume):
        self.play_calls.append((path, volume))
        return True

    def stop(self):
        self.stop_calls += 1
        self._playing = False


class TestSixBeatHostWiring(unittest.TestCase):
    """宿主接线：play_six_beat 点播循环舞 + 配乐放一遍即停。"""

    def _window(self, bgm_path, settlement_open=False):
        import petfw.host as hostmod
        from petfw.host import PetWindow
        win = PetWindow.__new__(PetWindow)   # 不跑 __init__，只借方法
        win.settlement_open = settlement_open
        win.played = []

        def _fake_play_action(name, play=None, hold_tail_ms=None, spec=None):
            if name not in win.states:      # 与真实 play_action 同款裁决
                return False
            win.played.append((name, play))
            return True

        win.play_action = _fake_play_action
        win.music = _FakeMusic()
        win._music_volume = 0.6
        win.states = {"dance6": {"frames": ["states/dance6_D000.png"],
                                 "frame_ms": 33, "play": "loop"}}
        patcher = unittest.mock.patch.object(
            hostmod, "resolve_dance6_bgm", lambda bases=(): bgm_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        return win

    def test_play_six_beat_loops_dance_and_plays_bgm_once(self):
        bgm = pathlib.Path("D:/nowhere/dance6_bgm.m4a")
        win = self._window(bgm_path=bgm)
        self.assertTrue(win.play_six_beat())
        self.assertEqual(win.played, [("dance6", "loop")], "舞走 loop 常驻档")
        self.assertEqual(win.music.play_calls, [(bgm, 0.6)],
                         "配乐音量取 [sound] music_volume")
        self.assertEqual(win.music.stop_calls, 1,
                         "开播前先顶掉可能在播的点歌")

    def test_play_six_beat_without_bgm_still_dances(self):
        win = self._window(bgm_path=None)
        self.assertTrue(win.play_six_beat())
        self.assertEqual(win.played, [("dance6", "loop")])
        self.assertEqual(win.music.play_calls, [],
                         "配乐缺失时静默开跳，绝不拦舞")

    def test_play_six_beat_silent_during_settlement(self):
        win = self._window(bgm_path=pathlib.Path("x.m4a"),
                           settlement_open=True)
        self.assertFalse(win.play_six_beat(), "结算画面打开期间一律忽略")
        self.assertEqual(win.played, [])
        self.assertEqual(win.music.play_calls, [])

    def test_play_six_beat_needs_dance6_state(self):
        win = self._window(bgm_path=pathlib.Path("x.m4a"))
        win.states = {}
        self.assertFalse(win.play_six_beat(), "dance6 缺图时不诈尸")

    def test_menu_builder_lists_six_beat_entry(self):
        """情绪组末尾必须有「六拍舞」词条，触发走 play_six_beat。"""
        from PySide6.QtWidgets import QApplication, QMenu
        from petfw.host import PetWindow
        QApplication.instance() or QApplication([])

        class _Timer:
            def isActive(self):
                return True

        class _Win:
            states = {"dance6": {}}
            reminder_timer = _Timer()

            def play_six_beat(self):
                self.hits = getattr(self, "hits", 0) + 1

            def scan_growth(self):
                pass

            def _toggle_reminders(self, on):
                pass

            def quit_app(self):
                pass

        win = _Win()
        menu = QMenu()
        PetWindow.build_actions_menu(menu, win)
        acts = {a.text(): a for a in menu.actions() if not a.isSeparator()}
        self.assertIn("六拍舞", acts, "情绪组缺六拍舞词条")
        acts["六拍舞"].trigger()
        self.assertEqual(win.hits, 1, "词条必须触发 play_six_beat")


if __name__ == "__main__":
    unittest.main()
