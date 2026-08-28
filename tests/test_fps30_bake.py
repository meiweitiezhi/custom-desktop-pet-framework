"""30fps 密度加密烘焙（v2）+ 渲染节拍可配置测试。

铁律：循环时长严格不变（偏差 <=5%），只把姿态密度加密到 30fps 载波；
重烘焙永远从原始 _f 姿态源出发，绝不拿插帧帧再插帧（防鬼影叠影）。
全程无 GUI、无网络：帧用 Pillow 程序化生成，manifest 用临时目录伪条目。
"""
import configparser
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops, ImageDraw  # noqa: E402

import prep_assets  # noqa: E402
from petfw.config import TEMPLATE  # noqa: E402
from petfw.host import resolve_tick_ms  # noqa: E402


def _blob(shift, size=64, color=(190, 50, 35, 255)):
    """程序化小帧：白底 + 平移的椭圆，相邻帧肉眼可辨。"""
    im = Image.new("RGBA", (size, size), (250, 250, 250, 255))
    d = ImageDraw.Draw(im)
    d.ellipse([8 + shift, 12, 40 + shift, 44], fill=color)
    return im


def _solid(rgba, size=32):
    return Image.new("RGBA", (size, size), rgba)


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


def _adjacent_mean(seq):
    """相邻帧灰度差均值：数值越小 = 相邻过渡越细密。"""
    if len(seq) < 2:
        return 0.0
    return sum(_mean_diff(a, b) for a, b in zip(seq, seq[1:])) / (len(seq) - 1)


class TestV2Transitions(unittest.TestCase):
    """k 公式：预算 = 旧循环 x 30fps，扣除源帧与 2 帧收招后按段均摊。"""

    def test_fast_states_k4(self):
        # 960ms -> 28.8 帧，扣 6 源帧与 2 收招帧 -> round(20.8/5)=4
        self.assertEqual(prep_assets.v2_transitions(960, 6), 4)

    def test_slow_states_k16(self):
        # 2940ms -> 88.2 帧 -> round(80.2/5)=16（eat/sleep 慢速语义自动保留）
        self.assertEqual(prep_assets.v2_transitions(2940, 6), 16)

    def test_floor_is_two_and_garbage_safe(self):
        self.assertEqual(prep_assets.v2_transitions(0, 6), 2)
        self.assertEqual(prep_assets.v2_transitions(None, 6), 2)
        self.assertEqual(prep_assets.v2_transitions("垃圾", 6), 2)


