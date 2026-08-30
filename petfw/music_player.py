"""点歌整首的音乐播放器：QMediaPlayer 薄封装 + 曲目配置裁决。

设计原则（与 sound_core / click_flow 一脉相承）：
- 播放本体全程静默降级：文件缺失、QtMultimedia 缺失、后端坏，一律返回
  False / False / False，绝不抛错，宿主据此回落「戳我」演出；
- 纯逻辑与 Qt 播放分离：status_token / _notify_finished / resolve_music
  无头可测（tests 用 stub），真播放路径不进单测；
- 回调一次性：歌完（EndOfMedia）触发一次即清空，重开新歌重新登记，
  避免上一首的旧账在下一首炸响。
"""
from __future__ import annotations

import pathlib

# 主人拍板的默认点歌曲目：本地私有（assets/local/ 整体不入库）
DEFAULT_MUSIC = "assets/local/bgm.mp3"

# music_volume 缺省值：与 [sound] volume 同源默认，但互不影响
DEFAULT_MUSIC_VOLUME = 0.6

# QMediaPlayer.MediaStatus.EndOfMedia 的枚举名（status_token 的映射目标）
_END_TOKEN = "EndOfMedia"


def status_token(status) -> str:
    """Qt 枚举 -> 稳定字符串令牌：有 .name 取 name，否则退化 str()。

    纯映射函数：无 Qt 环境也能测（tests 传假枚举对象）。
    """
    try:
        return str(status.name)
    except AttributeError:
        return str(status)


def resolve_music(raw, base_dirs=()) -> "pathlib.Path | None":
    """[sound] music_file 配置裁决：返回存在的曲目路径或 None。

    与 click_flow.resolve_click_sfx 同风格：
    - 配置留空默认尝试 assets/local/bgm.mp3（开箱即有点歌）；
    - 相对路径按 base_dirs 列表逐个解析（排在前面的优先，frozen 态先
      exe 目录后解包目录）；绝对路径直接验存在性；
    - 文件不存在 / 任何异常一律 None，宿主据此回落，绝不抛错。
    """
    try:
        text = str(raw or "").strip().strip('"').strip("'")
        if not text:
            text = DEFAULT_MUSIC
        path = pathlib.Path(text)
        if path.is_absolute():
            return path if path.is_file() else None
        bases = [pathlib.Path(b) for b in (base_dirs or ()) if b is not None]
        if not bases:
            bases = [pathlib.Path()]
        for base in bases:
            candidate = base / path
            if candidate.is_file():
                return candidate
        return None
    except Exception:
        return None


def _load_qt():
    """QtMultimedia 惰性加载（可注入替身）；缺失即抛由 play 兜住。"""
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    return QMediaPlayer, QAudioOutput, QUrl


class MusicPlayer:
    """一首歌的播放器：play/stop/is_playing/on_finished，坏境全程静默。

    宿主用法（host._perform_single_click）：
        if self.music.play(path, volume):
            self._start_song_dance()          # 循环伴舞
            self.music.on_finished(self._on_song_finished)
    播放器与音频输出惰性创建并复用；qt_loader 参数是测试注入口。
    """

    def __init__(self, parent=None, qt_loader=None):
        self._parent = parent
        self._qt_loader = qt_loader or _load_qt
        self._player = None
        self._audio = None
        self._finished_cbs = []     # 一次性回调：触发或 stop 即清空
        self._duration_s = 0.0
        self._want_playing = False  # 已点播闩：play() 成功即 True，封启动空窗

    # ---------------- 播放控制 ----------------
    music_disabled = False   # 总开关：True = 全部长音频下线（音效不受影响）

    def play(self, path, volume: float = DEFAULT_MUSIC_VOLUME) -> bool:
        """整首播放本地音频文件；成功 True，任何失败 False 且不抛。

        文件不存在先挡掉（不碰 Qt）；多媒体后端缺失/构造失败一律 False。
        music_disabled=True 时一律 False（主人拍板：朋友的酒全量下线）。
        """
        if self.music_disabled:
            return False
        try:
            p = pathlib.Path(str(path))
        except Exception:
            return False
        if not str(path) or not p.is_file():
            return False
        try:
            QMediaPlayer, QAudioOutput, QUrl = self._qt_loader()
            if self._player is None:
                self._player = QMediaPlayer(self._parent)
                self._audio = QAudioOutput(self._parent)
                self._player.setAudioOutput(self._audio)
                self._player.mediaStatusChanged.connect(self._on_media_status)
                self._player.durationChanged.connect(self._on_duration)
            try:
                vol = max(0.0, min(1.0, float(volume)))
            except (TypeError, ValueError):
                vol = DEFAULT_MUSIC_VOLUME
            self._audio.setVolume(vol)
            self._player.setSource(QUrl.fromLocalFile(str(p.resolve())))
            self._player.play()
            # 闩上「已点播」：后端从 play() 到真正出声有异步空窗，这期间
            # isPlaying() 还是 False；不闩住的话，连点第二下会重播/重置。
            self._want_playing = True
            return True
        except Exception:
            self._teardown()
            return False

    def stop(self):
        """停歌：撤闩、清掉未触发的歌完回调，is_playing 归 False。"""
        self._want_playing = False
        self._finished_cbs = []
        try:
            if self._player is not None:
                self._player.stop()
        except Exception:
            pass

    # ---------------- 状态查询 ----------------
    def is_playing(self) -> bool:
        """歌正在播：已点播闩成立且媒体未到终态；坏境恒 False。

        语义 = 「这首歌被点过且还没唱完/没坏」，比裸 isPlaying() 更严：
        后端启动空窗（play() 后头几十毫秒 isPlaying 仍 False）与加载期
        都算在播，宿主的「播歌中点击一律忽略」才不会漏。
        """
        if not self._want_playing:
            return False
        try:
            if self._player is None:
                return False
            return status_token(self._player.mediaStatus()) \
                not in ("EndOfMedia", "InvalidMedia")
        except Exception:
            return False

    def duration_seconds(self) -> float:
        """已知的歌曲时长（秒）；媒体还没加载出来时是 0（未知）。"""
        return self._duration_s

    # ---------------- 歌完回调 ----------------
    def on_finished(self, callback) -> None:
        """登记歌完回调（同一回调去重）；EndOfMedia 触发一次即清空。"""
        if callable(callback) and callback not in self._finished_cbs:
            self._finished_cbs.append(callback)

    def _on_media_status(self, status):
        """Qt mediaStatusChanged 槽：EndOfMedia 撤闩并广播；坏源只撤闩。"""
        token = status_token(status)
        if token == _END_TOKEN:
            self._want_playing = False
            self._notify_finished()
        elif token == "InvalidMedia":
            self._want_playing = False

    def _notify_finished(self):
        """歌完广播：快照后立即清空（一次性），单个回调炸了不连坐。"""
        cbs, self._finished_cbs = self._finished_cbs, []
        for cb in cbs:
            try:
                cb()
            except Exception:
                pass

    def _on_duration(self, ms):
        try:
            self._duration_s = max(0.0, float(ms) / 1000.0)
        except (TypeError, ValueError):
            self._duration_s = 0.0

    def _teardown(self):
        """播放路径翻车后的清场：引用全撤、闩归位，下次 play 重新构造。"""
        self._player = None
        self._audio = None
        self._want_playing = False
