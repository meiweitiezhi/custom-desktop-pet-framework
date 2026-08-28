"""剪纸动画生成器（cutout_anim）单元测试。

全程无 GUI、无网络；底图一律用程序合成的透明小圆脸替身，
绝不触碰任何真实表情包图片。锁定的行为：
- easing 端点与单调性；
- compose 同 seed 同字节、帧数公式、首尾帧贴合底图、透明背景、高度上限；
- RECIPES 八套配方的完整性与可合成性；
- 符号绘制与生气红晕等特效的实际产出像素。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from petfw.cutout_anim import (  # noqa: E402
    KNOWN_OPS,
    RECIPES,
    Recipe,
    apply_blink,
    beats_to_frames,
    compose,
    draw_symbol,
    draw_vignette,
    draw_steam_puffs,
    ease_in_out,
    ease_out_back,
    linear,
)


def _dummy_base(w=145, h=152):
    """程序合成一张透明底圆脸替身，替代真实素材参与测试。"""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((w // 4, h // 5, 3 * w // 4, 4 * h // 5),
              fill=(250, 210, 90, 255))
    d.ellipse((int(w * 0.38), int(h * 0.42), int(w * 0.46), int(h * 0.52)),
              fill=(40, 40, 40, 255))
    d.ellipse((int(w * 0.56), int(h * 0.42), int(w * 0.64), int(h * 0.52)),
              fill=(40, 40, 40, 255))
    return im


def _transparent_ratio(frame):
    hist = frame.getchannel("A").histogram()
    total = sum(hist)
    return hist[0] / total


class TestEasing(unittest.TestCase):
    """三个缓动函数的端点值与单调性契约。"""

    def test_linear(self):
        ys = [linear(i / 20) for i in range(21)]
        self.assertEqual(linear(0), 0)
        self.assertEqual(linear(1), 1)
        for a, b in zip(ys, ys[1:]):
            self.assertGreater(b, a)

    def test_ease_in_out(self):
        self.assertEqual(ease_in_out(0), 0)
        self.assertEqual(ease_in_out(1), 1)
        ys = [ease_in_out(i / 20) for i in range(21)]
        for a, b in zip(ys, ys[1:]):
            self.assertGreaterEqual(b, a)
        # 前后半程对称：f(0.3) == 1 - f(0.7)
        self.assertAlmostEqual(ease_in_out(0.3), 1 - ease_in_out(0.7))

    def test_ease_out_back(self):
        self.assertEqual(ease_out_back(0), 0)
        self.assertEqual(ease_out_back(1), 1)
        ys = [ease_out_back(i / 20) for i in range(21)]
        # 回弹特征：中段必须越过目标值 1 再回落
        self.assertGreater(max(ys), 1.0)
        self.assertEqual(max(ys), ys[-2] if ys[-2] == max(ys) else max(ys))
        for y in ys:
            self.assertGreater(y, -0.6)  # 反向不做过深下探


class TestComposeDeterminism(unittest.TestCase):
    def test_same_seed_identical_bytes(self):
        base = _dummy_base()
        f1 = compose(RECIPES["laugh"], base, seed=42)
        f2 = compose(RECIPES["laugh"], base, seed=42)
        self.assertEqual(len(f1), len(f2))
        for a, b in zip(f1, f2):
            self.assertEqual(a.tobytes(), b.tobytes())

    def test_different_seed_differs(self):
        base = _dummy_base()
        f1 = compose(RECIPES["laugh"], base, seed=42)
        f2 = compose(RECIPES["laugh"], base, seed=777)
        diff = any(a.tobytes() != b.tobytes() for a, b in zip(f1, f2))
        self.assertTrue(diff, "不同 seed 的粒子演出必须有可见差异")


class TestFrameCount(unittest.TestCase):
    """帧数公式：帧数 = 各 op beats 之和 × (30 / fps)，逐 op 展开后求和。"""

    def test_laugh_formula(self):
        # laugh: hold3 + shake3 + lean_back4 + bounce2 = 12 拍，fps15 → ×2
        self.assertEqual(len(compose(RECIPES["laugh"], _dummy_base())), 24)

    def test_beats_to_frames_scaling(self):
        self.assertEqual(beats_to_frames(3, 15), 6)
        self.assertEqual(beats_to_frames(5, 15), 10)
        self.assertEqual(beats_to_frames(3, 30), 3)
        self.assertEqual(beats_to_frames(1, 15), 2)
        # 最少保住 1 帧，非法 fps 兜底默认节奏
        self.assertEqual(beats_to_frames(1, 300), 1)
        self.assertEqual(beats_to_frames(0, 15), 0)
        self.assertEqual(beats_to_frames(2, 0), beats_to_frames(2, 15))


class TestRecipes(unittest.TestCase):
    EXPECTED_KEYS = {"laugh", "cry", "shock", "eat",
                     "sleep", "idle", "cheer", "angry"}

    def test_registry_complete_and_valid(self):
        self.assertEqual(set(RECIPES.keys()), self.EXPECTED_KEYS)
        for name, r in RECIPES.items():
            self.assertIsInstance(r, Recipe, name)
            self.assertTrue(5 <= r.fps <= 60, name)
            self.assertGreaterEqual(r.cycles, 1, name)
            self.assertTrue(r.steps, name)
            for op in r.steps:
                self.assertIn(op["op"], KNOWN_OPS, name)
                self.assertGreaterEqual(int(op.get("beats", 0)), 0, name)
                if op["op"] == "squash":
                    self.assertAlmostEqual(op["ratios"][0], 1.0, places=6, msg=name)
                    self.assertAlmostEqual(op["ratios"][-1], 1.0, places=6, msg=name)

    def test_all_recipes_compose_ok(self):
        base = _dummy_base()
        for name, r in RECIPES.items():
            frames = compose(r, base, seed=42)
            self.assertGreaterEqual(len(frames), 10, f"{name} 帧数不足")
            for f in frames[:2] + frames[-2:]:
                self.assertEqual(f.mode, "RGBA", name)

    def test_angry_has_red_pixels(self):
        frames = compose(RECIPES["angry"], _dummy_base(), seed=42)
        found = 0
        for f in frames[len(frames) // 3:]:
            px = f.load()
            w, h = f.size
            hits = 0
            for y in range(0, h, 3):
                for x in range(0, w, 3):
                    r, g, b, a = px[x, y]
                    if a > 200 and r >= 150 and g <= 90 and b <= 90:
                        hits += 1
            found = max(found, hits)
        self.assertGreaterEqual(found, 20, "生气红晕应出现成片红色像素")


class TestComposeContract(unittest.TestCase):
    def test_first_last_frames_match_base_pose(self):
        base = _dummy_base()
        still = compose(Recipe(state="t", steps=[{"op": "hold", "beats": 1}]), base)[0]
        moving = compose(RECIPES["laugh"], base, seed=42)
        self.assertEqual(moving[0].size, still.size)
        self.assertEqual(moving[0].tobytes(), still.tobytes())
        self.assertEqual(moving[-1].tobytes(), still.tobytes())

    def test_transparency_size_and_layout(self):
        base = _dummy_base(100, 400)  # 高个子替身逼出缩放下限
        frames = compose(RECIPES["shock"], base, seed=42)
        for f in frames:
            self.assertEqual(f.mode, "RGBA")
            self.assertLessEqual(f.size[1], 256)
        top_left = frames[0].getpixel((0, 0))
        self.assertEqual(top_left[3], 0, "角落必须全透明")
        self.assertGreater(_transparent_ratio(frames[0]), 0.10)


class TestSymbols(unittest.TestCase):
    """五种符号 + 蒸气对 + 眨眼带：画得出且有非空像素。"""

    def test_five_symbols_non_empty(self):
        for kind in ("star", "heart", "tear", "steam", "spark"):
            img = draw_symbol(kind, 18)
            self.assertEqual(img.mode, "RGBA", kind)
            self.assertTrue(img.getbbox(), f"{kind} 不应是空图")

    def test_steam_puffs_and_vignette_non_empty(self):
        puffs = draw_steam_puffs(200, 180, t=2)
        self.assertTrue(puffs.getbbox())
        vig = draw_vignette(200, 180)
        self.assertTrue(vig.getbbox())
        px = vig.load()
        red_hits = sum(
            1 for y in range(0, 180, 2) for x in range(0, 200, 2)
            if (lambda c: c[3] > 40 and c[0] >= 140 and c[1] <= 90)(px[x, y])
        )
        self.assertGreater(red_hits, 10, "红晕渐变应有可见红色像素")

    def test_blink_band_changes_face(self):
        base = _dummy_base()
        blinked = apply_blink(base.copy())
        self.assertNotEqual(base.tobytes(), blinked.tobytes())


if __name__ == "__main__":
    unittest.main()
