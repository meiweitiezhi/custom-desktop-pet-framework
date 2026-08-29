"""表演窗口（v5）：once 表演段支持 rounds 轮数 / perform_seconds 秒窗口。

主人拍板：一轮太快看不清——shock 表演段演 2 轮（28 帧×2）；sleep/cry
表演段持续 5 秒（帧在窗口内取模循环，乒乓在窗口内连续往返不断轮），
到点再走定格/转场谢幕。优先级 rounds > perform_seconds > 缺省一轮；
不写新字段的旧状态（dance 等）一轮照旧。
全程纯逻辑 + manifest 真值，无 GUI 无网络。
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw.action_player import (  # noqa: E402
    ActionPlayer,
    action_duration_seconds,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "assets" / "manifest.json"


def _win_spec(n=3, frame_ms=100, rounds=None, psecs=None,
              pingpong=False, hold=0.0, trans=0):
    """表演窗口夹具：n 帧表演 + 可选轮数/秒窗口 + 定格/转场按需拼装。"""
    spec = {"frames": [f"f{i}" for i in range(n)],
            "transition_frames": [f"q{i}" for i in range(trans)],
            "frame_ms": frame_ms,
            "hold_seconds": hold,
            "max_seconds": 99.0,
            "play": "once",
            "return_to": "idle"}
    if rounds is not None:
        spec["rounds"] = rounds
    if psecs is not None:
        spec["perform_seconds"] = psecs
    if pingpong:
        spec["pingpong"] = True
    return spec


# ======================================================== 玩家窗口行为
class TestRoundsWindow(unittest.TestCase):
    """rounds=N：表演段时长 = N × 帧一轮，帧下标每轮从头再演。"""

    def test_rounds_two_perform_last_exactly_two_rounds(self):
        p = ActionPlayer()
        p.start(_win_spec(n=3, frame_ms=100, rounds=2, hold=0.5, trans=2))
        durs = {name: dur for name, dur in p.segments}
        self.assertAlmostEqual(durs["perform"], 0.6, places=9,
                               msg="表演段必须恰为 2×帧一轮（0.3×2）")
        self.assertAlmostEqual(durs["hold"], 0.5, places=9)
        self.assertAlmostEqual(durs["transition"], 0.2, places=9)
        self.assertAlmostEqual(p.total_s, 0.6 + 0.5 + 0.2, places=9)

    def test_rounds_second_round_restarts_frame_from_zero(self):
        # n=3 @100ms、先 tick(0.0) 亮首帧再每拍 0.1s：第二轮必须重新从 0 起
        p = ActionPlayer()
        p.start(_win_spec(n=3, frame_ms=100, rounds=2))
        seq = [p.tick(0.0)]
        for _ in range(8):
            seq.append(p.tick(0.1))
            if seq[-1] is None:
                break
        self.assertEqual(seq, [0, 1, 2, 0, 1, 2, None],
                         "第二轮必须从 0 重演：0,1,2 → 0,1,2 → 谢幕")
        self.assertFalse(p.alive)

    def test_rounds_priority_over_perform_seconds(self):
        # 两字段同写：rounds 优先，表演段 0.6s 而不是 5.0s
        p = ActionPlayer()
        p.start(_win_spec(n=3, frame_ms=100, rounds=2, psecs=5.0))
        self.assertAlmostEqual(p.segments[0][1], 0.6, places=9,
                               msg="rounds 必须压过 perform_seconds")


class TestPerformSecondsWindow(unittest.TestCase):
    """perform_seconds=N：表演段持续 N 秒，帧在窗口内取模循环。"""

    def test_frames_keep_modulo_cycling_inside_window(self):
        # n=3 @100ms、先亮首帧再每拍 0.1s：5 秒窗口内帧持续取模循环不卡末帧
        p = ActionPlayer()
        p.start(_win_spec(n=3, frame_ms=100, psecs=5.0))
        seq = [p.tick(0.0)] + [p.tick(0.1) for _ in range(9)]
        self.assertEqual(seq, [0, 1, 2, 0, 1, 2, 0, 1, 2, 0],
                         "窗口内必须取模循环：0,1,2 反复续杯")

    def test_window_cuts_to_next_segment_at_exact_end(self):
        # 5.0 秒整必须切下一段：有定格就定格亮末帧，无下段就当场谢幕
        p = ActionPlayer()
        p.start(_win_spec(n=3, frame_ms=100, psecs=5.0, hold=0.4, trans=2))
        for _ in range(50):                 # 50 拍 × 0.1s = 5.0s 整
            p.tick(0.1)
        self.assertEqual(p.segment, "hold", "5.0 秒整必须切进定格段")
        self.assertEqual(p.tick(0.0), 2, "定格段恒亮末帧")
        p2 = ActionPlayer()
        p2.start(_win_spec(n=3, frame_ms=100, psecs=5.0))
        seq = []
        for _ in range(51):
            seq.append(p2.tick(0.1))
            if seq[-1] is None:
                break
        self.assertEqual(len(seq), 50, "49 拍表演帧 + 第 50 拍整点谢幕")
        self.assertIsNone(seq[-1], "无下段时 5.0 秒整必须当场谢幕")

    def test_big_dt_jump_across_window_ends_cleanly(self):
        # 一口吞下远超窗口的 dt 也只走完时间线就收摊，不越界爆帧
        p = ActionPlayer()
        p.start(_win_spec(n=3, frame_ms=100, psecs=5.0, hold=0.4, trans=2))
        self.assertIsNone(p.tick(99.0), "越过全时间线必须直接谢幕")
        self.assertFalse(p.alive)

    def test_garbage_window_fields_fall_back_to_one_round(self):
        # rounds/perform_seconds 写了垃圾值一律当没写：旧一轮行为兜底
        for bad in (_win_spec(rounds="abc"), _win_spec(rounds=-2),
                    _win_spec(psecs="x"), _win_spec(psecs=-1.5),
                    _win_spec(rounds=0, psecs=0)):
            p = ActionPlayer()
            p.start(bad)
            self.assertAlmostEqual(p.segments[0][1], 0.3, places=9,
                                   msg=f"垃圾窗口字段必须回落一轮: {bad}")
            seq = []
            for _ in range(10):
                seq.append(p.tick(0.05))
                if seq[-1] is None:
                    break
            self.assertEqual(seq[:6], [0, 1, 1, 2, 2, None],
                             "回落一轮后必须照旧播到尾即谢幕")


class TestWindowPingpong(unittest.TestCase):
    """乒乓与窗口叠加：窗口内往返连续不断轮（不逐轮重启）。"""

    def test_pingpong_mirrors_continuously_across_rounds(self):
        # n=3 @100ms、rounds=2：周期 4 的镜像 0,1,2,1 | 0,1 —— 不逐轮重启
        p = ActionPlayer()
        p.start(_win_spec(n=3, frame_ms=100, rounds=2, pingpong=True))
        seq = [p.tick(0.0)]
        for _ in range(6):
            seq.append(p.tick(0.1))
            if seq[-1] is None:
                break
        self.assertEqual(seq, [0, 1, 2, 1, 0, 1, None],
                         "乒乓必须跨轮连续往返：…2,1→0… 不断轮，窗口耗尽谢幕")
        self.assertFalse(p.alive)

    def test_pingpong_mirrors_inside_seconds_window(self):
        # n=3 @100ms、窗口 1.0 秒：全程镜像往返 10 帧到点谢幕
        p = ActionPlayer()
        p.start(_win_spec(n=3, frame_ms=100, psecs=1.0, pingpong=True))
        seq = [p.tick(0.0)]
        for _ in range(11):
            seq.append(p.tick(0.1))
            if seq[-1] is None:
                break
        self.assertEqual(seq, [0, 1, 2, 1, 0, 1, 2, 1, 0, 1, None],
                         "窗口内乒乓连续往返不断轮，到点谢幕")

    def test_once_pingpong_without_window_still_plains_to_tail(self):
        # 旧规矩不破：once+pingpong 但没写窗口字段 → 照旧线性播到尾即谢幕
        p = ActionPlayer()
        p.start(_win_spec(n=3, frame_ms=100, pingpong=True))
        seq = []
        for _ in range(10):
            seq.append(p.tick(0.05))
            if seq[-1] is None:
                break
        self.assertEqual(seq[:6], [0, 1, 1, 2, 2, None],
                         "无窗口的 once+pingpong 必须照旧播到尾")


class TestDurationHelper(unittest.TestCase):
    """action_duration_seconds 同步认账：表演段按窗口计。"""

    def test_duration_honors_rounds_and_seconds(self):
        self.assertAlmostEqual(action_duration_seconds(
            _win_spec(n=3, frame_ms=100, rounds=2, hold=0.5, trans=2)),
            0.6 + 0.5 + 0.2, places=9)
        self.assertAlmostEqual(action_duration_seconds(
            _win_spec(n=3, frame_ms=100, psecs=5.0)),
            5.0, places=9)
        # 旧形态（无窗口字段）照旧一轮口径
        self.assertAlmostEqual(action_duration_seconds(
            _win_spec(n=3, frame_ms=100, hold=0.5, trans=2)),
            0.3 + 0.5 + 0.2, places=9)


# ======================================================== manifest 真值
class TestManifestPerformWindow(unittest.TestCase):
    """真实 manifest 的 v5 窗口字段与保险丝精算（读真值显式断言）。"""

    @classmethod
    def setUpClass(cls):
        cls.states = json.loads(
            MANIFEST.read_text(encoding="utf-8"))["states"]

    def _player(self, name):
        p = ActionPlayer()
        p.start(dict(self.states[name]))
        return p

    def test_shock_rounds_two_with_precise_fuse(self):
        spec = self.states["shock"]
        self.assertEqual(spec.get("rounds"), 2, "shock 表演段必须恰 2 轮")
        self.assertNotIn("perform_seconds", spec,
                         "shock 用轮数口径，不写秒窗口")
        p = self._player("shock")
        durs = {name: dur for name, dur in p.segments}
        self.assertAlmostEqual(durs["perform"], 2 * 28 * 0.033, places=9)
        self.assertAlmostEqual(durs["hold"], 1.2, places=9)
        self.assertAlmostEqual(durs["transition"], 30 * 0.033, places=9)
        # 保险丝精算：2×0.924 + 1.2 + 0.99 + 1.0 宽限 = 5.038，显式锁值
        self.assertAlmostEqual(float(spec["max_seconds"]), 5.038, places=9)
        self.assertAlmostEqual(p.total_s + 1.0,
                               float(spec["max_seconds"]), places=9,
                               msg="max_seconds 必须 = 三段和 + 1 秒宽限")

    def test_shock_second_round_restarts_with_real_frames(self):
        # 28 帧 @33ms 一轮 0.924s、窗口 2 轮；shock 带乒乓：第一轮 0→27
        # 升到顶，0.93s 起第二轮按镜像折返（26 往下走），仍在表演段窗口内
        p = self._player("shock")
        self.assertTrue(p.perform_pingpong, "shock 窗口表演必须认乒乓")
        elapsed = 0.0
        seen = []
        while elapsed < 0.93:
            seen.append(p.tick(0.03))
            elapsed += 0.03
        self.assertEqual(p.segment, "perform", "0.93s 仍在表演段窗口内")
        self.assertEqual(seen[-1], 26,
                         "第二轮乒乓折返：cycle 28 镜像回下标 26 往下走")

    def test_sleep_five_second_window_with_precise_fuse(self):
        spec = self.states["sleep"]
        self.assertEqual(spec.get("perform_seconds"), 5.0,
                         "sleep 表演段必须持续 5 秒")
        self.assertNotIn("rounds", spec, "sleep 用秒窗口口径，不写轮数")
        p = self._player("sleep")
        self.assertEqual([(n, round(d, 9)) for n, d in p.segments],
                         [("perform", 5.0)],
                         "sleep 无定格无转场：时间线只有 5 秒表演段")
        self.assertAlmostEqual(float(spec["max_seconds"]), 6.0, places=9,
                               msg="保险丝 = 5.0 + 0 + 0 + 1.0 宽限")
        # 窗口内帧持续取模循环：88 帧 @22ms 一轮 1.936s，约 2.5 轮
        idx = p.tick(4.95)
        self.assertTrue(0 <= idx < 88, "窗口内帧下标必须落在表演帧范围内")
        self.assertEqual(p.segment, "perform")
        self.assertIsNone(p.tick(0.05), "5.0 秒整当场谢幕（无下段）")

    def test_cry_five_second_window_then_hold_transition(self):
        spec = self.states["cry"]
        self.assertEqual(spec.get("perform_seconds"), 5.0,
                         "cry 表演段必须持续 5 秒")
        p = self._player("cry")
        durs = {name: dur for name, dur in p.segments}
        self.assertAlmostEqual(durs["perform"], 5.0, places=9)
        self.assertAlmostEqual(durs["hold"], 1.2, places=9)
        self.assertAlmostEqual(durs["transition"], 30 * 0.033, places=9)
        # 保险丝精算：5.0 + 1.2 + 0.99 + 1.0 宽限 = 8.19，显式锁值
        self.assertAlmostEqual(float(spec["max_seconds"]), 8.19, places=9)
        self.assertAlmostEqual(p.total_s + 1.0,
                               float(spec["max_seconds"]), places=9,
                               msg="max_seconds 必须 = 三段和 + 1 秒宽限")
        for _ in range(10):                 # 10 拍 × 0.5s = 5.0s 整
            p.tick(0.5)
        self.assertEqual(p.segment, "hold", "cry 必须 5.0 秒整切进定格")
        self.assertEqual(p.tick(0.0), 27, "定格恒亮末帧（28 帧的下标 27）")
        p.tick(1.2)
        self.assertEqual(p.segment, "transition", "定格完必须进转场")

    def test_dance_without_window_fields_regression(self):
        # 无新字段的旧状态回归不变：dance 一轮照旧、保险丝现值不动
        spec = self.states["dance"]
        self.assertNotIn("rounds", spec)
        self.assertNotIn("perform_seconds", spec)
        p = self._player("dance")
        durs = {name: dur for name, dur in p.segments}
        # dance 视频重建档：帧数随源视频浮动，一律读 manifest 真值折算
        ms = float(spec["frame_ms"]) / 1000.0
        self.assertGreater(len(spec["frames"]), 24,
                           "dance 全帧档必须多于 24 帧（真值驱动不锁死）")
        self.assertAlmostEqual(durs["perform"], len(spec["frames"]) * ms,
                               places=9,
                               msg="dance 表演段必须照旧只播一轮")
        self.assertNotIn("hold", durs,
                         "dance 定格 0 秒：0 长段不许出现在时间线上")
        self.assertAlmostEqual(durs["transition"],
                               len(spec["transition_frames"]) * ms, places=9)
        expected_max = round(len(spec["frames"]) * ms
                             + len(spec["transition_frames"]) * ms + 1.0, 3)
        self.assertAlmostEqual(float(spec["max_seconds"]), expected_max,
                               places=9,
                               msg="max_seconds 必须 = 表演+转场+1s 宽限")
        self.assertAlmostEqual(p.total_s + 1.0,
                               float(spec["max_seconds"]), places=9)
        p.tick(0.5)
        self.assertEqual(p.segment, "perform", "0.5s 时仍在一轮表演段内")


if __name__ == "__main__":
    unittest.main()
