"""动作流畅度四连优化测试：插帧烘焙 / 乒乓循环 / 闲置自动入睡。

全程无 GUI、无网络：帧用 Pillow 程序化生成，manifest 用临时目录伪条目，
dance 跳过逻辑用伪 manifest 判定（帧文件不必存在）。
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops, ImageDraw  # noqa: E402

import prep_assets  # noqa: E402
from petfw.action_player import ActionPlayer  # noqa: E402
from petfw.animator_core import (  # noqa: E402
    FrameClock,
    next_index_pingpong,
    schedule,
)
from petfw.idle_policy import should_auto_sleep  # noqa: E402


def _solid(rgba, size=32):
    return Image.new("RGBA", (size, size), rgba)


def _blob(shift, size=64, color=(190, 50, 35, 255)):
    """程序化小帧：白底 + 平移的椭圆，相邻帧肉眼可辨。"""
    im = Image.new("RGBA", (size, size), (250, 250, 250, 255))
    d = ImageDraw.Draw(im)
    d.ellipse([8 + shift, 12, 40 + shift, 44], fill=color)
    return im


def _mean_diff(a, b):
    if a.size != b.size:
        b = b.resize(a.size)
    h = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).histogram()
    total = sum(h)
    if not total:
        return 0.0
    return sum(v * i for i, v in enumerate(h)) / float(total)


def _write_frames(directory, images, stem, tag="f"):
    for i, im in enumerate(images):
        im.save(directory / f"{stem}_{tag}{i}.png")


class TestInterpolate(unittest.TestCase):
    def setUp(self):
        self.a = _solid((255, 40, 40, 255))
        self.b = _solid((40, 40, 255, 255))

    def test_step_count(self):
        self.assertEqual(len(prep_assets.interpolate(self.a, self.b, 2)), 2)
        self.assertEqual(len(prep_assets.interpolate(self.a, self.b, 5)), 5)
        self.assertEqual(prep_assets.interpolate(self.a, self.b, 0), [])

    def test_endpoints_not_repeated_and_monotonic(self):
        mids = prep_assets.interpolate(self.a, self.b, 3)
        for mid in mids:
            self.assertGreater(_mean_diff(mid, self.a), 0.0,
                               "中间帧不许与起点重复")
            self.assertGreater(_mean_diff(mid, self.b), 0.0,
                               "中间帧不许与终点重复")
        # 越靠后的中间帧离终点越近（渐进单调）
        gaps = [_mean_diff(m, self.b) for m in mids]
        self.assertTrue(all(x > y for x, y in zip(gaps, gaps[1:])),
                        f"必须单调逼近终点: {gaps}")

    def test_deterministic(self):
        first = prep_assets.interpolate(self.a, self.b, 2)
        again = prep_assets.interpolate(self.a, self.b, 2)
        for x, y in zip(first, again):
            self.assertEqual(x.tobytes(), y.tobytes())

    def test_blend_differs_from_first_frame(self):
        (mid,) = prep_assets.interpolate(self.a, self.b, 1)
        self.assertGreater(_mean_diff(mid, self.a), 1.0,
                           "blend 结果必须和首帧拉开差距")


class TestBakeSmooth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)
        self.states = self.td / "states"
        self.states.mkdir()
        self.frames = [_blob(i * 3) for i in range(6)]
        _write_frames(self.states, self.frames, "laugh")
        # idle 特意用不同尺寸，逼烘焙层做画布对齐
        self.idle = _solid((120, 200, 90, 255), size=40)
        self.idle.save(self.states / "idle.png")
        self.entry = {
            "file": "states/laugh.png",
            "frames": [f"states/laugh_f{i}.png" for i in range(6)],
            "frame_ms": 40,
        }

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_state_frames_reads_in_entry_order(self):
        out = prep_assets.load_state_frames(self.states, self.entry)
        self.assertEqual(len(out), 6)
        for got, want in zip(out, self.frames):
            self.assertEqual(got.size, want.size)
            self.assertEqual(got.mode, "RGBA")
        self.assertEqual(out[0].tobytes(), self.frames[0].tobytes())
        self.assertNotEqual(out[0].tobytes(), out[5].tobytes())

    def test_bake_frame_count_formula(self):
        frag = prep_assets.bake_smooth(self.entry, self.states,
                                       blends=2,
                                       tail_to=self.states / "idle.png")
        # 6 + (6-1)*2 = 16 帧 + 尾部 2 帧余韵 = 18
        self.assertEqual(len(frag["frames"]), 18)
        self.assertEqual(len(list(self.states.glob("laugh_S*.png"))), 18)

    def test_bake_without_tail_is_16(self):
        frag = prep_assets.bake_smooth(self.entry, self.states, blends=2)
        self.assertEqual(len(frag["frames"]), 16)

    def test_bake_names_use_S_tag(self):
        frag = prep_assets.bake_smooth(self.entry, self.states, blends=2,
                                       tail_to=self.states / "idle.png")
        self.assertEqual(frag["frames"],
                         [f"states/laugh_S{i:03d}.png" for i in range(18)])

    def test_bake_fragment_fields(self):
        frag = prep_assets.bake_smooth(self.entry, self.states, blends=2,
                                       tail_to=self.states / "idle.png")
        self.assertIs(frag["pingpong"], True)
        self.assertIsInstance(frag["frame_ms"], int)
        self.assertTrue(60 <= frag["frame_ms"] <= 120)

    def test_bake_frame_ms_target_three_x_then_clamp(self):
        # 原时长 6*40=240ms，目标 3 倍 720ms / 18 帧 = 40 -> 下限钳到 60
        frag = prep_assets.bake_smooth(self.entry, self.states, blends=2)
        self.assertEqual(frag["frame_ms"], 60)
        # 区间内不钳：frame_ms=80，无尾帧 16 张 -> 6*80*3=1440 / 16 = 90
        entry80 = dict(self.entry, frame_ms=80)
        self.assertEqual(prep_assets.bake_smooth(
            entry80, self.states, blends=2)["frame_ms"], 90)
        # 上限钳：frame_ms=400 -> 7200 / 18 = 400 -> 120
        entry400 = dict(self.entry, frame_ms=400)
        self.assertEqual(prep_assets.bake_smooth(
            entry400, self.states, blends=2)["frame_ms"], 120)

    def test_tail_ends_exactly_on_idle(self):
        frag = prep_assets.bake_smooth(self.entry, self.states, blends=2,
                                       tail_to=self.states / "idle.png")
        baked = prep_assets.load_state_frames(self.states, frag)
        idle = Image.open(self.states / "idle.png").convert("RGBA")
        last = baked[-1]
        self.assertEqual(last.size, self.frames[0].size)
        self.assertIsNone(ImageChops.difference(
            last, idle.resize(self.frames[0].size)).getbbox(),
            "最后一张必须是 idle 原图（收招定格）")
        # 倒数第二张是 50% 融合帧：与两端都不同
        second_last = baked[-2]
        self.assertGreater(_mean_diff(second_last, last), 0.0)
        self.assertGreater(_mean_diff(second_last, baked[-3]), 0.0)

    def test_bake_deterministic(self):
        frag_a = prep_assets.bake_smooth(self.entry, self.states, blends=2,
                                         tail_to=self.states / "idle.png")
        pixels_a = [im.tobytes() for im in
                    prep_assets.load_state_frames(self.states, frag_a)]
        frag_b = prep_assets.bake_smooth(self.entry, self.states, blends=2,
                                         tail_to=self.states / "idle.png")
        pixels_b = [im.tobytes() for im in
                    prep_assets.load_state_frames(self.states, frag_b)]
        self.assertEqual(pixels_a, pixels_b)

    def test_bake_slow_mode_relaxed_clamp(self):
        """特别慢速档（eat/sleep）：blends=3、clamp 下限放宽到 140。"""
        frag = prep_assets.bake_smooth(self.entry, self.states, blends=3,
                                       tail_to=self.states / "idle.png",
                                       ms_min=140, ms_max=200)
        # 6 + 5*3 = 21 帧 + 尾部 2 帧余韵 = 23
        self.assertEqual(len(frag["frames"]), 23)
        # 原时长 240ms*3 = 720 / 23 ≈ 31 -> 下限钳到 140
        self.assertEqual(frag["frame_ms"], 140)
        # 慢动作享受：循环总时长必须 3 秒以上
        self.assertGreaterEqual(frag["frame_ms"] * len(frag["frames"]), 3000)
        self.assertIs(frag["pingpong"], True)


class TestBakeAllSkipsDance(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)
        self.states = self.td / "states"
        self.states.mkdir()
        _write_frames(self.states, [_blob(i * 3) for i in range(6)], "laugh")
        _write_frames(self.states, [_blob(i * 3) for i in range(6)], "eat")
        _write_frames(self.states, [_blob(i * 3) for i in range(6)], "sleep")
        _solid((120, 200, 90, 255), size=40).save(self.states / "idle.png")
        manifest = {
            "pet": "my-pet",
            "states": {
                "dance": {"file": "states/dance_F000.png",
                          "frames": [f"states/dance_F{i:03d}.png"
                                     for i in range(61)],
                          "frame_ms": 41, "play": "once",
                          "return_to": "idle"},
                "laugh": {"file": "states/laugh.png",
                          "frames": [f"states/laugh_f{i}.png"
                                     for i in range(6)],
                          "frame_ms": 40, "play": "once",
                          "return_to": "idle"},
                "eat": {"file": "states/eat.png",
                        "frames": [f"states/eat_f{i}.png" for i in range(6)],
                        "frame_ms": 40, "play": "once",
                        "return_to": "idle"},
                "sleep": {"file": "states/sleep.png",
                          "frames": [f"states/sleep_f{i}.png"
                                     for i in range(6)],
                          "frame_ms": 40, "play": "once",
                          "return_to": "idle"},
                "idle": {"file": "states/idle.png"},
                "cheer": {"file": "states/cheer.png"},
            },
        }
        self.manifest_path = self.td / "manifest.json"
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_short_multi_frame_states_baked(self):
        patched = prep_assets.bake_all_smooth(
            manifest_path=self.manifest_path, states_dir=self.states,
            tail=self.states / "idle.png")
        self.assertEqual(sorted(patched), ["eat", "laugh", "sleep"],
                         "dance(61帧) 与单图状态必须跳过")
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        dance = data["states"]["dance"]
        self.assertEqual(len(dance["frames"]), 61, "dance 帧表一字不动")
        self.assertNotIn("pingpong", dance, "dance 不许被加 pingpong")
        laugh = data["states"]["laugh"]
        self.assertEqual(len(laugh["frames"]), 18)
        self.assertIs(laugh["pingpong"], True)
        self.assertEqual(laugh["frame_ms"], 60)
        self.assertNotIn("pingpong", data["states"]["cheer"])
        self.assertFalse(list(self.states.glob("dance_S*.png")))

    def test_eat_sleep_get_slow_bake(self):
        """eat/sleep 特别慢速档：23 帧、frame_ms 落在 140~200、循环 ≥3 秒。"""
        patched = prep_assets.bake_all_smooth(
            manifest_path=self.manifest_path, states_dir=self.states,
            tail=self.states / "idle.png")
        for name in ("eat", "sleep"):
            frag = patched[name]
            self.assertEqual(len(frag["frames"]), 23, name)
            self.assertIs(frag["pingpong"], True, name)
            self.assertTrue(140 <= frag["frame_ms"] <= 200,
                            f"{name} frame_ms={frag['frame_ms']}")
            self.assertGreaterEqual(
                frag["frame_ms"] * len(frag["frames"]), 3000,
                f"{name} 循环总时长必须 3 秒以上")
            self.assertEqual(
                len(list(self.states.glob(f"{name}_S*.png"))), 23, name)

    def test_baked_files_exist_on_disk(self):
        prep_assets.bake_all_smooth(
            manifest_path=self.manifest_path, states_dir=self.states,
            tail=self.states / "idle.png")
        names = sorted(p.name for p in self.states.glob("laugh_S*.png"))
        self.assertEqual(names, [f"laugh_S{i:03d}.png" for i in range(18)])


class TestNextIndexPingpong(unittest.TestCase):
    def test_n2_oscillates(self):
        # n=2：0 -(+1)-> 1 -(+1 撞墙反向)-> 0 -(-1 撞墙反向)-> 1 ...
        self.assertEqual(next_index_pingpong(0, 2, 1), (1, 1))
        self.assertEqual(next_index_pingpong(1, 2, 1), (0, -1))
        self.assertEqual(next_index_pingpong(0, 2, -1), (1, 1))
        self.assertEqual(next_index_pingpong(1, 2, -1), (0, -1),
                         "还没撞到头墙，方向不变")

    def test_n3_round_trip(self):
        # 0 1 2 1 0 1 2 1 0 ...：到尾反向、回头正向
        i, d, seen = 0, 1, []
        for _ in range(8):
            i, d = next_index_pingpong(i, 3, d)
            seen.append(i)
        self.assertEqual(seen, [1, 2, 1, 0, 1, 2, 1, 0])

    def test_degenerate_inputs_safe(self):
        self.assertEqual(next_index_pingpong(0, 0, 1), (0, 1))
        self.assertEqual(next_index_pingpong(0, 1, 1), (0, 1))
        self.assertEqual(next_index_pingpong(0, 3, 0), (1, 1), "方向 0 当正向")
        self.assertEqual(next_index_pingpong(None, 3, 1), (0, 1),
                         "垃圾下标一律归位正向")


class TestFrameClockPingpong(unittest.TestCase):
    def test_default_clock_still_loops(self):
        clock = FrameClock(60)
        seen = [clock.advance(3) for _ in range(6)]
        self.assertEqual(seen, [1, 2, 0, 1, 2, 0])

    def test_pingpong_clock_round_trip(self):
        clock = FrameClock(60, pingpong=True)
        seen = [clock.advance(3) for _ in range(8)]
        self.assertEqual(seen, [1, 2, 1, 0, 1, 2, 1, 0])

    def test_pingpong_reset_restores_direction(self):
        clock = FrameClock(60, pingpong=True)
        clock.advance(3)
        clock.advance(3)
        clock.advance(3)      # 2 -(撞尾墙)-> 1，此刻已反向
        self.assertEqual(clock.direction, -1)
        clock.reset()
        self.assertEqual(clock.direction, 1)

    def test_schedule_accepts_pingpong_kwarg(self):
        # 乒乓只改走帧顺序不改节拍：间隔计算保持原语义
        self.assertEqual(schedule(6, 120, True, pingpong=True), 120)
        self.assertEqual(schedule(6, 120, False, pingpong=True), 240)


class TestActionPlayerPingpong(unittest.TestCase):
    def _player(self, extra):
        spec = {"frames": [f"f{i}" for i in range(4)], "frame_ms": 100,
                "play": "loop"}
        spec.update(extra)
        player = ActionPlayer()
        player.start(spec)
        return player

    def test_loop_pingpong_round_trip(self):
        player = self._player({"pingpong": True})
        seen = [player.tick(0.1) for _ in range(9)]
        # 0 1 2 3 2 1 0 1 2 3 ...（首帧由宿主先渲染，tick 从 1 起）
        self.assertEqual(seen, [1, 2, 3, 2, 1, 0, 1, 2, 3])

    def test_once_ignores_pingpong_and_terminates(self):
        spec = {"frames": [f"f{i}" for i in range(4)], "frame_ms": 100,
                "play": "once", "pingpong": True}
        player = ActionPlayer()
        player.start(spec)
        seen = []
        for _ in range(10):
            idx = player.tick(0.1)
            if idx is None:
                seen.append(None)
                break
            seen.append(idx)
        self.assertEqual(seen, [1, 2, 3, None], "once 播到尾即谢幕，不反向")
        self.assertTrue(player.done)

    def test_loop_without_pingpong_unchanged(self):
        player = self._player({})
        seen = [player.tick(0.1) for _ in range(6)]
        self.assertEqual(seen, [1, 2, 3, 0, 1, 2])


class TestIdlePolicy(unittest.TestCase):
    def test_quiet_threshold_boundary(self):
        self.assertFalse(should_auto_sleep(89.9, "idle"))
        self.assertTrue(should_auto_sleep(90.0, "idle"))
        self.assertTrue(should_auto_sleep(3600.0, "idle"))

    def test_only_when_current_is_idle(self):
        self.assertFalse(should_auto_sleep(1000, "cheer"))
        self.assertFalse(should_auto_sleep(1000, "sleep"))

    def test_bubble_blocks_sleep(self):
        self.assertFalse(should_auto_sleep(1000, "idle", bubble_visible=True))
        self.assertTrue(should_auto_sleep(1000, "idle", bubble_visible=False))

    def test_default_bubble_not_visible(self):
        self.assertTrue(should_auto_sleep(91, "idle"))

    def test_garbage_inputs_safe(self):
        self.assertFalse(should_auto_sleep(None, "idle"))
        self.assertFalse(should_auto_sleep("abc", "idle"))


if __name__ == "__main__":
    unittest.main()
