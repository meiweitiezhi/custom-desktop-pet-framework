"""dance 视频重建档的动态契约：manifest 真值驱动，不再锁死帧数。

dance 升级为可灵图生视频重建的全帧档后，帧数随源视频浮动（解码去重后
不可预知），断言一律读 manifest 真值：数量下限、节拍合理、引用与磁盘
一致、转场帧与保险丝自洽；并校验视频重建源头标记 source_frames
（i2v 首帧锚点 cheer_ref）。全程无 GUI 无网络。
"""
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
MANIFEST = ASSETS / "manifest.json"
STATES_DIR = ASSETS / "states"


class TestDanceVideoFrames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(
            MANIFEST.read_text(encoding="utf-8"))["states"]["dance"]

    def test_frame_count_is_dynamic_but_abundant(self):
        n = len(self.spec["frames"])
        self.assertGreater(n, 24, "dance 全帧档必须多于 24 帧（真值驱动）")
        ms = float(self.spec["frame_ms"])
        self.assertGreaterEqual(ms, 16, "节拍不得快过 60fps 载波")
        self.assertLessEqual(ms, 60, "节拍不得慢过旧 6 帧档")
        loop_s = n * ms / 1000.0
        self.assertGreaterEqual(loop_s, 2.0, "循环时长至少 2 秒")
        self.assertLessEqual(loop_s, 20.0,
                             "循环时长不得超过 20 秒（15s 源片去重后留余量）")

    def test_frames_reference_real_files_with_uniform_prefix(self):
        frames = self.spec["frames"]
        self.assertTrue(
            all(str(f).startswith("states/dance_F")
                and str(f).endswith(".png") for f in frames),
            "全帧必须是连续 dance_F 序列（源视频全帧档大写 F 命名）")
        self.assertEqual(self.spec["file"], frames[0], "file 必须指向首帧")
        for rel in frames:
            p = STATES_DIR / pathlib.Path(str(rel)).name
            self.assertTrue(p.exists(), f"缺帧图: {rel}")

    def test_source_frames_marker_references_real_files(self):
        """dance.source_frames 源头标记必须存在且引用一致（assets 相对路径）。"""
        src = self.spec.get("source_frames")
        self.assertTrue(src, "视频重建档必须写 source_frames 源头标记")
        for rel in src:
            p = ASSETS / str(rel).replace("\\", "/")
            self.assertTrue(p.exists(), f"source_frames 引用缺图: {rel}")

    def test_transition_and_fuse_self_consistent(self):
        ms = float(self.spec["frame_ms"])
        trans = self.spec["transition_frames"]
        self.assertTrue(trans, "必须有压扁回弹转场帧")
        self.assertTrue(all("_Q" in pathlib.Path(str(t)).stem for t in trans),
                        "转场帧必须是 _Q 序列")
        self.assertEqual(len(trans), round(1000.0 / ms),
                         "转场帧数必须随节拍折算 round(1000/frame_ms)")
        for rel in trans:
            p = STATES_DIR / pathlib.Path(str(rel)).name
            self.assertTrue(p.exists(), f"缺转场帧: {rel}")
        total = (len(self.spec["frames"]) * ms
                 + float(self.spec.get("hold_seconds") or 0) * 1000
                 + len(trans) * ms + 1000)
        self.assertAlmostEqual(float(self.spec["max_seconds"]),
                               round(total / 1000.0, 3), places=9,
                               msg="max_seconds 必须 = 表演+定格+转场+1s 宽限")


if __name__ == "__main__":
    unittest.main()
