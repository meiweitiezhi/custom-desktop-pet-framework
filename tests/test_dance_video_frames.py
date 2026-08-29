"""dance 回滚档的动态契约：旧 61 帧视频重建版（主人拍板弃用可灵 350 帧）。

旧 dance_D 系列当年从跳舞结算视频抽取，帧图已佚失；但源视频还在
（assets/local/source/跳舞结算_30到53秒.mp4）且重建管线确定性——重跑
即复原。断言读 manifest 真值：帧数 61±5（容忍去重差异）、循环时长
≈2.5 秒、节拍 41ms、转场帧 24 张 _Q、保险丝显式自洽；并守护可灵标记
（source_frames）不再回潮。全程无 GUI 无网络。
"""
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
MANIFEST = ASSETS / "manifest.json"
STATES_DIR = ASSETS / "states"


class TestDanceRollback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(
            MANIFEST.read_text(encoding="utf-8"))["states"]["dance"]

    def test_frame_count_61_plus_minus_5(self):
        n = len(self.spec["frames"])
        self.assertGreaterEqual(n, 56, "dance 回滚档帧数不得少于 61-5")
        self.assertLessEqual(n, 66, "dance 回滚档帧数不得多于 61+5")

    def test_loop_duration_about_2_5_seconds(self):
        n = len(self.spec["frames"])
        ms = float(self.spec["frame_ms"])
        loop_s = n * ms / 1000.0
        self.assertGreaterEqual(loop_s, 2.2, "循环时长须 ≈2.5s（下限）")
        self.assertLessEqual(loop_s, 2.8, "循环时长须 ≈2.5s（上限）")

    def test_frame_ms_is_41(self):
        self.assertEqual(int(self.spec["frame_ms"]), 41, "旧档节拍 41ms")

    def test_once_fields_rollback_shape(self):
        self.assertEqual(self.spec["play"], "once")
        self.assertEqual(self.spec["return_to"], "idle")
        self.assertEqual(float(self.spec["hold_seconds"]), 0.0,
                         "dance 不定格（主人拍板现值）")

    def test_frames_reference_real_files_with_uniform_prefix(self):
        frames = self.spec["frames"]
        self.assertTrue(
            all(str(f).startswith("states/dance_F")
                and str(f).endswith(".png") for f in frames),
            "全帧必须是连续 dance_F 序列（源视频重建档大写 F 命名）")
        self.assertEqual(self.spec["file"], frames[0], "file 必须指向首帧")
        self.assertEqual(
            frames,
            [f"states/dance_F{i:03d}.png" for i in range(len(frames))],
            "表演帧必须是本状态的连续编号帧")
        for rel in frames:
            p = STATES_DIR / pathlib.Path(str(rel)).name
            self.assertTrue(p.exists(), f"缺帧图: {rel}")

    def test_transition_and_fuse_self_consistent(self):
        ms = float(self.spec["frame_ms"])
        trans = self.spec["transition_frames"]
        self.assertEqual(len(trans), 24, "转场帧必须恰为 dance_Q 24 张")
        self.assertTrue(all("_Q" in pathlib.Path(str(t)).stem for t in trans),
                        "转场帧必须是 _Q 序列")
        for rel in trans:
            p = STATES_DIR / pathlib.Path(str(rel)).name
            self.assertTrue(p.exists(), f"缺转场帧: {rel}")
        total = (len(self.spec["frames"]) * ms
                 + float(self.spec.get("hold_seconds") or 0) * 1000
                 + len(trans) * ms + 1000)
        self.assertAlmostEqual(float(self.spec["max_seconds"]),
                               round(total / 1000.0, 3), places=9,
                               msg="max_seconds 必须 = 表演+定格+转场+1s 宽限")

    def test_kling_source_frames_marker_removed(self):
        """可灵 i2v 档的 source_frames 源头标记随回滚一并撤下，不得回潮。"""
        self.assertNotIn("source_frames", self.spec,
                         "旧 61 帧档没有 source_frames 标记")


if __name__ == "__main__":
    unittest.main()
