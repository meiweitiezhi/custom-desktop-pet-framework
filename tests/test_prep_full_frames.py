"""全帧动作管线（prep_assets 全帧模式）测试：目录帧核心 + MP4 薄封装。

全程无 GUI、无网络：帧用 Pillow 程序化生成的小 PNG 序列；MP4 解码层
用 stub 替身验证（本机是否装 imageio_ffmpeg 都不影响结论）。
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

import prep_assets  # noqa: E402


def _moving_frame(k: int, size: int = 64) -> Image.Image:
    """白底小图，大椭圆大幅平移——相邻帧灰度均值差远超去重阈值 2.0。"""
    im = Image.new("RGB", (size, size), (250, 250, 250))
    d = ImageDraw.Draw(im)
    x = 3 + ((k * 7) % 34)
    y = 5 + ((k * 11) % 22)
    d.ellipse([x, y, x + 24, y + 24], fill=(190, 50, 35))
    return im


def _write_seq(directory: pathlib.Path, images, prefix="f"):
    for i, im in enumerate(images):
        im.save(directory / f"{prefix}{i:03d}.png")


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestBuildAction(_TmpCase):
    def test_identical_adjacent_frames_are_deduped(self):
        # 25 张各不相同的帧 + 紧挨着塞一张与前一张完全相同的 -> 必须被跳过
        frames_dir = self.td / "frames"
        frames_dir.mkdir()
        imgs = [_moving_frame(i) for i in range(25)]
        _write_seq(frames_dir, imgs)
        imgs[24].save(frames_dir / "f999.png")   # 与最后一张逐像素相同
        md = prep_assets.build_action(frames_dir)
        self.assertEqual(md["count"], 25, "重复帧必须去重")
        self.assertEqual(len(md["frames"]), 25)

    def test_full_frames_output_names_canvas_and_matte(self):
        frames_dir = self.td / "frames"
        frames_dir.mkdir()
        _write_seq(frames_dir, [_moving_frame(i * 3) for i in range(30)])
        out_dir = self.td / "states"
        md = prep_assets.build_action(frames_dir, out_dir=out_dir,
                                      state="demo", fps_est=24.0)
        names = [p.name for p in sorted(out_dir.glob("*.png"))]
        self.assertEqual(names,
                         [f"demo_F{i:03d}.png" for i in range(md["count"])])
        self.assertEqual(md["fps_est"], 24.0)
        sizes = set()
        for p in out_dir.glob("*.png"):
            with Image.open(p) as im:
                sizes.add(im.size)
                rgba = im.convert("RGBA")
                self.assertEqual(rgba.getpixel((0, 0))[3], 0,
                                 "统一画布后四角必须是抠掉的透明区")
        self.assertEqual(len(sizes), 1, "全部帧必须裁到同一联合包围盒")

    def test_empty_and_garbage_sources_are_safe(self):
        empty = self.td / "empty"
        empty.mkdir()
        md = prep_assets.build_action(empty)
        self.assertEqual((md["count"], md["frames"], md["fps_est"]),
                         (0, [], 0.0))


class TestVideoDecodeLayer(_TmpCase):
    def test_extract_full_frames_with_stubbed_decoder(self):
        imgs = [_moving_frame(i * 2, size=64) for i in range(40)]
        saved = prep_assets._read_video_frames
        prep_assets._read_video_frames = lambda p: (
            imgs, {"fps": 50.0, "size": (64, 64)})
        try:
            out_dir = self.td / "states"
            md = prep_assets.extract_full_frames("fake.mp4", out_dir, "dance",
                                                 fps_cap=25)
            self.assertGreater(md["count"], 0)
            self.assertEqual(md["fps_est"], 25.0, "超出 fps_cap 要压到上限")
            self.assertEqual(
                [p.name for p in sorted(out_dir.glob("*.png"))][:1],
                ["dance_F000.png"])
            # 解码失败路径：拿不到任何帧时返回空元数据而不是抛错
            prep_assets._read_video_frames = lambda p: ([], None)
            md_empty = prep_assets.extract_full_frames("broken.mp4", out_dir,
                                                       "dance")
            self.assertEqual(md_empty["count"], 0)
            self.assertEqual(md_empty["frames"], [])
        finally:
            prep_assets._read_video_frames = saved

    def test_missing_decoder_returns_no_frames(self):
        import builtins
        real_import = builtins.__import__

        def _no_ffmpeg(name, *a, **k):
            if name == "imageio_ffmpeg":
                raise ImportError("没有解码器")
            return real_import(name, *a, **k)

        builtins.__import__ = _no_ffmpeg
        try:
            frames, meta = prep_assets._read_video_frames("whatever.mp4")
        finally:
            builtins.__import__ = real_import
        self.assertEqual(frames, [])
        self.assertIsNone(meta)


if __name__ == "__main__":
    unittest.main()
