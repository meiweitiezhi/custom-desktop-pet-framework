"""结算画面（任务一）纯逻辑测试：文案生成、每日定时毫秒差、BGM 探测。

全程无 GUI、无网络、不依赖本机是否真有 assets/local/bgm.mp3。
"""
import datetime
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw.config import TEMPLATE  # noqa: E402
from petfw.settlement_core import (  # noqa: E402
    build_settlement_lines,
    find_bgm,
    next_delay_ms,
)


class TestSettlementLines(unittest.TestCase):
    def test_lines_contain_commits_and_title(self):
        lines = build_settlement_lines(3, "卷王蛋", False, "2026-08-27")
        body = "\n".join(lines)
        self.assertIn("×3", body)          # KO 提交数里必须出现数值
        self.assertIn("「卷王蛋」", body)   # 称号字符串必须出现
        self.assertIn("2026-08-27", body)
        # 游戏风的头尾框架行
        self.assertEqual(lines[0], "━━━━ 今日回合 ━━━━")
        self.assertEqual(lines[-1], "———— 按任意处结束回放 ————")

    def test_levelup_flourish_line_appears_only_when_leveled(self):
        up = build_settlement_lines(30, "代码之蛋", True, "2026-08-27")
        down = build_settlement_lines(30, "代码之蛋", False, "2026-08-27")
        self.assertIn("↻ 称号进化发生 ↻", up)
        self.assertNotIn("↻ 称号进化发生 ↻", down)

    def test_garbage_inputs_do_not_crash(self):
        for commits, title, date_str in (
            (None, None, None), (-5, "", ""), (10 ** 12, 12345, 7.5),
            ("abc", ["x"], object()),
        ):
            try:
                lines = build_settlement_lines(commits, title, "?", date_str)
            except Exception as e:  # pragma: no cover - 失败时给出具体输入
                self.fail(f"异常值 {commits!r}/{title!r} 炸了: {e}")
            self.assertTrue(lines)

    def test_negative_commits_clamped_to_zero(self):
        body = "\n".join(build_settlement_lines(-9, "咸鱼蛋", False, "d"))
        self.assertIn("×0", body)
        self.assertNotIn("-9", body)


class TestNextDelay(unittest.TestCase):
    def test_same_day_distance(self):
        now = datetime.datetime(2026, 8, 27, 10, 0, 0)
        ms = next_delay_ms(now, "18:00")
        self.assertEqual(ms, 8 * 3600 * 1000)

    def test_past_time_schedules_tomorrow(self):
        now = datetime.datetime(2026, 8, 27, 19, 0, 0)
        ms = next_delay_ms(now, "18:00")
        self.assertEqual(ms, 23 * 3600 * 1000)

    def test_exact_time_counts_as_passed(self):
        # 恰好压点启动：排明天，避免与业务重复触发
        now = datetime.datetime(2026, 8, 27, 18, 0, 0)
        ms = next_delay_ms(now, "18:00")
        self.assertEqual(ms, 24 * 3600 * 1000)

    def test_bad_format_falls_back_to_default(self):
        now = datetime.datetime(2026, 8, 27, 10, 0, 0)
        for bad in ("25:99", "abc", "", None, "99"):
            self.assertEqual(next_delay_ms(now, bad), 8 * 3600 * 1000,
                             f"daily_time={bad!r} 应回落默认 18:00")

    def test_result_always_positive(self):
        now = datetime.datetime(2026, 8, 27, 23, 59, 59)
        self.assertGreater(next_delay_ms(now, "23:59"), 0)


class TestFindBgm(unittest.TestCase):
    def test_mp3_preferred_over_m4a(self):
        with tempfile.TemporaryDirectory() as td:
            local = pathlib.Path(td) / "local"
            local.mkdir()
            (local / "bgm.m4a").write_bytes(b"x")
            (local / "bgm.mp3").write_bytes(b"x")
            got = find_bgm(td)
            self.assertIsNotNone(got)
            self.assertEqual(got.suffix, ".mp3")

    def test_m4a_only_still_found(self):
        with tempfile.TemporaryDirectory() as td:
            local = pathlib.Path(td) / "local"
            local.mkdir()
            (local / "bgm.m4a").write_bytes(b"x")
            self.assertEqual(find_bgm(td).name, "bgm.m4a")

    def test_missing_everything_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(find_bgm(td))          # 目录在但没有文件
        # 目录不存在 / 非法输入都不许炸
        self.assertIsNone(find_bgm(pathlib.Path(tempfile.mkdtemp()) / "nope"))
        self.assertIsNone(find_bgm(None))

    def test_extra_dirs_fallback_for_frozen_deploy(self):
        # exe 分发场景：包内没有，exe 旁的 assets/local/ 兜底命中
        with tempfile.TemporaryDirectory() as td:
            bundle = pathlib.Path(td) / "bundle"
            bundle.mkdir()
            side = pathlib.Path(td) / "side" / "local"
            side.mkdir(parents=True)
            (side / "bgm.mp3").write_bytes(b"x")
            self.assertIsNone(find_bgm(bundle))
            got = find_bgm(bundle, extra_dirs=(side.parent,))
            self.assertIsNotNone(got)
            self.assertEqual(got.name, "bgm.mp3")


class TestTemplateHasSettlement(unittest.TestCase):
    def test_template_contains_section(self):
        self.assertIn("[settlement]", TEMPLATE)
        self.assertIn("enabled", TEMPLATE.split("[settlement]", 1)[1])
        self.assertIn("daily_time", TEMPLATE.split("[settlement]", 1)[1])


if __name__ == "__main__":
    unittest.main()
