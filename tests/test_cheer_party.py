"""打气派对循环（cheer party）：bake_cheer_party 生成 45 帧整活连招。

cheer 原为单图静态；升级为常驻搞笑循环（play=loop、正向不走乒乓）。
连招五幕：两快两慢挥旗（±18°+小跳+挥臂弧线残影）→ 蓄力猛压 0.75 弹
过冲 1.15 → 原地粗转一圈 12 帧 → 顶点定格 3 帧+三颗星星爆开 → 落地
回弹收招。全程程序合成（纯 Pillow，就地实现五角星，不 import
cutout_anim），绝不触碰真实素材。
"""
import math
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

import prep_assets  # noqa: E402


def _party_base():
    """程序化 cheer 底图：蓝色团子 + 双手红旗（避开白/黄检测色的干扰）。"""
    im = Image.new("RGBA", (225, 159), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, 224, 158], fill=(96, 156, 220, 255))
    d.rectangle([4, 18, 40, 58], fill=(214, 40, 40, 255))
    d.rectangle([188, 6, 224, 44], fill=(200, 60, 60, 255))
    return im


def _principal_axis_deg(im):
    """不透明像素二阶矩主轴角（度，-90~90）：倾摆角的实测量。"""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    m00 = m10 = m01 = m20 = m02 = m11 = 0.0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            a = px[x, y][3]
            if a < 40:
                continue
            m00 += a
            m10 += a * x
            m01 += a * y
            m20 += a * x * x
            m02 += a * y * y
            m11 += a * x * y
    if m00 <= 0:
        return 0.0
    cx, cy = m10 / m00, m01 / m00
    u20 = m20 / m00 - cx * cx
    u02 = m02 / m00 - cy * cy
    u11 = m11 / m00 - cx * cy
    return math.degrees(0.5 * math.atan2(2 * u11, u20 - u02))


