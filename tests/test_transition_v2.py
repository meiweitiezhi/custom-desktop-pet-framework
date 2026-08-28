"""压扁回弹转场（transition-v2）：bake_squash_return 在最大压扁瞬间换装。

替换旧 _T 渐变转场（主人拍板 2026-08）：三幕结构——40% 帧数把表演末姿态
平滑压到 (sy 0.78, sx 1.18)（锚点=底部中心，向地板压），恰在最大压扁帧
无缝换装到 idle 的同比例压扁帧（两图都压到 78% 时轮廓差异最小），60% 帧
ease_out_back 式经 1.12 过冲回弹落定为 idle。全程无 GUI：帧用 Pillow
程序化生成，manifest 用临时目录伪条目，绝不触碰真实素材。
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops, ImageDraw  # noqa: E402

import prep_assets  # noqa: E402

SIZE = 160      # 画布边长
TOP = 24        # 内容顶部留白（给过冲 1.12 留生长空间，防裁顶）


def _body(color, hat=False, size=SIZE):
    """程序化姿态帧：底部大椭圆（顶留白 TOP）；hat=True 时头顶加帽（姿态差）。"""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, TOP, size - 1, size - 1], fill=color)
    if hat:
        d.rectangle([size // 2 - 20, 6, size // 2 + 20, TOP + 2], fill=color)
    return im


def _mean_diff(a, b):
    if a.size != b.size:
        b = b.resize(a.size)
    h = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).histogram()
    total = sum(h)
    if not total:
        return 0.0
    return sum(v * i for i, v in enumerate(h)) / float(total)


def _content_ratio(frame, ref_h):
    """帧不透明内容高度 / 参照内容高度（压扁率/过冲率的实测量）。"""
    box = frame.getchannel("A").getbbox()
    return (box[3] - box[1]) / float(ref_h)


class TestBakeSquashReturn(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._tmp.name)
        self.states = self.td / "states"
        self.states.mkdir()
        # shock：6 帧伪表演（末帧戴帽红团子，故意不同于 idle）
        for i in range(6):
            _body((190, 50, 35, 255), hat=(i == 5)).save(
                self.states / f"shock_D{i:03d}.png")
        # dance：2 帧全帧档 41ms；cry：2 帧 33ms
        for i in range(2):
            _body((60, 120, 200, 255), hat=True).save(
                self.states / f"dance_F{i:03d}.png")
            _body((120, 200, 90, 255), hat=bool(i)).save(
                self.states / f"cry_D{i:03d}.png")
        self.idle = _body((240, 200, 60, 255))          # 黄团子待机
        self.idle.save(self.states / "idle.png")
        # cheer 派对烘焙所需的底图（rebuild 一键入口用）
        _body((96, 156, 220, 255), size=64).save(self.states / "cheer.png")
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
                        "frames": [f"states/cry_D{i:03d}.png"
                                   for i in range(2)],
                        "frame_ms": 33, "play": "once",
                        "return_to": "idle"},
                "sleep": {"file": "states/cry_D000.png",
                          "frames": ["states/cry_D000.png",
                                     "states/cry_D001.png"],
                          "frame_ms": 140, "play": "loop",
                          "pingpong": True},
                "cheer": {"file": "states/cheer.png"},
            },
        })

    def tearDown(self):
        self._tmp.cleanup()

    def _write_manifest(self, data):
        self.manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    def _read_manifest(self):
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _run(self, targets=("shock", "cry", "dance")):
        return prep_assets.bake_all_squash_returns(
            self.states, self.manifest_path, targets=targets)

    def _q_seq(self, name, _n_tail=None):
        # v4：转场帧独立成 transition_frames 字段，整个列表就是 _Q 序列
        frag = {"frames": self._read_manifest()["states"][name]
                ["transition_frames"]}
        return prep_assets.load_state_frames(self.states, frag)

    # ------------------------------------------------------------ 转场独立
    def test_appends_exact_Q_frames_keeps_frame_ms(self):
        done = self._run()
        data = self._read_manifest()["states"]
        self.assertEqual(sorted(done), ["cry", "dance", "shock"])
        # 33ms 载波 -> round(1000/33)=30 帧；41ms -> round(1000/41)=24 帧
        expect = {"shock": (6, 30, 33), "cry": (2, 30, 33), "dance": (2, 24, 41)}
        for name, (n_base, n_q, ms) in expect.items():
            spec = data[name]
            self.assertEqual(len(spec["frames"]), n_base,
                             f"{name} frames 保持纯表演帧，不追加转场")
            self.assertEqual(spec["transition_frames"],
                             [f"states/{name}_Q{i:03d}.png" for i in range(n_q)],
                             f"{name} 转场帧独立成 transition_frames 字段")
            self.assertEqual(spec["frame_ms"], ms,
                             f"{name} frame_ms 沿用条目原值不变")
            self.assertEqual(spec["frames"],
                             [f"states/{name}_{'F' if name == 'dance' else 'D'}"
                              f"{i:03d}.png" for i in range(n_base)],
                             f"{name} 表演帧段原样保留")

    # ------------------------------------------------------------ 压扁曲线
    def test_squash_minimum_in_middle_and_in_band(self):
        self._run()
        seq = self._q_seq("shock", 30)
        ratios = [_content_ratio(f, 154) for f in seq]      # A 侧参照内容高 154
        ratios[12:] = [_content_ratio(f, 136) for f in seq[12:]]  # 换装后 B 侧参照 136
        vmin = min(ratios)
        self.assertGreaterEqual(vmin, 0.75, "最大压扁不得深于 0.75")
        self.assertLessEqual(vmin, 0.81, "最大压扁不得浅于 0.81")
        argmin = ratios.index(vmin)
        self.assertGreaterEqual(argmin, 30 * 0.25, "最小压扁必须出现在中段之前界后")
        self.assertLessEqual(argmin, 30 * 0.60, "最小压扁必须出现在中段之后界前")

    def test_overshoot_frame_above_108(self):
        self._run()
        seq = self._q_seq("shock", 30)
        overshoots = [_content_ratio(f, 136) for f in seq[12:]]
        self.assertTrue(any(r > 1.08 for r in overshoots),
                        "回弹段必须存在 >1.08 的过冲帧（ease_out_back 式）")
        self.assertLessEqual(max(overshoots), 1.16, "过冲不得失控超过 1.16")

    def test_last_frame_is_idle_rgb(self):
        self._run()
        idle = Image.open(self.states / "idle.png").convert("RGBA")
        for name in ("shock", "cry", "dance"):
            last = self._q_seq(name, 24)[-1]
            self.assertIsNone(
                ImageChops.difference(
                    last.convert("RGB"),
                    idle.resize(last.size).convert("RGB")).getbbox(),
                f"{name} 落定帧必须就是 idle 姿态（比 RGB 通道，避开 RGBA 陷阱）")

    # ---------------------------------------------------------------- 幂等
    def test_idempotent_rerun_bytes_and_purges_legacy_T_and_Q(self):
        # 手工埋两代历史遗留：_T 渐变帧与上一轮 _Q 帧各一张（文件+引用），
        # 表演 frames 与转场 transition_frames 两个槽位都要能清干净
        data = self._read_manifest()
        data["states"]["shock"]["frames"] = (
            ["states/shock_T004.png", "states/shock_Q099.png"]
            + data["states"]["shock"]["frames"])
        data["states"]["shock"]["transition_frames"] = (
            ["states/shock_T004.png", "states/shock_Q099.png"])
        self._write_manifest(data)
        (self.states / "shock_T004.png").write_bytes(
            (self.states / "shock_D000.png").read_bytes())
        (self.states / "shock_Q099.png").write_bytes(
            (self.states / "shock_D001.png").read_bytes())
        first = self._run()
        self.assertFalse((self.states / "shock_T004.png").exists(),
                         "旧 _T 帧文件必须被幂等清理")
        self.assertFalse((self.states / "shock_Q099.png").exists(),
                         "上一轮 _Q 帧文件必须被幂等清理")
        self.assertEqual(list(self.states.glob("shock_T*.png")), [])
        self.assertEqual(len(list(self.states.glob("shock_Q*.png"))), 30)
        data1 = self._read_manifest()
        self.assertEqual(data1["states"]["shock"]["frames"][:6],
                         [f"states/shock_D{i:03d}.png" for i in range(6)])
        self.assertEqual(data1["states"]["shock"]["transition_frames"],
                         [f"states/shock_Q{i:03d}.png" for i in range(30)])
        bytes1 = {name: [(self.states / pathlib.Path(r).name).read_bytes()
                         for r in data1["states"][name]["transition_frames"]]
                  for name in first}
        second = self._run()
        self.assertEqual({k: len(v) for k, v in second.items()}, 
                         {k: len(v) for k, v in first.items()},
                         "重跑不得再次追加：先清旧 _Q 再生成")
        self.assertEqual(first, second, "重跑 frames 列表必须逐字一致")
        data2 = self._read_manifest()
        self.assertEqual(data1["states"]["shock"]["transition_frames"],
                         data2["states"]["shock"]["transition_frames"],
                         "重跑 transition_frames 必须逐字一致")
        bytes2 = {name: [(self.states / pathlib.Path(r).name).read_bytes()
                         for r in data2["states"][name]["transition_frames"]]
                  for name in first}
        self.assertEqual(bytes1, bytes2, "重跑产物必须字节级一致")

    # ------------------------------------------------------------ 换装点平滑
    def test_swap_jump_below_hard_cut_three_states(self):
        """换装点单帧跳变 < 旧硬切相邻差，且压扁段每一步都比换装点更平滑。

        旧 12 帧渐变把 A→B 同一跳变摊成 M/11 的每步差，但每一帧都是
        50% 叠影鬼影；新方案在最大压扁处单帧完成换装换取零叠影，跳变
        必须仍显著低于「不做任何转场」的硬切 M（压扁确实收窄了跳变）。
        """
        for name, n_base in (("shock", 6), ("cry", 2), ("dance", 2)):
            self._run((name,))
            data = self._read_manifest()["states"][name]
            seq = (prep_assets.load_state_frames(
                       self.states, {"frames": data["frames"]})
                   + prep_assets.load_state_frames(
                       self.states, {"frames": data["transition_frames"]}))
            a = seq[n_base - 1]
            idle = Image.open(self.states / "idle.png").convert("RGBA")
            b = idle.resize(a.size) if idle.size != a.size else idle
            hard_cut = _mean_diff(a, b)
            self.assertGreater(hard_cut, 0.0, f"{name} 夹具两姿态必须有差异")
            diffs = [_mean_diff(x, y) for x, y in zip(seq, seq[1:])]
            swap = n_base + 12 if name != "dance" else n_base + 10  # 换装帧下标
            swap_jump = max(diffs[swap - 1], diffs[swap])
            self.assertLess(swap_jump, hard_cut,
                            f"{name} 换装点跳变必须低于硬切（压扁收窄姿态差）")
            self.assertLess(max(diffs[:swap - 1]), swap_jump,
                            f"{name} 压扁段每一步都必须比换装点更平滑（曲线连续）")

    # -------------------------------------------------------- 一键重烤入口
    def test_rebuild_all_one_click_and_idempotent(self):
        summary = prep_assets.rebuild_all_animation_assets(
            manifest_path=self.manifest_path, states_dir=self.states,
            idle_img=self.states / "idle.png")
        data = self._read_manifest()["states"]
        # v4：frames 保持纯表演帧，转场帧独立成 transition_frames 字段
        self.assertEqual(len(data["shock"]["frames"]), 6)
        self.assertEqual(len(data["shock"]["transition_frames"]), 30)
        self.assertEqual(len(data["dance"]["frames"]), 2)
        self.assertEqual(len(data["dance"]["transition_frames"]), 24)
        # v4 字段齐备：hold_seconds/max_seconds 显式写出
        self.assertEqual(data["shock"]["hold_seconds"], 0.0)
        self.assertEqual(data["shock"]["max_seconds"],
                         round((6 * 33 + 0 + 30 * 33 + 1000) / 1000.0, 3))
        # cheer 派对：45 帧循环档
        self.assertEqual(len(data["cheer"]["frames"]), 45)
        self.assertEqual(data["cheer"]["frame_ms"], 33)
        self.assertEqual(data["cheer"]["play"], "loop")
        self.assertFalse(data["cheer"].get("pingpong"))
        # sleep 提速（主人拍板）：帧图不动，纯播放节拍 90ms（80~100 区间）
        self.assertTrue(80 <= data["sleep"]["frame_ms"] <= 100,
                        "sleep frame_ms 必须落在 80~100 提速区间")
        self.assertEqual(summary["sleep_frame_ms"], 90)
        text1 = self.manifest_path.read_text(encoding="utf-8")
        prep_assets.rebuild_all_animation_assets(
            manifest_path=self.manifest_path, states_dir=self.states,
            idle_img=self.states / "idle.png")
        self.assertEqual(text1,
                         self.manifest_path.read_text(encoding="utf-8"),
                         "一键重跑 manifest 必须字节级一致（幂等）")


if __name__ == "__main__":
    unittest.main()