class TestBakeSmoothV2(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)
        self.states = self.td / "states"
        self.states.mkdir()
        self.sources = [f"states/laugh_f{i}.png" for i in range(6)]
        _write_frames(self.states, [_blob(i * 3) for i in range(6)], "laugh")
        self.idle_path = self.states / "idle.png"
        _solid((120, 200, 90, 255), size=40).save(self.idle_path)
        self.entry = {"file": "states/laugh.png"}

    def tearDown(self):
        self._tmp.cleanup()

    def _bake(self, old_loop_ms=960, tail_to=True):
        return prep_assets.bake_smooth_v2(
            self.entry, self.sources, self.states, old_loop_ms,
            tail_to=self.idle_path if tail_to else None)

    def test_frame_count_formula_6_plus_5k_plus_tail(self):
        k = prep_assets.v2_transitions(960, 6)
        self.assertEqual(k, 4)
        frag = self._bake()
        # 6 源帧 + 5 段 x k 渐变 + 2 收招余韵
        self.assertEqual(len(frag["frames"]), 6 + 5 * k + 2)
        self.assertEqual(len(frag["frames"]), 28)
        self.assertEqual(len(list(self.states.glob("laugh_D*.png"))), 28)

    def test_names_use_D_tag_sequential(self):
        frag = self._bake()
        self.assertEqual(frag["frames"],
                         [f"states/laugh_D{i:03d}.png" for i in range(28)])

    def test_fragment_fields(self):
        frag = self._bake()
        self.assertEqual(frag["frame_ms"], round(1000 / prep_assets.TARGET_FPS))
        self.assertEqual(frag["frame_ms"], 33)
        self.assertIs(frag["pingpong"], True)
        self.assertEqual(frag["play"], "once")
        self.assertEqual(frag["return_to"], "idle")
        self.assertEqual(frag["source_frames"], self.sources)
        self.assertEqual(frag["source_loop_ms"], 960)

    def test_loop_duration_within_5_percent(self):
        for old in (960, 2940):
            frag = self._bake(old_loop_ms=old)
            new_loop = frag["frame_ms"] * len(frag["frames"])
            dev = abs(new_loop - old) / old
            self.assertLessEqual(dev, 0.05,
                                 f"old={old} new={new_loop} 偏差 {dev:.2%}")

    def test_tail_lands_on_idle(self):
        frag = self._bake()
        baked = prep_assets.load_state_frames(self.states, frag)
        idle = Image.open(self.idle_path).convert("RGBA")
        last = baked[-1]
        self.assertEqual(last.size, _blob(0).size)
        self.assertIsNone(ImageChops.difference(
            last, idle.resize(last.size)).getbbox(),
            "最后一张必须是 idle 原图（收招定格）")
        self.assertGreater(_mean_diff(baked[-2], baked[-1]), 0.0)
        self.assertGreater(_mean_diff(baked[-2], baked[-3]), 0.0)

    def test_idempotent_rerun_identical_bytes(self):
        frag1 = self._bake()
        bytes1 = [(self.states / pathlib.Path(r).name).read_bytes()
                  for r in frag1["frames"]]
        frag2 = self._bake()
        bytes2 = [(self.states / pathlib.Path(r).name).read_bytes()
                  for r in frag2["frames"]]
        self.assertEqual(frag1["frames"], frag2["frames"])
        self.assertEqual(frag1["source_frames"], frag2["source_frames"])
        self.assertEqual(bytes1, bytes2, "重跑产物必须字节级一致")

    def test_density_finer_than_v1(self):
        """v2 相邻帧灰度差均值必须小于旧 v1 插帧版（密度确实更细）。"""
        v1_src = prep_assets.load_state_frames(
            self.states, {"frames": self.sources})
        old_seq = [v1_src[0]]
        for a, b in zip(v1_src, v1_src[1:]):
            old_seq.extend(prep_assets.interpolate(a, b, 2))   # 旧 blends=2
            old_seq.append(b)
        idle = _blob(21, color=(120, 200, 90, 255))   # 贴近末帧的余韵目标
        idle.save(self.states / "idle2.png")
        frag = prep_assets.bake_smooth_v2(
            self.entry, self.sources, self.states, 960,
            tail_to=self.states / "idle2.png")
        new_seq = prep_assets.load_state_frames(self.states, frag)
        old_mean, new_mean = _adjacent_mean(old_seq), _adjacent_mean(new_seq)
        self.assertGreater(old_mean, 0.0)
        self.assertLess(new_mean, old_mean,
                        f"v2 密度必须更细: old={old_mean:.3f} new={new_mean:.3f}")


