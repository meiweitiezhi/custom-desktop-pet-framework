"""转场补帧（任务三）回归：extend_return_transition 给 once 状态补收招帧。

铁律：追加计数精确、末帧==idle、幂等重跑字节一致、once 总时长=表演+转场
（30fps 载波下 12 帧 ≈ 0.4 秒）。全程无 GUI：帧用 Pillow 程序化生成，
manifest 用临时目录伪条目，绝不触碰真实素材。
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops, ImageDraw  # noqa: E402

import prep_assets  # noqa: E402


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


class TestExtendReturnTransition(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)
        self.states = self.td / "states"
        self.states.mkdir()
        # shock：6 帧伪条目（末帧故意不同于 idle，逼出真实渐变）
        for i in range(6):
            _blob(i * 4).save(self.states / f"shock_D{i:03d}.png")
        # dance：2 帧全帧档
        for i in range(2):
            _blob(30 + i * 4).save(self.states / f"dance_F{i:03d}.png")
        # cry：末帧直接放 idle 姿态（_D 烘焙自带收招定格的真实形态）
        _solid((120, 200, 90, 255), size=48).save(self.states / "cry_D000.png")
        _solid((120, 200, 90, 255), size=48).save(self.states / "cry_D001.png")
        _solid((120, 200, 90, 255), size=40).save(self.states / "idle.png")
        self.manifest_path = self.td / "manifest.json"
        self._write_manifest({
            "pet": "my-pet",
            "states": {
                "idle": {"file": "states/idle.png"},
                "shock": {"file": "states/shock_D000.png",
                          "frames": [f"states/shock_D{i:03d}.png"
                                     for i in range(6)],
                          "frame_ms": 33, "play": "once",
                          "return_to": "idle"},
                "dance": {"file": "states/dance_F000.png",
                          "frames": [f"states/dance_F{i:03d}.png"
                                     for i in range(2)],
                          "frame_ms": 41, "play": "once",
                          "return_to": "idle"},
                "cry": {"file": "states/cry_D000.png",
                        "frames": ["states/cry_D000.png",
                                   "states/cry_D001.png"],
                        "frame_ms": 33, "play": "once",
                        "return_to": "idle"},
                # 常驻态（loop）没有「播完收招」语义，必须跳过
                "sleep": {"file": "states/sleep.png",
                          "frames": ["states/cry_D000.png"],
                          "frame_ms": 33, "play": "loop"},
            },
            "_disabled_states": {"hide": {"file": "states/hide.png"}},
        })

    def tearDown(self):
        self._tmp.cleanup()

    def _write_manifest(self, data):
        self.manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    def _read_manifest(self):
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _run(self, targets=("shock", "cry", "dance"), frames=12):
        return prep_assets.extend_return_transition(
            self.states, self.manifest_path, targets, frames=frames)

    # ------------------------------------------------------------ 追加计数
    def test_appends_exact_count_with_T_names(self):
        before = {n: (len(s["frames"]), s["frame_ms"])
                  for n, s in self._read_manifest()["states"].items()
                  if s.get("frames")}
        done = self._run()
        self.assertEqual(sorted(done), ["cry", "dance", "shock"])
        data = self._read_manifest()["states"]
        for name in ("shock", "cry", "dance"):
            old_n, old_ms = before[name]
            rels = data[name]["frames"]
            self.assertEqual(len(rels), old_n + 12, f"{name} 必须精确追加 12 帧")
            self.assertEqual(data[name]["frame_ms"], old_ms,
                             f"{name} frame_ms 一律不变")
            tails = rels[old_n:]
            self.assertEqual(
                tails, [f"states/{name}_T{i:03d}.png" for i in range(12)],
                f"{name} 转场帧命名规约 <状态>_T{{idx:03d}}.png")
            for rel in tails:
                self.assertTrue((self.states / pathlib.Path(rel).name).exists())

    def test_loop_duration_grows_by_transition_only(self):
        """once 总时长 = 表演 + 转场：33ms 载波下 12 帧 ≈ +0.4 秒。"""
        data = self._read_manifest()["states"]
        old_shock = len(data["shock"]["frames"]) * data["shock"]["frame_ms"]
        old_dance = len(data["dance"]["frames"]) * data["dance"]["frame_ms"]
        self._run()
        data = self._read_manifest()["states"]
        new_shock = len(data["shock"]["frames"]) * data["shock"]["frame_ms"]
        new_dance = len(data["dance"]["frames"]) * data["dance"]["frame_ms"]
        self.assertEqual(new_shock - old_shock, 12 * 33,
                         "shock：+12 帧 x 33ms ≈ 0.4 秒转场")
        self.assertEqual(new_dance - old_dance, 12 * 41,
                         "dance：frame_ms 沿用条目自身节拍，不硬切 33")
        # 常驻 sleep 完全没被碰
        self.assertEqual(len(data["sleep"]["frames"]), 1)

    # ------------------------------------------------------------ 末帧==idle
    def test_last_frame_is_idle_both_paths(self):
        self._run()
        idle = Image.open(self.states / "idle.png").convert("RGBA")
        for name in ("shock", "cry", "dance"):
            frag = {"frames": self._read_manifest()["states"][name]["frames"]}
            seq = prep_assets.load_state_frames(self.states, frag)
            last = seq[-1]
            self.assertIsNone(
                ImageChops.difference(last, idle.resize(last.size)).getbbox(),
                f"{name} 收招转场末帧必须是 idle 原图")

    def test_blend_path_contains_both_endpoints(self):
        """含首尾：_T000 即序列末帧，中间帧严格介于末帧与 idle 之间。"""
        self._run()
        frag = {"frames": self._read_manifest()["states"]["shock"]["frames"]}
        seq = prep_assets.load_state_frames(self.states, frag)
        src_last = Image.open(self.states / "shock_D005.png").convert("RGBA")
        # 比 RGB 通道（RGBA 的 difference().getbbox() 会被全零 alpha 掩盖）
        self.assertIsNone(ImageChops.difference(
            seq[-12].convert("RGB"), src_last.convert("RGB")).getbbox(),
            "_T000 必须就是原序列末帧（含首端点）")
        self.assertGreater(_mean_diff(seq[-12], seq[-1]), 0.0,
                           "末帧与起点之间必须真有画面变化")
        for mid in seq[-11:-1]:
            self.assertLess(_mean_diff(mid, seq[-1]), _mean_diff(seq[-12], seq[-1]),
                            "中间帧必须比起点更接近 idle（单调渐变）")

    # ---------------------------------------------------------------- 幂等
    def test_idempotent_rerun_same_count_and_bytes(self):
        first = self._run()
        n1 = {k: len(v) for k, v in first.items()}
        data1 = self._read_manifest()
        bytes1 = {name: [(self.states / pathlib.Path(r).name).read_bytes()
                         for r in rels[-12:]]
                  for name, rels in first.items()}
        second = self._run()
        self.assertEqual({k: len(v) for k, v in second.items()}, n1,
                         "重跑不得再次追加：先清旧 _T 帧再生成")
        data2 = self._read_manifest()
        for name in first:
            self.assertEqual(data1["states"][name]["frames"],
                             data2["states"][name]["frames"])
        bytes2 = {name: [(self.states / pathlib.Path(r).name).read_bytes()
                         for r in rels[-12:]]
                  for name, rels in second.items()}
        self.assertEqual(bytes1, bytes2, "重跑产物必须字节级一致")
        # 磁盘上不留垃圾：每个目标恰 12 张 _T 文件
        for name in first:
            files = list(self.states.glob(f"{name}_T*.png"))
            self.assertEqual(len(files), 12, f"{name} 的 _T 文件数量漂移")

    def test_stale_T_references_are_purged(self):
        self._run()
        # 手工污染：往 frames 头部插一条旧 _T 引用 + 磁盘多一张野 _T 文件
        data = self._read_manifest()
        frames = data["states"]["shock"]["frames"]
        frames.insert(0, "states/shock_T004.png")
        (self.states / "shock_T999.png").write_bytes(
            (self.states / "shock_T000.png").read_bytes())
        self._write_manifest(data)
        done = self._run(("shock",))
        # 旧引用清场后重生成：总数恰 18、头部是纯 _D、尾部是纯新 _T
        # （T004 是新帧的合法名字，不能靠「不含这个名字」判断清场）
        self.assertEqual(len(done["shock"]), 6 + 12)
        self.assertEqual(done["shock"][:6],
                         [f"states/shock_D{i:03d}.png" for i in range(6)])
        self.assertEqual(done["shock"][-12:],
                         [f"states/shock_T{i:03d}.png" for i in range(12)])
        self.assertFalse((self.states / "shock_T999.png").exists(),
                         "野 _T 文件必须被幂等清理")

    # -------------------------------------------------------------- 跳过面
    def test_skips_non_once_disabled_and_missing(self):
        done = self._run(targets=("sleep", "hide", "ghost", "shock"))
        self.assertEqual(sorted(done), ["shock"])
        # sleep/hide/ghost 的 frames 一字不动
        data = self._read_manifest()
        self.assertEqual(len(data["states"]["sleep"]["frames"]), 1)
        self.assertNotIn("hide", data["states"], "禁用区条目不得被误烘")
        self.assertFalse(list(self.states.glob("hide_T*.png")))
        self.assertFalse(list(self.states.glob("ghost_T*.png")))

    def test_zone_metadata_survives_write_back(self):
        self._run()
        m = self._read_manifest()
        self.assertEqual(set(m["_disabled_states"]), {"hide"},
                         "manifest 顶层禁用区必须在写回后原样保留")


if __name__ == "__main__":
    unittest.main()
