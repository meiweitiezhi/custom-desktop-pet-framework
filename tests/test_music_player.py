"""music_player 测试：resolve_music 配置裁决 + MusicPlayer 纯逻辑（stub）。

Qt 播放本身不进单测：真播放路径用注入的坏 loader 模拟后端不可用，
其余判定（文件存在性、状态令牌映射、回调一次性语义）全部无头可测。
"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw.music_player import (  # noqa: E402
    DEFAULT_MUSIC,
    MusicPlayer,
    resolve_music,
    status_token,
)


def _boom_loader():
    raise ImportError("no multimedia backend")


class _FakeStatus:
    """模仿 QMediaPlayer.MediaStatus 枚举成员（有 .name）。"""

    def __init__(self, name):
        self.name = name


class TestResolveMusic(unittest.TestCase):
    """[sound] music_file 配置裁决：与 resolve_click_sfx 同风格，绝不抛错。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.mp3 = self.tmp / "assets" / "local" / "bgm.mp3"
        self.mp3.parent.mkdir(parents=True)
        self.mp3.write_bytes(b"ID3fake")

    def test_empty_defaults_to_local_bgm(self):
        got = resolve_music("", (self.tmp,))
        self.assertIsNotNone(got)
        self.assertEqual(pathlib.Path(got).name, "bgm.mp3")
        # 默认路径也不存在时回落 None
        self.assertIsNone(resolve_music("", (self.tmp / "ghost",)))

    def test_default_constant_is_local_bgm(self):
        self.assertEqual(DEFAULT_MUSIC, "assets/local/bgm.mp3")

    def test_relative_resolved_against_base_dirs_in_order(self):
        first = self.tmp / "first"
        second = self.tmp / "second"
        first.mkdir()
        second.mkdir()
        (second / "song.mp3").write_bytes(b"x")
        self.assertEqual(resolve_music("song.mp3", (first, second)),
                         second / "song.mp3")
        (first / "song.mp3").write_bytes(b"y")
        self.assertEqual(resolve_music("song.mp3", (first, second)),
                         first / "song.mp3", "排在前面的 base 优先")

    def test_absolute_path_checked_directly(self):
        self.assertEqual(resolve_music(str(self.mp3), ()), self.mp3)
        self.assertIsNone(resolve_music(str(self.tmp / "nope.mp3"), ()))

    def test_missing_file_and_garbage_return_none_without_raise(self):
        self.assertIsNone(resolve_music("ghost.mp3", (self.tmp,)))
        # None/空串同走默认曲目：setUp 里默认文件存在，应命中它
        self.assertEqual(resolve_music(None, (self.tmp,)), self.mp3)
        # 纯空白配置同走默认曲目；默认在给定 base 下不存在才回落 None
        self.assertIsNone(resolve_music("   ", (self.tmp / "ghost",)))


class TestMusicPlayerPureParts(unittest.TestCase):
    """无 Qt 依赖的部分：构造不碰多媒体、坏输入与坏后端一律 False。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def _real_file(self) -> pathlib.Path:
        f = self.tmp / "real.mp3"
        f.write_bytes(b"ID3")
        return f

    def test_construction_needs_no_qt(self):
        player = MusicPlayer()
        self.assertFalse(player.is_playing())
        self.assertEqual(player.duration_seconds(), 0.0)

    def test_stop_before_any_play_is_safe(self):
        player = MusicPlayer()
        player.stop()
        self.assertFalse(player.is_playing())

    def test_play_missing_or_garbage_path_returns_false(self):
        player = MusicPlayer()
        self.assertFalse(player.play(self.tmp / "ghost.mp3", 0.6))
        self.assertFalse(player.play(None, 0.6))
        self.assertFalse(player.play("", 0.6))
        self.assertFalse(player.is_playing())

    def test_backend_unavailable_returns_false_not_raise(self):
        player = MusicPlayer(qt_loader=_boom_loader)
        self.assertFalse(player.play(self._real_file(), 0.6),
                         "后端坏必须 False，不许抛")
        self.assertFalse(player.is_playing())

    def test_on_finished_dedupes_and_fires_once(self):
        player = MusicPlayer()

        class _Sink:
            def __init__(self):
                self.hits = 0

            def note(self):
                self.hits += 1

        sink = _Sink()
        player.on_finished(sink.note)
        player.on_finished(sink.note)   # 同一宿主方法重复登记只算一次
        player._notify_finished()
        self.assertEqual(sink.hits, 1)
        player._notify_finished()
        self.assertEqual(sink.hits, 1, "回调一次性：触发后即清空")

    def test_on_finished_ignores_uncallable_and_swallows_cb_errors(self):
        player = MusicPlayer()
        player.on_finished(None)
        player.on_finished(42)

        def _bad():
            raise RuntimeError("boom")

        player.on_finished(_bad)
        player._notify_finished()   # 回调炸了也不许向外抛

    def test_status_token_mapping(self):
        self.assertEqual(status_token(_FakeStatus("EndOfMedia")), "EndOfMedia")
        self.assertEqual(status_token(_FakeStatus("LoadedMedia")), "LoadedMedia")
        self.assertEqual(status_token("EndOfMedia"), "EndOfMedia")

    def test_end_of_media_detection_routes_to_notify(self):
        player = MusicPlayer()
        hits = []
        player.on_finished(lambda: hits.append(1))
        player._on_media_status(_FakeStatus("EndOfMedia"))
        self.assertEqual(hits, [1])
        player._on_media_status(_FakeStatus("LoadedMedia"))
        player._on_media_status(_FakeStatus("EndOfMedia"))
        self.assertEqual(hits, [1], "回调清空后不再触发")

    def test_playing_latch_cleared_by_end_and_stop(self):
        """已点播闩：play 成功即算在播（封启动空窗），终态才撤。"""
        player = MusicPlayer()
        self.assertFalse(player.is_playing(), "没点过歌不许算在播")
        player._want_playing = True               # 模拟 play() 成功闩上
        self.assertFalse(player.is_playing(), "没后端实例不算在播")
        # 造一个最小假后端：mediaStatus 返回可映射对象
        player._player = type("_P", (), {
            "mediaStatus": lambda self: _FakeStatus("LoadedMedia")})()
        self.assertTrue(player.is_playing(), "加载/启动空窗期应算在播")
        player._on_media_status(_FakeStatus("EndOfMedia"))
        self.assertFalse(player.is_playing(), "歌完必须撤闩")
        player._want_playing = True
        player._player = type("_P", (), {
            "mediaStatus": lambda self: _FakeStatus("InvalidMedia")})()
        player._on_media_status(_FakeStatus("InvalidMedia"))
        self.assertFalse(player.is_playing(), "坏源撤闩，不许永远忽略点击")
        player._want_playing = True
        player.stop()
        self.assertFalse(player.is_playing(), "stop 撤闩")


class TestTemplateMusicKeys(unittest.TestCase):
    """config 模板 [sound] 段：点歌整首的默认曲目与音量要写进模板。"""

    def test_template_has_music_settings(self):
        from petfw.config import TEMPLATE
        sound = TEMPLATE.split("[sound]", 1)[1]
        self.assertIn("music_file = assets/local/bgm.mp3", sound)
        self.assertIn("music_volume = 0.6", sound)
        self.assertIn("互不影响", sound, "注释要说明与音效 volume 互不影响")


if __name__ == "__main__":
    unittest.main()
