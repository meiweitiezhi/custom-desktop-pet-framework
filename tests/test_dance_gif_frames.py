"""dance GIF 扭舞档：8 帧新舞接入（gif_to_state_frames）+ 专用压扁转场。

主人拍板 2026-08：dance 换用用户提供的 8 帧 GIF（960×960@40ms 白底）——
逐帧独立白底洪泛抠图 -> 全帧联合包围盒统一裁剪（对齐防抖）-> 等比缩放
高 <=400 -> dance_G{idx:02d}.png；转场以 gif 末帧（dance_G07）为起点、
idle.png 为终点重烘 8 张压扁回弹帧 dance_T{idx:03d}.png；manifest 表演
窗口 5 秒 + 定格 0.3 秒 + 保险丝 6.62 显式自洽。全程无 GUI 无网络：
合成 GIF 与帧图全部用 Pillow 程序化生成，真实 manifest 只读真值断言。
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops, ImageDraw  # noqa: E402

import prep_assets  # noqa: E402
from petfw.song_flow import dance_loop_spec  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "assets" / "manifest.json"
STATES_DIR = ROOT / "assets" / "states"


def _dance_pose(k: int, size: int = 96) -> Image.Image:
    """白底合成舞姿：红团子随 k 平移（帧间错位验证联合包围盒对齐）。"""
    im = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(im)
    x = 8 + (k * 9) % 30
    y = 12 + (k * 5) % 18
    d.ellipse([x, y, x + 34, y + 34], fill=(190, 50, 35))
    if k == 7:                       # 末帧戴帽：给转场一个可感知的姿态差
        d.rectangle([x + 8, y - 8, x + 26, y + 2], fill=(190, 50, 35))
    return im


def _make_test_gif(path: pathlib.Path, n: int = 3, duration: int = 40,
                   size: int = 96) -> None:
    imgs = [_dance_pose(k, size) for k in range(n)]
    imgs[0].save(path, save_all=True, append_images=imgs[1:],
                 duration=duration, loop=0)


def _body(color, hat=False, size=160, top=24) -> Image.Image:
    """程序化姿态帧：底部大椭圆（顶留白），hat=True 时头顶加帽。"""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, top, size - 1, size - 1], fill=color)
    if hat:
        d.rectangle([size // 2 - 20, 6, size // 2 + 20, top + 2], fill=color)
    return im


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# ======================================================== gif_to_state_frames
class TestGifToStateFrames(_TmpCase):
    def _run(self, duration=40, n=3, out=None):
        gif = self.td / "demo.gif"
        _make_test_gif(gif, n=n, duration=duration)
        out = out or (self.td / "states")
        return prep_assets.gif_to_state_frames(gif, out, "demo"), out

    def test_outputs_exact_G_series_and_patch_shape(self):
        frag, out = self._run()
        self.assertEqual(sorted(frag), ["frame_ms", "frames"])
        self.assertEqual(frag["frame_ms"], 40, "40ms 中位延迟原样保留")
        self.assertEqual(frag["frames"],
                         [f"states/demo_G{i:02d}.png" for i in range(3)])
        for rel in frag["frames"]:
            self.assertTrue((out / pathlib.Path(rel).name).is_file(),
                            f"缺帧图: {rel}")

    def test_uniform_canvas_height_cap_and_matte(self):
        frag, out = self._run()
        sizes = set()
        for rel in frag["frames"]:
            with Image.open(out / pathlib.Path(rel).name) as im:
                self.assertEqual(im.mode, "RGBA")
                sizes.add(im.size)
                corner = im.getpixel((0, 0))
                self.assertEqual(corner[3], 0,
                                 "白底必须被洪泛抠掉（角落像素透明）")
                self.assertIsNotNone(im.getchannel("A").getbbox(),
                                     "主体必须还在画面上")
        self.assertEqual(len(sizes), 1, "全帧必须统一画布（联合包围盒防抖）")
        self.assertLessEqual(next(iter(sizes))[1], 400,
                             "等比缩放后高度不得超 400")

    def test_all_frames_kept_not_thinned(self):
        # 8 帧 GIF 必须整批保留——dance 扭舞档与旧抽稀 <=6 帧管线的分水岭
        frag, _ = self._run(n=8)
        self.assertEqual(len(frag["frames"]), 8)

    def test_deterministic_rerun_bytes_identical(self):
        frag1, out1 = self._run()
        frag2, out2 = self._run(out=self.td / "states2")
        self.assertEqual(frag1, frag2)
        for rel in frag1["frames"]:
            self.assertEqual((out1 / pathlib.Path(rel).name).read_bytes(),
                             (out2 / pathlib.Path(rel).name).read_bytes(),
                             "重跑必须字节级一致（确定性管线）")

    def test_frame_ms_clamped_into_30_120(self):
        # GIF 延迟以厘秒存储：20ms 可表示且低于下限；250ms 高于上限
        self.assertEqual(self._run(duration=20)[0]["frame_ms"], 30,
                         "过快延迟必须钳到下限 30")
        self.assertEqual(self._run(duration=250)[0]["frame_ms"], 120,
                         "过慢延迟必须钳到上限 120")


# ======================================================== dance 专用压扁转场
class TestDanceGifTransition(_TmpCase):
    """8 张 _T 压扁回弹：gif 末帧 -> idle，5 秒窗口口径的保险丝自洽。"""

    N_G = 8

    def setUp(self):
        super().setUp()
        self.states = self.td / "states"
        self.states.mkdir()
        for k in range(self.N_G):
            _dance_pose(k).save(self.states / f"dance_G{k:02d}.png")
        self.idle = _body((240, 200, 60, 255))
        self.idle.save(self.states / "idle.png")
        self.manifest_path = self.td / "manifest.json"
        self._write_manifest({
            "pet": "my-pet",
            "states": {
                "idle": {"file": "states/idle.png"},
                "dance": {
                    "file": "states/dance_G00.png",
                    "frames": [f"states/dance_G{k:02d}.png"
                               for k in range(self.N_G)],
                    "frame_ms": 40, "perform_seconds": 5.0,
                    "hold_seconds": 0.3, "play": "once",
                    "return_to": "idle",
                },
            },
        })

    def tearDown(self):
        super().tearDown()

    def _write_manifest(self, data):
        self.manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    def _read_dance(self):
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return data["states"]["dance"]

    def _run(self):
        return prep_assets.bake_dance_gif_transition(
            states_dir=self.states, manifest_path=self.manifest_path)

    def test_bakes_exact_8_T_frames_transition_slot_only(self):
        frag = self._run()
        expect = [f"states/dance_T{i:03d}.png" for i in range(8)]
        self.assertEqual(frag["transition_frames"], expect)
        self.assertEqual(self._read_dance()["transition_frames"], expect)
        for rel in expect:
            self.assertTrue((self.states / pathlib.Path(rel).name).is_file(),
                            f"缺转场帧: {rel}")
        entry = self._read_dance()
        self.assertEqual(entry["frames"],
                         [f"states/dance_G{k:02d}.png"
                          for k in range(self.N_G)],
                         "表演帧段保持纯 _G 帧，转场绝不混进 frames")
        self.assertEqual(entry["frame_ms"], 40, "节拍沿用条目原值")

    def test_starts_at_gif_last_frame_ends_at_idle(self):
        self._run()
        idle = Image.open(self.states / "idle.png").convert("RGBA")
        seq = prep_assets.load_state_frames(
            self.states, {"frames": self._read_dance()["transition_frames"]})
        self.assertEqual(len(seq), 8)
        self.assertIsNone(
            ImageChops.difference(
                seq[-1].convert("RGB"),
                idle.resize(seq[-1].size).convert("RGB")).getbbox(),
            "落定帧必须就是 idle 姿态（比 RGB 通道，避开 RGBA 陷阱）")
        g7 = Image.open(self.states / "dance_G07.png").convert("RGBA")
        self.assertIsNone(
            ImageChops.difference(seq[0].convert("RGB"),
                                  g7.convert("RGB")).getbbox(),
            "转场起点必须原样接住 gif 末帧（dance_G07），无缝开压")

    def test_max_seconds_uses_perform_window(self):
        self._run()
        entry = self._read_dance()
        # 5.0 表演窗口 + 0.3 定格 + 8x40ms 转场 + 1.0 宽限 = 6.62
        self.assertAlmostEqual(float(entry["max_seconds"]), 6.62, places=9)
        total = (float(entry["perform_seconds"])
                 + float(entry["hold_seconds"])
                 + len(entry["transition_frames"])
                 * float(entry["frame_ms"]) / 1000.0 + 1.0)
        self.assertAlmostEqual(float(entry["max_seconds"]),
                               round(total, 3), places=9,
                               msg="保险丝必须 = 窗口+定格+转场+1s 宽限")

    def test_idempotent_rerun_bytes_and_purges_legacy(self):
        # 埋两代历史遗留：旧渐变 _T 帧与上一轮 _Q 帧各一张（文件+引用）
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        data["states"]["dance"]["transition_frames"] = [
            "states/dance_T004.png", "states/dance_Q099.png"]
        self._write_manifest(data)
        (self.states / "dance_T004.png").write_bytes(
            (self.states / "dance_G00.png").read_bytes())
        (self.states / "dance_Q099.png").write_bytes(
            (self.states / "dance_G01.png").read_bytes())
        self._run()
        self.assertFalse((self.states / "dance_Q099.png").exists(),
                         "上一轮 _Q 帧文件必须被幂等清理")
        self.assertEqual(list(self.states.glob("dance_Q*.png")), [])
        bytes1 = [(self.states / f"dance_T{i:03d}.png").read_bytes()
                  for i in range(8)]
        text1 = self.manifest_path.read_text(encoding="utf-8")
        self._run()
        bytes2 = [(self.states / f"dance_T{i:03d}.png").read_bytes()
                  for i in range(8)]
        self.assertEqual(bytes1, bytes2, "重跑帧图必须字节级一致（确定性）")
        self.assertEqual(text1,
                         self.manifest_path.read_text(encoding="utf-8"),
                         "重跑 manifest 必须字节级一致（幂等）")


# ======================================================== 真实 manifest 真值
class TestDanceGifManifestTruth(unittest.TestCase):
    """真实仓库契约：dance = 8 帧 GIF 新舞 + 5 秒窗口 + 8 张 _T 谢幕。"""

    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(
            MANIFEST.read_text(encoding="utf-8"))["states"]["dance"]

    def test_frames_are_exact_G_series(self):
        self.assertEqual(
            self.spec["frames"],
            [f"states/dance_G{i:02d}.png" for i in range(8)],
            "表演帧必须是 8 张连续 dance_G 序列（GIF 新舞档）")
        self.assertEqual(self.spec["file"], self.spec["frames"][0],
                         "file 必须指向首帧")
        for rel in self.spec["frames"]:
            p = STATES_DIR / pathlib.Path(str(rel)).name
            self.assertTrue(p.is_file(), f"缺帧图: {rel}")

    def test_gif_frames_uniform_canvas_and_height_cap(self):
        sizes = set()
        for rel in self.spec["frames"]:
            with Image.open(STATES_DIR / pathlib.Path(str(rel)).name) as im:
                self.assertEqual(im.mode, "RGBA")
                sizes.add(im.size)
                self.assertIsNotNone(im.getchannel("A").getbbox())
        self.assertEqual(len(sizes), 1, "8 帧必须统一画布（对齐防抖）")
        self.assertLessEqual(next(iter(sizes))[1], 400)

    def test_rhythm_and_window_fields(self):
        self.assertEqual(int(self.spec["frame_ms"]), 40, "GIF 原生 25fps")
        self.assertEqual(float(self.spec["perform_seconds"]), 5.0,
                         "点播表演窗口 5 秒")
        self.assertEqual(float(self.spec["hold_seconds"]), 0.3,
                         "压扁谢幕前定格 0.3 秒")
        self.assertEqual(self.spec["play"], "once")
        self.assertEqual(self.spec["return_to"], "idle")

    def test_transition_frames_are_exact_T_series(self):
        trans = self.spec["transition_frames"]
        self.assertEqual(trans,
                         [f"states/dance_T{i:03d}.png" for i in range(8)])
        for rel in trans:
            self.assertTrue(
                (STATES_DIR / pathlib.Path(str(rel)).name).is_file(),
                f"缺转场帧: {rel}")
        last = Image.open(
            STATES_DIR / pathlib.Path(str(trans[-1])).name).convert("RGB")
        idle = Image.open(STATES_DIR / "idle.png").convert("RGBA")
        self.assertIsNone(
            ImageChops.difference(last, idle.resize(last.size)
                                  .convert("RGB")).getbbox(),
            "转场末帧必须就是 idle")

    def test_fuse_self_consistent_6_62(self):
        total = (float(self.spec["perform_seconds"])
                 + float(self.spec["hold_seconds"])
                 + len(self.spec["transition_frames"])
                 * float(self.spec["frame_ms"]) / 1000.0 + 1.0)
        self.assertAlmostEqual(float(self.spec["max_seconds"]),
                               round(total, 3), places=9,
                               msg="max_seconds 必须 = 5+0.3+0.32+1.0 显式自洽")
        self.assertAlmostEqual(float(self.spec["max_seconds"]), 6.62, places=9)

    def test_old_kling_and_old_transition_frames_unreferenced(self):
        text = MANIFEST.read_text(encoding="utf-8")
        self.assertNotIn("dance_F", text, "旧可灵/视频档 dance_F 不得再被引用")
        self.assertNotIn("dance_Q", text, "旧 24 张 _Q 转场不得再被引用")

    def test_dance_loop_spec_compatible(self):
        """伴舞循环：frames 剔除转场后恰为 8 张 _G 全量。"""
        spec = dance_loop_spec(self.spec, loop_seconds=53.0,
                               frame_ms=int(self.spec["frame_ms"]))
        self.assertEqual(spec["play"], "loop")
        self.assertEqual(spec["frames"], self.spec["frames"],
                         "转场帧剔除后必须剩全量 8 张表演帧")
        self.assertEqual(len(spec["frames"]), 8)


if __name__ == "__main__":
    unittest.main()
