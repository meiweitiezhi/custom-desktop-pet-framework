"""动作点播播放器（ActionPlayer）纯逻辑测试 + SetState 排队裁决测试。

全程无 GUI、无网络：玩家只认 spec 字典与 dt 浮点秒，不碰 Qt 不读文件。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw.action_player import (  # noqa: E402
    ActionPlayer,
    action_duration_seconds,
)


def _spec(n=3, frame_ms=100, play="once"):
    return {"frames": [f"f{i}" for i in range(n)],
            "frame_ms": frame_ms, "play": play}


class TestOnceMode(unittest.TestCase):
    def test_not_started_returns_none(self):
        p = ActionPlayer()
        self.assertIsNone(p.tick(0.016))
        self.assertFalse(p.alive)

    def test_once_plays_exact_rounds_then_none(self):
        # n=3, frame_ms=100 -> 一轮 0.3 秒；按半帧 50ms 步进：每帧驻留两拍，
        # 恰好在累计 0.3s 的那一下收到 None，此后永远 None 不复活
        p = ActionPlayer()
        p.start(_spec(n=3, frame_ms=100, play="once"))
        self.assertTrue(p.alive)
        seq = [p.tick(0.05) for _ in range(20)]
        self.assertEqual(seq[:6], [0, 1, 1, 2, 2, None])
        self.assertTrue(all(s is None for s in seq[5:]),
                        "once 播完必须终止，不能续杯")
        self.assertEqual(round(action_duration_seconds(
            _spec(3, 100)), 6), 0.3)

    def test_once_finish_reports_requested_state(self):
        p = ActionPlayer()
        p.start(_spec(), on_finish_state="cheer")
        self.assertEqual(p.on_finish_state, "cheer")
        p2 = ActionPlayer()
        p2.start(_spec())
        self.assertEqual(p2.on_finish_state, "idle", "缺省回落 idle")

    def test_big_dt_jump_still_ends_exactly(self):
        # 一口吞下远超一轮的 dt 也只算一轮账，立刻收摊不越界爆帧
        p = ActionPlayer()
        p.start(_spec(n=4, frame_ms=50, play="once"))
        values = []
        idx = p.tick(5.0)
        while idx is not None and len(values) < 10:
            values.append(idx)
            idx = p.tick(0.01)
        self.assertIsNone(idx)
        self.assertLessEqual(max(values or [0]), 3)


class TestLoopMode(unittest.TestCase):
    def test_loop_never_terminates_and_wraps(self):
        # 半帧步进：跨过边界的当拍立刻亮新帧，此后驻留一拍再跨
        p = ActionPlayer()
        p.start(_spec(n=3, frame_ms=50, play="loop"))
        seq = [p.tick(0.025) for _ in range(400)]
        self.assertTrue(all(s is not None for s in seq))
        self.assertEqual(seq[:8], [0, 1, 1, 2, 2, 0, 0, 1])

    def test_missing_play_field_defaults_loop(self):
        # 向后兼容：manifest 老条目没写 play 就按循环待机处理
        spec = {"frames": ["a", "b"], "frame_ms": 80}
        p = ActionPlayer()
        p.start(spec)
        vals = {p.tick(0.08) for _ in range(12)}
        self.assertEqual(vals, {0, 1})


class TestLifecycle(unittest.TestCase):
    def test_restart_resets_accumulator_cleanly(self):
        p = ActionPlayer()
        p.start(_spec(n=3, frame_ms=100, play="once"))
        p.tick(0.07)                       # 第一轮里走到一半
        p.start(_spec(n=3, frame_ms=100, play="once"),
                on_finish_state="sleep")   # 中途换节目
        self.assertEqual(p.tick(0.01), 0, "重启必须从头一帧开始")
        self.assertEqual(p.on_finish_state, "sleep", "结束去向同步更新")

    def test_dt_debt_is_cleared_without_drift(self):
        # 误差清账：200 个 1ms 小步恰好凑满总时长（0.2s）即收摊，
        # 既不许提前夭折，也不许因浮点残渣赖着不走
        p = ActionPlayer()
        p.start(_spec(n=10, frame_ms=20, play="once"))
        ticks_to_finish = 0
        while p.tick(0.001) is not None:
            ticks_to_finish += 1
            self.assertLess(ticks_to_finish, 400, "once 模式赖场了")
        self.assertAlmostEqual(ticks_to_finish, 200, delta=3)

    def test_bad_spec_never_crashes(self):
        for bad in ({}, {"frames": []}, {"frames": ["x"], "frame_ms": 0},
                    {"frames": ["x"], "frame_ms": "abc"}, None):
            p = ActionPlayer()
            p.start(bad)                   # 垃圾输入不许抛错
            self.assertFalse(p.alive)
            self.assertIsNone(p.tick(0.016))


# ------------------------------------------------- SetState 让路裁决（纯函数）
class TestDeferIfPlaying(unittest.TestCase):
    def test_playing_defers_and_last_request_wins(self):
        from petfw.host import defer_if_playing
        # 表演中：第一笔排队、第二笔覆盖——候补位只留最后请求
        target, pending = defer_if_playing(None, True, "eat")
        self.assertIsNone(target)
        self.assertEqual(pending, "eat")
        target, pending = defer_if_playing("eat", True, "laugh")
        self.assertIsNone(target)
        self.assertEqual(pending, "laugh", "后来的请求必须顶掉先来的")

    def test_idle_applies_now_and_clears_stale_pending(self):
        from petfw.host import defer_if_playing
        target, pending = defer_if_playing("eat", False, "shock")
        self.assertEqual(target, "shock", "空闲时切表情立即生效")
        self.assertIsNone(pending, "过期的候补要顺手清掉")


if __name__ == "__main__":
    unittest.main()