class TestBakeAllSmoothV2(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)
        self.states = self.td / "states"
        self.states.mkdir()
        for stem in ("laugh", "eat", "sleep", "dance"):
            _write_frames(self.states, [_blob(i * 3) for i in range(6)],
                          stem)
        _solid((120, 200, 90, 255), size=40).save(self.states / "idle.png")
        # 拟真现状：九态已被旧 v1 烘成 _S 插帧帧（16 帧@60ms / 21 帧@140ms），
        # 磁盘另有原始 6 姿态 _f 源；dance 是 61 帧全帧档
        manifest = {"pet": "my-pet", "states": {
            "idle": {"file": "states/idle.png"},
            "cheer": {"file": "states/cheer.png"},
            "dance": {"file": "states/dance_F000.png",
                      "frames": [f"states/dance_F{i:03d}.png"
                                 for i in range(61)],
                      "frame_ms": 41, "play": "once", "return_to": "idle"},
            "laugh": {"file": "states/laugh.png",
                      "frames": [f"states/laugh_S{i:03d}.png"
                                 for i in range(16)],
                      "frame_ms": 60, "play": "once",
                      "return_to": "idle", "pingpong": True},
            "eat": {"file": "states/eat.png",
                    "frames": [f"states/eat_S{i:03d}.png"
                               for i in range(21)],
                    "frame_ms": 140, "play": "once",
                    "return_to": "idle", "pingpong": True},
            "sleep": {"file": "states/sleep.png",
                      "frames": [f"states/sleep_S{i:03d}.png"
                                 for i in range(21)],
                      "frame_ms": 140, "play": "once",
                      "return_to": "idle", "pingpong": True},
        }}
        self.manifest_path = self.td / "manifest.json"
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_bakes_smoothed_states_from_f_sources_skips_dance(self):
        patched = prep_assets.bake_all_smooth_v2(
            self.manifest_path, self.states, self.states / "idle.png")
        self.assertEqual(sorted(patched), ["eat", "laugh", "sleep"],
                         "dance(全帧档) 与单图状态必须跳过")
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        laugh = data["states"]["laugh"]
        self.assertEqual(len(laugh["frames"]), 28)
        self.assertTrue(all("_D" in f for f in laugh["frames"]),
                        "新产物必须是 _D 系列")
        self.assertTrue(all("_f" in f for f in laugh["source_frames"]),
                        "source_frames 必须指向原始 _f 姿态源")
        self.assertNotIn("_S", json.dumps(laugh),
                         "条目里不许再引用旧 _S 插帧帧")
        new_loop = laugh["frame_ms"] * len(laugh["frames"])
        self.assertLessEqual(abs(new_loop - 960) / 960, 0.05)
        for name in ("eat", "sleep"):
            entry = data["states"][name]
            self.assertEqual(len(entry["frames"]), 88, name)
            loop = entry["frame_ms"] * len(entry["frames"])
            self.assertLessEqual(abs(loop - 2940) / 2940, 0.05, name)
        dance = data["states"]["dance"]
        self.assertEqual(len(dance["frames"]), 61, "dance 帧表一字不动")
        self.assertFalse(list(self.states.glob("dance_D*.png")))
        self.assertNotIn("source_frames", dance)

    def test_rerun_is_idempotent(self):
        prep_assets.bake_all_smooth_v2(
            self.manifest_path, self.states, self.states / "idle.png")
        data1 = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        src1 = data1["states"]["laugh"]["source_frames"]
        bytes1 = [(self.states / f"laugh_D{i:03d}.png").read_bytes()
                  for i in range(28)]
        patched = prep_assets.bake_all_smooth_v2(
            self.manifest_path, self.states, self.states / "idle.png")
        data2 = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(data1["states"]["laugh"]["source_frames"], src1)
        self.assertEqual(patched["laugh"]["source_frames"], src1,
                         "第二次重跑 source_frames 不得漂移")
        self.assertTrue(all("_f" in s for s in src1),
                        "重跑仍必须从原始 _f 源出发，绝不二次插帧")
        bytes2 = [(self.states / f"laugh_D{i:03d}.png").read_bytes()
                  for i in range(28)]
        self.assertEqual(bytes1, bytes2, "重跑产物必须字节级一致")


class TestTickMsConfig(unittest.TestCase):
    def test_template_has_tick_ms_in_pet_section(self):
        self.assertIn("tick_ms = 33", TEMPLATE)
        self.assertLess(TEMPLATE.index("[pet]"), TEMPLATE.index("tick_ms"))
        self.assertIn("30fps", TEMPLATE, "缺省值旁要注明 30fps 载波语义")

    def test_default_is_33(self):
        cp = configparser.ConfigParser()
        cp.add_section("pet")           # 有段无键 -> 缺省 33
        self.assertEqual(resolve_tick_ms(cp), 33)
        cp.set("pet", "tick_ms", "66")
        self.assertEqual(resolve_tick_ms(cp), 66)

    def test_extremes_clamped_and_garbage_falls_back(self):
        def cp_with(value):
            cp = configparser.ConfigParser()
            cp.add_section("pet")
            cp.set("pet", "tick_ms", value)
            return cp

        self.assertEqual(resolve_tick_ms(cp_with("5")), 16, "下限钳到 16")
        self.assertEqual(resolve_tick_ms(cp_with("200")), 100, "上限钳到 100")
        self.assertEqual(resolve_tick_ms(cp_with("abc")), 33, "乱码回落缺省")
        self.assertEqual(resolve_tick_ms(configparser.ConfigParser()),
                         33, "连 [pet] 段都没有也安全回落")


if __name__ == "__main__":
    unittest.main()
