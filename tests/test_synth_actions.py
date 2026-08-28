"""外星吸入动作的纯逻辑烘焙测试：build_suck_frames 无 Qt、确定性、可注入底图。

用纯品红圆当「角色」：角色像素与黄色光束像素色域天然分离，质心/缺席断言
不依赖任何真实素材（也绝不读仓库图片）。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from petfw.synth_actions import build_suck_frames  # noqa: E402

SIZE = (160, 160)
CHAR = (255, 0, 255, 255)        # 纯品红：测试里的「角色」色
BEAM_R, BEAM_G, BEAM_B = 255, 230, 80   # 光束黄（与实现约定一致）


def _base_image() -> Image.Image:
    """居中实心圆底图：半径 40 的品红圆，RGBA。"""
    im = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx, cy, r = SIZE[0] // 2, SIZE[1] // 2, 40
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=CHAR)
    return im


def _pixels(im: Image.Image):
    return list(im.getdata())


def _char_pixels(im: Image.Image):
    """角色像素（品红系、alpha 可见）。"""
    return [(r, g, b, a) for (r, g, b, a) in _pixels(im)
            if a > 8 and r > 200 and b > 200 and g < 120]


def _beam_pixels(im: Image.Image):
    """光束像素：黄色系且可见。"""
    return [(r, g, b, a) for (r, g, b, a) in _pixels(im)
            if a > 8 and r > 230 and g > 190 and b < 150]


def _char_centroid_y(im: Image.Image):
    """角色像素的质心 y（行号均值）；没有角色像素返回 None。"""
    w, _h = im.size
    n = 0
    total = 0.0
    for i, (r, g, b, a) in enumerate(_pixels(im)):
        if a > 8 and r > 200 and b > 200 and g < 120:
            total += i // w
            n += 1
    return total / n if n else None


class TestBuildSuckFrames(unittest.TestCase):
    def setUp(self):
        self.frames = build_suck_frames(_base_image())

    def test_frame_count_is_frames_plus_hover(self):
        self.assertEqual(len(self.frames), 26 + 13)
        # 参数可调：自定义帧数同样成立
        self.assertEqual(len(build_suck_frames(_base_image(), 5, 2)), 7)

    def test_all_rgba_same_size(self):
        for im in self.frames:
            self.assertEqual(im.mode, "RGBA")
            self.assertEqual(im.size, SIZE)

    def test_char_centroid_y_monotonic_non_increasing(self):
        ys = []
        for im in self.frames[:26]:
            cy = _char_centroid_y(im)
            self.assertIsNotNone(cy, "前段每帧都必须有角色")
            ys.append(cy)
        for prev, cur in zip(ys, ys[1:]):
            self.assertLessEqual(cur, prev + 1e-6, "角色质心必须单调不增")

    def test_rise_starts_mid_and_ends_near_top(self):
        first = _char_centroid_y(self.frames[0])
        last = _char_centroid_y(self.frames[25])
        self.assertGreater(first, SIZE[1] * 0.35, "起点应在画面中下部")
        self.assertLess(last, SIZE[1] * 0.25, "终点应到画面顶部 10% 附近")

    def test_char_shrinks_and_fades(self):
        first_area = len(_char_pixels(self.frames[0]))
        last_area = len(_char_pixels(self.frames[25]))
        self.assertLess(last_area, first_area * 0.5, "角色要明显缩小")
        last_alphas = [a for (_r, _g, _b, a) in _char_pixels(self.frames[25])]
        self.assertTrue(last_alphas)
        self.assertLessEqual(max(last_alphas), 80, "末端 alpha 应压到 70 附近")

    def test_hover_segment_has_no_char(self):
        for im in self.frames[26:]:
            self.assertEqual(_char_pixels(im), [], "空场悬停段不许再有角色")

    def test_beam_yellow_present_every_frame(self):
        for i, im in enumerate(self.frames):
            self.assertTrue(_beam_pixels(im), f"第 {i} 帧没有光束黄")

    def test_beam_alpha_flickers(self):
        counts = [len(_beam_pixels(im)) for im in self.frames]
        self.assertGreater(max(counts) - min(counts), 0, "光束必须逐帧闪烁")

    def test_deterministic_with_seed(self):
        again = build_suck_frames(_base_image())
        for a, b in zip(self.frames, again):
            self.assertEqual(a.tobytes(), b.tobytes(), "同种子必须逐字节一致")


if __name__ == "__main__":
    unittest.main()
