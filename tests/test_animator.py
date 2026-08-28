"""逐帧动画引擎（任务五）：animator_core 纯逻辑 + GIF 拆帧管线端到端。

全程无 GUI、无网络；GIF 用 Pillow 在临时目录程序化生成，不依赖任何真实素材。
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw.animator_core import (  # noqa: E402
    FrameClock,
    next_index,
    sample_frames,
    schedule,
    validate_rate,
)


def _sorted_strict(seq):
    return all(b > a for a, b in zip(seq, seq[1:]))


class TestSampleFrames(unittest.TestCase):
    def test_total_within_cap_returns_full_range(self):
        self.assertEqual(sample_frames(1), [0])
        self.assertEqual(sample_frames(4), [0, 1, 2, 3])
        self.assertEqual(sample_frames(6), [0, 1, 2, 3, 4, 5])
        # GIF 只有 5 帧而 cap=10：也该原样全量
        self.assertEqual(sample_frames(5, 10), [0, 1, 2, 3, 4])

    def test_over_cap_samples_evenly_and_keeps_first(self):
        for total in (7, 13, 48, 300, 975):
            picks = sample_frames(total, 6)
            self.assertEqual(len(picks), 6, f"total={total}")
            self.assertTrue(_sorted_strict(picks), f"{total} -> {picks}")
            self.assertEqual(picks[0], 0, f"{total} 必须含首帧")
            for p in picks:
                self.assertTrue(0 <= p < total, f"{total} 下标越界: {p}")

    def test_all_indices_within_bounds_and_ints(self):
        picks = sample_frames(300)
        self.assertTrue(all(isinstance(i, int) for i in picks))

    def test_degenerate_inputs_safe(self):
        self.assertEqual(sample_frames(0), [])
        self.assertEqual(sample_frames(-3), [])
        self.assertEqual(sample_frames(10, 0), [])


class TestSchedule(unittest.TestCase):
    def test_normal_is_slow_double_base(self):
        # 平时慢速卖萌：间隔 = base 的两倍
        self.assertEqual(schedule(6, 120, False), 240)
        self.assertEqual(schedule(8, 90, False), 180)

    def test_celebrate_is_full_speed_base(self):
        # 结算/蹦跶时全速狂欢：间隔 = base 本身
        self.assertEqual(schedule(6, 120, True), 120)

    def test_degenerate_inputs_return_zero(self):
        self.assertEqual(schedule(0, 120, True), 0)   # 没帧
        self.assertEqual(schedule(6, 0, False), 0)    # 没节拍
        self.assertEqual(schedule(6, -50, True), 0)   # 非法基速


class TestNextIndex(unittest.TestCase):
    def test_steps_forward_and_wraps(self):
        self.assertEqual(next_index(0, 3), 1)
        self.assertEqual(next_index(1, 3), 2)
        self.assertEqual(next_index(2, 3), 0)   # 循环回绕
        self.assertEqual(next_index(997, 6), 998 % 6)

    def test_single_frame_and_empty_stay_put(self):
        self.assertEqual(next_index(0, 1), 0)
        self.assertEqual(next_index(0, 0), 0)   # 空列表不许崩


class TestValidateRate(unittest.TestCase):
    def test_boundaries_inclusive(self):
        self.assertEqual(validate_rate(0.5), 0.5)     # 下界合法
        self.assertEqual(validate_rate(4.0), 4.0)     # 上界合法

    def test_valid_middle_values(self):
        self.assertEqual(validate_rate(1.0), 1.0)
        self.assertEqual(validate_rate(2.5), 2.5)
        self.assertEqual(validate_rate("2.5"), 2.5)   # ini 里读出来是字符串
        self.assertEqual(validate_rate(" 1.5 "), 1.5)

    def test_invalid_values_return_none(self):
        for bad in (0.49, -0.4, 4.1, 100, "abc", "", "  ", None, object(),
                    True):   # bool 是 int 子类，也当非法防呆
            self.assertIsNone(validate_rate(bad), f"rate={bad!r}")


class TestFrameClock(unittest.TestCase):
    def test_interval_switches_with_mood(self):
        clock = FrameClock(80)
        self.assertEqual(clock.interval_ms(5, False), 160)
        self.assertEqual(clock.interval_ms(5, True), 80)

    def test_advance_cycles_index(self):
        clock = FrameClock(60)
        seen = [clock.advance(3) for _ in range(6)]
        self.assertEqual(seen, [1, 2, 0, 1, 2, 0])


# ------------------------------------------------- GIF 拆帧管线端到端
def _make_test_gif(path: pathlib.Path, n_frames=5, size=64, duration=110):
    """程序化生成小 GIF：白底 + 各帧不同颜色的椭圆（位置微移模拟动画）。

    duration=None 时刻意不写帧时长，模拟没有 duration 元数据的 GIF。
    """
    from PIL import Image, ImageDraw
    colors = [(220, 30, 30), (30, 90, 220), (240, 200, 20),
              (40, 170, 70), (160, 40, 200)]
    frames = []
    for k in range(n_frames):
        im = Image.new("RGB", (size, size), (250, 250, 250))
        d = ImageDraw.Draw(im)
        col = colors[k % len(colors)]
        d.ellipse([10 + k * 2, 12, 42 + k * 2, 44], fill=col)
        frames.append(im)
    kw = {"save_all": True, "append_images": frames[1:], "loop": 0}
    if duration is not None:
        kw["duration"] = duration
    frames[0].save(path, **kw)
    return frames


class TestGifPipeline(unittest.TestCase):
    def setUp(self):
        import prep_assets
        self.prep = prep_assets
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)
        self.raw = self.td / "raw"
        self.out = self.td / "states"
        self.raw.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_process_gif_outputs_uniform_cropped_frames(self):
        gif = self.raw / "dance.gif"
        _make_test_gif(gif, n_frames=5)
        info = self.prep.process_gif(gif, out_dir=self.out)
        names = [p.name for p in sorted(self.out.glob("*.png"))]
        self.assertEqual(names, [f"dance_f{i}.png" for i in range(5)])
        self.assertEqual(info["frames"],
                         [f"states/dance_f{i}.png" for i in range(5)])
        sizes = set()
        from PIL import Image
        for p in self.out.glob("*.png"):
            with Image.open(p) as im:
                sizes.add(im.size)
        self.assertEqual(len(sizes), 1, "所有帧必须统一裁到联合包围盒，防抖动")
        # 中位帧时长：duration=110 均匀 -> 取整 110
        self.assertEqual(info["frame_ms"], 110)

    def test_gif_without_duration_falls_back_to_120(self):
        gif = self.raw / "sleep.gif"
        _make_test_gif(gif, n_frames=3, duration=None)
        info = self.prep.process_gif(gif, out_dir=self.out)
        self.assertEqual(len(info["frames"]), 3)
        self.assertEqual(info["frame_ms"], 120, "无 duration 元数据退默认 120ms")

    def test_process_gif_caps_long_gifs_to_six_frames(self):
        gif = self.raw / "cheer.gif"
        _make_test_gif(gif, n_frames=25)
        info = self.prep.process_gif(gif, out_dir=self.out)
        self.assertEqual(len(info["frames"]), 6)
        self.assertEqual(len(list(self.out.glob("*.png"))), 6)

    def test_flood_clear_removed_border_but_kept_subject(self):
        gif = self.raw / "idle.gif"
        _make_test_gif(gif, n_frames=2)
        self.prep.process_gif(gif, out_dir=self.out)
        from PIL import Image
        im = Image.open(self.out / "idle_f0.png").convert("RGBA")
        w, h = im.size
        self.assertLess(w * h, 64 * 64, "抠图+裁剪后应明显小于原画布")
        corner = im.getpixel((0, 0))
        self.assertEqual(corner[3], 0, "四角必须是抠掉的透明区")
        center = im.getpixel((im.width // 2, im.height // 2))
        self.assertGreater(center[3], 200, "椭圆主体必须保留不透明")

    def test_missing_duration_defaults_120(self):
        # 帧时长信息全缺（存成 0）时退默认 120ms
        self.assertEqual(self.prep.median_frame_ms([], default=120), 120)
        self.assertEqual(self.prep.median_frame_ms([0, 0], default=120), 120)

    def test_median_frame_ms_picks_middle(self):
        self.assertEqual(self.prep.median_frame_ms([100, 120, 140]), 120)
        self.assertEqual(self.prep.median_frame_ms([50]), 50)
        self.assertEqual(self.prep.median_frame_ms([50, 150]), 100)
        self.assertEqual(self.prep.median_frame_ms([80, 0, 90, 0]), 85)


class TestManifestMerge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._tmp.name) / "manifest.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _base_manifest(self):
        return {
            "pet": "my-pet",
            "states": {
                "idle": {"file": "states/idle.png", "bob_amp": 3,
                         "period_ms": 2600, "tilt_deg": 0},
                "dance": {"file": "states/dance.png", "bob_amp": 9},
            },
        }

    def test_merge_adds_frames_and_keeps_other_keys(self):
        self.path.write_text(json.dumps(self._base_manifest(),
                                        ensure_ascii=False), encoding="utf-8")
        import prep_assets
        patch = {"dance": {"frames": ["states/dance_f0.png",
                                      "states/dance_f1.png"],
                           "frame_ms": 110}}
        data = prep_assets.merge_manifest_entries(patch, manifest_path=self.path)
        dance = data["states"]["dance"]
        self.assertEqual(dance["frames"], ["states/dance_f0.png",
                                           "states/dance_f1.png"])
        self.assertEqual(dance["frame_ms"], 110)
        self.assertEqual(dance["bob_amp"], 9, "既有键不能被抹掉")
        idle = data["states"]["idle"]
        self.assertEqual(idle["period_ms"], 2600, "其它状态不能被动")

    def test_merge_creates_manifest_when_absent(self):
        import prep_assets
        patch = {"eat": {"frames": ["states/eat_f0.png"], "frame_ms": 120}}
        data = prep_assets.merge_manifest_entries(patch, manifest_path=self.path)
        self.assertTrue(self.path.exists())
        self.assertEqual(data["states"]["eat"]["frames"],
                         ["states/eat_f0.png"])
        # 落盘内容可回读、格式稳定（json 标准缩进）
        again = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(again["states"]["eat"]["frame_ms"], 120)


class TestCollectMissingFramesSchema(unittest.TestCase):
    """load_states 双 schema：多帧条目按整组 frames 判定缺图。"""

    def test_frames_entry_complete_is_not_missing(self):
        from petfw.host import collect_missing
        states = {"dance": {"frames": ["states/dance_f0.png",
                                       "states/dance_f1.png"]}}
        core, optional = collect_missing(
            states, ["states/dance_f0.png", "states/dance_f1.png"])
        self.assertEqual((core, optional), ([], []))

    def test_frames_entry_partial_counts_as_missing_optional(self):
        from petfw.host import collect_missing
        states = {"dance": {"frames": ["states/dance_f0.png",
                                       "states/dance_f1.png"]},
                  "idle": {"frames": ["states/idle_f0.png"]}}
        core, optional = collect_missing(states, ["states/dance_f0.png"])
        self.assertEqual(core, ["idle"])      # 核心态多帧照样是硬门槛
        self.assertEqual(optional, ["dance"])

    def test_file_single_mode_still_supported(self):
        from petfw.host import collect_missing
        states = {"cheer": {"file": "states/cheer.png"}}
        self.assertEqual(collect_missing(states, ["states/cheer.png"]),
                         ([], []))


class TestTemplateBgmoKeys(unittest.TestCase):
    def test_template_has_bgm_settings(self):
        from petfw.config import TEMPLATE
        settle = TEMPLATE.split("[settlement]", 1)[1]
        self.assertIn("bgm = true", settle)
        self.assertIn("bgm_rate = 2.5", settle)
        self.assertIn("1.5", settle)   # 注释里给出退档建议


if __name__ == "__main__":
    unittest.main()
