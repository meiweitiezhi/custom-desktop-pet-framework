"""song_flow 纯逻辑测试：播歌忽略条款 + 伴舞循环规格。

全程无 GUI、无 Qt：判定全部是纯函数，输入乱码不许抛错。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw.song_flow import dance_loop_spec, should_ignore_click  # noqa: E402


class TestShouldIgnoreClick(unittest.TestCase):
    """歌播着的时候，单击/双击一律忽略。"""

    def test_playing_blocks_every_click(self):
        self.assertTrue(should_ignore_click(True))

    def test_silence_lets_clicks_through(self):
        self.assertFalse(should_ignore_click(False))

    def test_truthy_garbage_counts_as_playing(self):
        self.assertTrue(should_ignore_click(1))
        self.assertFalse(should_ignore_click(None))
        self.assertFalse(should_ignore_click(0))


class TestDanceLoopSpec(unittest.TestCase):
    """伴舞循环规格：play=loop、frames 剔除转场同名尾、带 loop_seconds。"""

    def test_returns_loop_spec_with_frames_and_seconds(self):
        entry = {"frames": ["states/dance_F000.png", "states/dance_F001.png"],
                 "transition_frames": ["states/dance_Q000.png"]}
        spec = dance_loop_spec(entry, loop_seconds=53.0, frame_ms=41)
        self.assertEqual(spec["play"], "loop")
        self.assertEqual(spec["frames"],
                         ["states/dance_F000.png", "states/dance_F001.png"])
        self.assertEqual(spec["loop_seconds"], 53.0)
        self.assertEqual(spec["frame_ms"], 41)

    def test_frames_matching_transition_names_are_stripped(self):
        """转场同名文件（含反斜杠/大小写差异）必须从循环帧里剔除。"""
        entry = {
            "frames": ["a.png", "states/dance_Q000.png", "states/dance_Q001.png"],
            "transition_frames": ["other/dance_Q000.png", "x\\DANCE_q001.PNG"],
        }
        spec = dance_loop_spec(entry, 10.0, 50)
        self.assertEqual(spec["frames"], ["a.png"], "转场同名帧不许进循环")

    def test_entry_without_transition_keeps_all_frames(self):
        spec = dance_loop_spec({"frames": ["a.png", "b.png"]}, 30.0, 33)
        self.assertEqual(spec["frames"], ["a.png", "b.png"])

    def test_garbage_inputs_never_throw(self):
        spec = dance_loop_spec(None, "x", None)
        self.assertEqual(spec["frames"], [])
        self.assertEqual(spec["play"], "loop")
        self.assertEqual(spec["loop_seconds"], 0.0)
        self.assertEqual(spec["frame_ms"], 0)
        spec = dance_loop_spec({"frames": ["a.png"]}, -5, "bad")
        self.assertEqual(spec["frames"], ["a.png"])
        self.assertEqual(spec["loop_seconds"], 0.0)
        self.assertEqual(spec["frame_ms"], 0)


if __name__ == "__main__":
    unittest.main()
