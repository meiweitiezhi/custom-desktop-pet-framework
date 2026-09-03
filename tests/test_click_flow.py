"""点击管线纯逻辑测试：280ms 双击判定 / 结算忽略 / click_sfx 配置裁决。

全程无 GUI、无网络：窗口语义用可注入时钟测试，不做真 QTimer 测试。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw.click_flow import (  # noqa: E402
    DOUBLE_CLICK_MS,
    ClickResolver,
    resolve_click,
    resolve_click_sfx,
    should_perform,
)


class _FakeClock:
    """手动拨的时钟：monotonic() 返回可推近的秒值。"""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestResolveClick(unittest.TestCase):
    """纯分类器：窗口收点数 -> 裁决词。"""

    def test_three_verdicts(self):
        self.assertEqual(resolve_click(1), "single")
        self.assertEqual(resolve_click(2), "double")
        self.assertEqual(resolve_click(3), "double", "三连点按双击算")
        self.assertEqual(resolve_click(0), "pending")

    def test_garbage_counts_pending(self):
        for bad in (-1, None):
            with self.subTest(bad=bad):
                self.assertEqual(resolve_click(bad), "pending")

    def test_window_constant(self):
        self.assertEqual(DOUBLE_CLICK_MS, 280)


class TestClickResolver(unittest.TestCase):
    """可注入时钟的窗口状态机：pending -> single/double。"""

    def setUp(self):
        self.clock = _FakeClock()
        self.r = ClickResolver(now=self.clock, window_ms=DOUBLE_CLICK_MS)

    def test_first_press_is_pending(self):
        self.assertEqual(self.r.press(), "pending")
        self.assertTrue(self.r.armed())

    def test_second_press_within_window_is_double(self):
        self.r.press()
        self.clock.advance(0.279)          # 窗口内 279ms
        self.assertEqual(self.r.press(), "double")
        self.assertFalse(self.r.armed(), "双击后不再挂起")

    def test_second_press_after_window_rearms(self):
        self.r.press()
        self.clock.advance(0.281)          # 出窗 281ms：算新一轮第一击
        self.assertEqual(self.r.press(), "pending")

    def test_timeout_single_and_unarmed_timeout_pending(self):
        self.r.press()
        self.clock.advance(0.280)
        self.assertEqual(self.r.timeout(), "single")
        self.assertFalse(self.r.armed())
        self.assertEqual(self.r.timeout(), "pending", "没挂着就超时不算演出")

    def test_cancel_drops_pending(self):
        self.r.press()
        self.r.cancel()
        self.assertFalse(self.r.armed())
        self.assertEqual(self.r.timeout(), "pending", "取消后超时不许再判 single")
        self.assertEqual(self.r.press(), "pending", "取消后还能开新窗口")


class TestSettlementClause(unittest.TestCase):
    """节假日不生效条款：结算画面开着时一切点击演出都不放。"""

    def test_settlement_open_blocks_show(self):
        self.assertFalse(should_perform(True))
        self.assertTrue(should_perform(False))


class TestResolveClickSfx(unittest.TestCase):
    """[sound] click_sfx 配置裁决：填了且文件存在才用专属音效，否则回落 pop。"""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.wav = self.tmp / "click.wav"
        self.wav.write_bytes(b"RIFF----WAVE")

    def test_empty_defaults_to_local_click_wav(self):
        # 配置留空 = 开箱即唱：默认尝试 assets/local/click.wav（相对 base_dir）
        (self.tmp / "assets").mkdir()
        (self.tmp / "assets" / "local").mkdir()
        (self.tmp / "assets" / "local" / "click.wav").write_bytes(b"x")
        got = resolve_click_sfx("", self.tmp)
        self.assertEqual(pathlib.Path(got).name, "click.wav")
        # 默认路径也不存在时才回落 pop
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(resolve_click_sfx("", pathlib.Path(td)))

    def test_existing_file_returns_absolute_path(self):
        got = resolve_click_sfx(str(self.wav), self.tmp)
        self.assertEqual(pathlib.Path(got), self.wav)
        # 相对路径相对 base_dir 解析
        got = resolve_click_sfx("click.wav", self.tmp)
        self.assertEqual(pathlib.Path(got), self.wav)

    def test_missing_file_falls_back_to_pop(self):
        self.assertIsNone(resolve_click_sfx("nope.wav", self.tmp))
        self.assertIsNone(resolve_click_sfx(str(self.tmp / "ghost.wav")))


# ------------------------------------------------- host 演出参数（duck-typing）
class _FakeMusic:
    """假 MusicPlayer：is_playing 可拨档，play 可编排成功/失败。"""

    def __init__(self, playing=False, play_ok=True):
        self._playing = playing
        self._play_ok = play_ok
        self.play_calls = []      # [(path, volume)]
        self.finish_cbs = []
        self.stop_calls = 0

    def is_playing(self):
        return self._playing

    def play(self, path, volume):
        self.play_calls.append((path, volume))
        return self._play_ok

    def on_finished(self, cb):
        self.finish_cbs.append(cb)

    def duration_seconds(self):
        return 0.0

    def stop(self):
        self.stop_calls += 1
        self._playing = False


class _RecordingWin:
    """假宿主：只记录 apply/play_action/play 专音效/music 的调用。"""

    def __init__(self, settlement_open=False, music_ok=True):
        self.settlement_open = settlement_open
        self.applied = []
        self.played = []          # [(name, play, hold_tail_ms, spec)]
        self.click_sfx_plays = 0
        self.suck_plays = 0
        self.music = _FakeMusic(play_ok=music_ok)
        self.music_path = None    # 缺省无 mp3 -> 单击回落「戳我」现状
        self.states = {"dance": {"frames": ["d0.png", "d1.png"],
                                 "frame_ms": 41, "return_to": "idle"}}

    def apply(self, cmds):
        self.applied += cmds

    def play_action(self, name, play=None, hold_tail_ms=None, spec=None):
        self.played.append((name, play, hold_tail_ms, spec))
        return True

    def _play_click_sfx(self):
        self.click_sfx_plays += 1

    def play(self, name):
        if name == "suck":
            self.suck_plays += 1


class TestHostClickShows(unittest.TestCase):
    """单击/双击演出的参数快照：点歌、忽略条款、降级与结算忽略。"""

    def _win(self, music_path=None, **kw):
        from petfw.host import PetWindow
        win = PetWindow.__new__(PetWindow)   # 不跑 __init__，只借方法
        rec = _RecordingWin(**kw)
        win.settlement_open = rec.settlement_open
        win._rec = rec
        win.apply = rec.apply
        win.play_action = rec.play_action
        win._play_click_sfx = rec._play_click_sfx
        win.play = rec.play
        win.music = rec.music
        win._music_path = music_path
        win._music_volume = 0.6
        win.states = rec.states
        return win, rec

    def test_single_click_without_music_keeps_shock_no_bubble(self):
        """降级路径：mp3 缺失（music_path=None）回落 shock 定格演出。
        弹字气泡按主人拍板下线（2026-09）：不再 Say，音效与演出照旧。"""
        win, rec = self._win()
        win._perform_single_click()
        self.assertEqual(rec.applied, [], "回落单击不再弹「不要戳我」气泡")
        self.assertEqual(rec.played, [("shock", None, 1200, None)],
                         "回落单击必须演 shock，且带 1200ms 尾部定格")
        self.assertEqual(rec.click_sfx_plays, 1, "click.wav 播放保持不变")
        self.assertEqual(rec.music.play_calls, [], "没曲子就不该碰播放器")

    def test_single_click_starts_song_and_loop_dance(self):
        """主路径：整首播放 + dance 循环伴舞 + 登记歌完回调，无气泡无戳我。"""
        import tempfile
        song = pathlib.Path(tempfile.mkdtemp()) / "bgm.mp3"
        song.write_bytes(b"ID3")
        win, rec = self._win(music_path=song)
        win._perform_single_click()
        self.assertEqual(rec.music.play_calls, [(song, 0.6)],
                         "整首播放，音量取 [sound] music_volume")
        self.assertEqual(len(rec.played), 1)
        name, play, hold, spec = rec.played[0]
        self.assertEqual((name, play), ("dance", "loop"))
        self.assertIsNone(hold)
        self.assertEqual(spec["play"], "loop")
        self.assertEqual(spec["frames"], ["d0.png", "d1.png"])
        self.assertEqual(len(rec.music.finish_cbs), 1, "必须登记歌完回调")
        self.assertEqual(rec.applied, [], "点歌路径不弹「不要戳我」气泡")
        self.assertEqual(rec.click_sfx_plays, 0, "点歌路径不叠 click.wav")

    def test_single_click_falls_back_when_backend_dead(self):
        """降级路径：后端不可用（play 返回 False）回落定格演出，无气泡。"""
        import tempfile
        song = pathlib.Path(tempfile.mkdtemp()) / "bgm.mp3"
        song.write_bytes(b"ID3")
        win, rec = self._win(music_path=song, music_ok=False)
        win._perform_single_click()
        self.assertEqual(rec.applied, [], "弹字气泡已下线，回落不 Say")
        self.assertEqual(rec.played, [("shock", None, 1200, None)])
        self.assertEqual(rec.click_sfx_plays, 1)
        self.assertEqual(len(rec.music.finish_cbs), 0, "没开播就不登记回调")

    def test_single_click_ignored_while_music_playing(self):
        """歌播着的时候单击一律忽略：不重播、不重置、不触发别的演出。"""
        import tempfile
        song = pathlib.Path(tempfile.mkdtemp()) / "bgm.mp3"
        song.write_bytes(b"ID3")
        win, rec = self._win(music_path=song)
        rec.music._playing = True
        win._perform_single_click()
        self.assertEqual(rec.applied, [])
        self.assertEqual(rec.played, [])
        self.assertEqual(rec.click_sfx_plays, 0)
        self.assertEqual(rec.music.play_calls, [])

    def test_single_click_silent_during_settlement(self):
        import tempfile
        song = pathlib.Path(tempfile.mkdtemp()) / "bgm.mp3"
        song.write_bytes(b"ID3")
        win, rec = self._win(music_path=song, settlement_open=True)
        win._perform_single_click()
        self.assertEqual(rec.applied, [])
        self.assertEqual(rec.played, [])
        self.assertEqual(rec.click_sfx_plays, 0)

    def test_double_click_dance_show_with_click_sfx(self):
        """双击=点歌开跳：click.wav（原声）+ dance 演出，UFO 吸入退役。"""
        win, rec = self._win()
        win._perform_double_click()
        self.assertEqual(rec.suck_plays, 0, "合成 suck 音效必须一并退役")
        self.assertEqual(rec.played, [("dance", None, None, None)])
        self.assertEqual(rec.click_sfx_plays, 1, "双击要播 click.wav 原声")
        self.assertEqual(rec.applied, [], "双击不弹气泡")

    def test_double_click_ignored_while_music_playing(self):
        """歌播着的时候双击同样忽略：不重播、不重置 dance。"""
        win, rec = self._win()
        rec.music._playing = True
        win._perform_double_click()
        self.assertEqual(rec.played, [])
        self.assertEqual(rec.click_sfx_plays, 0)

    def test_double_click_silent_during_settlement(self):
        win, rec = self._win(settlement_open=True)
        win._perform_double_click()
        self.assertEqual(rec.suck_plays, 0)
        self.assertEqual(rec.played, [])
        self.assertEqual(rec.click_sfx_plays, 0)

    def test_settlement_open_stops_song_music(self):
        """结算开屏给点歌让路：停歌收回调，画面 BGM 独自上场。"""
        win, rec = self._win(music_path="somewhere/bgm.mp3")
        rec.music._playing = True
        win._prev_state = None
        win.current = "idle"
        win._on_settlement_opened()
        self.assertEqual(rec.music.stop_calls, 1, "结算开屏必须停掉点歌 BGM")
        self.assertTrue(win.settlement_open)

    def test_quit_app_stops_music(self):
        """退出进程前先停歌，别让音乐拖尾。"""
        from petfw.host import PetWindow
        win = PetWindow.__new__(PetWindow)
        rec = _RecordingWin()
        win.bridge = None
        win._save_position = lambda: None
        win.music = rec.music
        win.quit_app()
        self.assertEqual(rec.music.stop_calls, 1)


if __name__ == "__main__":
    unittest.main()