def _count_color(im, region_half=False, star=False):
    """统计目标像素数：star=True 数黄色五角星像素，否则数白色高亮弧线像素。"""
    px = im.convert("RGBA").load()
    w, h = im.size
    n = 0
    for y in range(h if not region_half else h // 2):
        for x in range(0, w, 2):
            r, g, b, a = px[x, y]
            if a < 90:
                continue
            if star:
                if r >= 225 and 170 <= g <= 250 and b <= 140:
                    n += 1
            elif min(r, g, b) >= 238:
                n += 1
    return n * (2 if not region_half else 1)


class TestBakeCheerParty(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)
        self.states = self.td / "states"
        self.states.mkdir()
        self.base = _party_base()
        self.frag = prep_assets.bake_cheer_party(
            self.base, fps_ms=33, out_dir=self.states)

    def tearDown(self):
        self._tmp.cleanup()

    def _frame(self, idx):
        return Image.open(
            self.states / f"cheer_D{idx:03d}.png").convert("RGBA")

    # ------------------------------------------------------------ 帧数与档位
    def test_bakes_45_frames_loop_manifest(self):
        frag = self.frag
        self.assertEqual(len(frag["frames"]), 45, "必须整 45 帧 ≈ 1.5 秒")
        self.assertEqual(frag["frame_ms"], 33)
        self.assertEqual(frag["play"], "loop", "cheer 变常驻搞笑循环")
        self.assertFalse(frag.get("pingpong"), "正向循环，不走乒乓")
        self.assertEqual(frag["frames"],
                         [f"states/cheer_D{i:03d}.png" for i in range(45)])
        for rel in frag["frames"]:
            self.assertTrue((self.states / pathlib.Path(rel).name).exists())

    def test_sequence_deterministic(self):
        again = prep_assets.bake_cheer_party(
            self.base, fps_ms=33, out_dir=self.states)
        self.assertEqual(again["frames"], self.frag["frames"])
        for rel in self.frag["frames"]:
            p = self.states / pathlib.Path(rel).name
            self.assertTrue(len(p.read_bytes()) > 0)
        # 第二遍逐字节比对：重烘焙必须确定性（从源帧纯计算重建）
        first = {rel: (self.states / pathlib.Path(rel).name).read_bytes()
                 for rel in self.frag["frames"]}
        prep_assets.bake_cheer_party(self.base, fps_ms=33, out_dir=self.states)
        for rel, blob in first.items():
            self.assertEqual(
                (self.states / pathlib.Path(rel).name).read_bytes(), blob,
                f"{rel} 重烘焙字节不一致，违背确定性铁律")

    # ------------------------------------------------------------ 连招幅度
    def test_wave_frames_have_strong_tilt_over_15(self):
        angles = [abs(p["angle"]) for p in prep_assets._cheer_poses()]
        self.assertGreaterEqual(max(angles), 18.0, "挥旗幅度必须拉到 ±18°")
        # 产物级：实测帧主轴角，必须有 >15° 的帧
        measured = max(abs(_principal_axis_deg(self._frame(i)))
                       for i in range(18))
        self.assertGreater(measured, 15.0,
                           f"挥旗段实测主轴角 {measured:.1f}° 必须 >15°")

    def test_wave_rhythm_two_fast_two_slow(self):
        """卡点喜感：两快速摆（各 3 帧）+ 两慢速摆（各 6 帧）。"""
        angles = [p["angle"] for p in prep_assets._cheer_poses()[:18]]
        self.assertEqual(angles, [18.0] * 3 + [-18.0] * 3
                         + [18.0] * 6 + [-18.0] * 6)

    def test_charge_squash_and_overshoot_extremes(self):
        poses = prep_assets._cheer_poses()
        sys_ = [p["sy"] for p in poses]
        self.assertAlmostEqual(min(sys_), 0.75, places=2,
                               msg="蓄力必须压到 0.75")
        self.assertGreaterEqual(max(sys_), 1.15, "弹起必须过冲到 1.15")
        # 产物级：过冲帧内容确实比底图高 10% 以上
        base_h = self.base.getchannel("A").getbbox()[3]
        peak = max(self._frame(i).getchannel("A").getbbox()[3]
                   for i in range(len(poses)))
        self.assertGreater(peak / base_h, 1.1, "必须存在缩放 >1.1 的过冲帧")

    def test_star_burst_pixels_present(self):
        poses = prep_assets._cheer_poses()
        star_idx = [i for i, p in enumerate(poses) if p["stars"]]
        self.assertEqual(star_idx, [36, 37, 38], "顶点定格恰 3 帧带星星爆开")
        for i in star_idx:
            self.assertGreater(_count_color(self._frame(i), star=True), 20,
                               f"第 {i} 帧必须画得出黄色五角星像素")
        # 非星星帧不得误染黄色
        self.assertLess(_count_color(self._frame(10), star=True), 5)

    def test_swing_arc_trails_on_wave_frames_only(self):
        """挥臂弧线残影：旗区白色高亮像素只出现在挥旗帧（透明度 100~180）。"""
        poses = prep_assets._cheer_poses()
        alphas = [p["arc_alpha"] for p in poses[:18] if p["arc"]]
        self.assertTrue(all(100 <= a <= 180 for a in alphas),
                        "弧线残影透明度必须落在 100~180")
        wave_max = max(_count_color(self._frame(i), region_half=True)
                       for i in range(18))
        calm_max = max(_count_color(self._frame(i), region_half=True)
                       for i in (19, 21, 25, 40, 44))
        self.assertGreater(wave_max, 60, "挥旗帧必须画得出弧线残影")
        self.assertLess(calm_max, max(6, wave_max // 8),
                        "非挥旗帧的旗区不得出现弧线残影")

    def test_loop_seam_to_first_frame(self):
        """末帧落定回中性姿态，循环回第 1 帧（+18° 挥旗起点）不突兀。"""
        poses = prep_assets._cheer_poses()
        self.assertEqual(len(poses), 45)
        self.assertAlmostEqual(poses[44]["angle"], 0.0)
        self.assertAlmostEqual(poses[44]["sy"], 1.0, places=2)
        self.assertAlmostEqual(poses[0]["angle"], 18.0)


if __name__ == "__main__":
    unittest.main()
