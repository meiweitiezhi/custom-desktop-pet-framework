"""全屏游戏风「走马灯结算画面」表现层。

GUI 只消费 settlement_core 输出的字符串行，不做任何业务计算：
打字机逐字吐稿（60ms/字符）-> 升级时字号闪两下 -> 点击/Esc 结束；
BGM 播放全部包在 try 里，缺文件 / 缺多媒体后端一律静默无声，绝不影响画面。
窗口开/关各发一条 Qt 信号（opened/closed），宿主用它同步本体表情演出。
"""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .animator_core import validate_rate

# 打字机节奏与闪烁参数
TYPE_INTERVAL_MS = 60
FLASH_TOGGLE_MS = 220
FLASH_BASE_PX = 26
FLASH_BIG_PX = 36

STYLE = ("QLabel{{color:#33ff66;background:transparent;"
         "font-family:'Consolas','Microsoft YaHei Mono',monospace;"
         "font-size:{}px;font-weight:bold;}}")


class SettlementWindow(QWidget):
    """半透明黑幕 + 绿色等宽字体的全屏结算画面；开/关对外广播信号，关窗即停 BGM。"""

    opened = Signal()   # 首次真正开演（showEvent 武装完成）时发出
    closed = Signal()   # 关窗收尾时发出，宿主借此恢复本体状态

    def __init__(self, lines, bgm_path=None, bgm_enabled=True, bgm_rate=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("今日回合结算")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)

        self._bgm_path = bgm_path
        self._bgm_enabled = bool(bgm_enabled)
        self._bgm_rate_raw = bgm_rate   # 原始配置值，播放时才洗成合法倍率
        self._player = None
        self._audio_out = None

        self._body = "\n".join(str(x) for x in (lines or ["……空的结算单"]))
        self._cursor = 0

        self.view = QLabel(self)
        self.view.setTextFormat(Qt.PlainText)
        self.view.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.view.setWordWrap(True)
        self.view.setStyleSheet(STYLE.format(FLASH_BASE_PX))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(80, 0, 80, 0)
        lay.addStretch(2)
        lay.addWidget(self.view)
        lay.addStretch(3)

        # 打字机：每 60ms 吐 1 个字符
        self._typer = QTimer(self)
        self._typer.setInterval(TYPE_INTERVAL_MS)
        self._typer.timeout.connect(self._type_one_char)

        # 升级高光：标题字号来回切两下
        self._flash_left = 0
        self._blinker = QTimer(self)
        self._blinker.setInterval(FLASH_TOGGLE_MS)
        self._blinker.timeout.connect(self._flash_once)

    # ---------------- 打字机与高光 ----------------
    def _type_one_char(self):
        self._cursor = min(len(self._body), self._cursor + 1)
        self.view.setText(self._body[:self._cursor])
        if self._cursor >= len(self._body):
            self._typer.stop()
            if "称号进化" in self._body:
                self._flash_left = 4     # 两下闪烁 = 四次切换，结束回到基准字号
                self._blinker.start()

    def _flash_once(self):
        big = self._flash_left % 2 == 0
        self.view.setStyleSheet(
            STYLE.format(FLASH_BIG_PX if big else FLASH_BASE_PX))
        self._flash_left -= 1
        if self._flash_left <= 0:
            self._blinker.stop()
            self.view.setStyleSheet(STYLE.format(FLASH_BASE_PX))

    # ---------------- 生命周期 ----------------
    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_armed", False):
            self._armed = True
            self.view.setText("")
            self._typer.start()
            self._start_bgm()
            self.opened.emit()

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 200))
        super().paintEvent(ev)

    def mousePressEvent(self, e):
        self.close()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter,
                       Qt.Key_Space):
            self.close()

    def closeEvent(self, e):
        self._typer.stop()
        self._blinker.stop()
        self._stop_bgm()
        self.closed.emit()

    # ---------------- BGM（静默降级原则）----------------
    def _start_bgm(self):
        """有本地 BGM 且配置允许就按变速倍率循环播放；任何失败都只是无声。

        QtMultimedia 的 import 也放进 try——裁剪版依赖缺失时不能崩应用。
        倍率经 animator_core.validate_rate 洗过（0.5~4.0，非法回落 1.0）；
        setPlaybackRate 单独包 try，后端不支持变速时就原速播。
        """
        if not self._bgm_path or not self._bgm_enabled:
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self._player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._audio_out.setVolume(0.55)
            self._player.setAudioOutput(self._audio_out)
            self._player.setSource(
                QUrl.fromLocalFile(str(Path(self._bgm_path).resolve())))
            try:
                rate = validate_rate(self._bgm_rate_raw) or 1.0
                self._player.setPlaybackRate(rate)
            except Exception:
                pass   # 不支持变速的后端：原速照播
            self._player.setLoops(QMediaPlayer.Loops.Infinite)
            self._player.play()
        except Exception as exc:
            print(f"[团子] BGM 放不了（缺后端或文件失效），静默继续：{exc}")
            self._player = None
            self._audio_out = None

    def _stop_bgm(self):
        try:
            if self._player is not None:
                self._player.stop()
        except Exception:
            pass
        self._player = None
        self._audio_out = None
