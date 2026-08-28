"""点击管线的纯逻辑核心：280ms 双击判定、结算忽略条款、专属音效裁决。

无 Qt、时钟可注入：宿主只做搬运（QTimer 单发 + 事件转调），所有判定都在
这里完成，因此点击时序可以做无头测试——不做真 QTimer 测试。
"""
from __future__ import annotations

import pathlib
import time

# 双击判定窗口：第一击落下后等这么久，没等到第二击就算单击
DOUBLE_CLICK_MS = 280


def resolve_click(clicks: int) -> str:
    """按窗口期内收到的点击数裁决演出词：

    - 1 击（窗口到期仍只此一击）  -> "single"  单击专属演出
    - 2 击及以上（窗口内连点）    -> "double"  双击点歌开跳演出
      （五态精简前是「双击吸入演出」，alien_suck 已入禁用区）
    - 0 击（还没点 / 没挂着窗口）  -> "pending" 什么都不做
    """
    try:
        n = int(clicks)
    except (TypeError, ValueError):
        return "pending"
    if n >= 2:
        return "double"
    if n == 1:
        return "single"
    return "pending"


def should_perform(settlement_open: bool) -> bool:
    """节假日不生效条款：结算画面开着时，任何点击演出都不放。"""
    return not bool(settlement_open)


def resolve_click_sfx(raw, base_dir=None) -> str | None:
    """[sound] click_sfx 配置裁决：填了且文件存在才用专属音效。

    相对路径相对 base_dir（仓库根 / exe 目录）解析；空值、文件不存在
    一律返回 None，宿主据此回落内置 pop。绝不抛错。
    """
    try:
        text = str(raw or "").strip().strip('"').strip("'")
        if not text:
            return None
        path = pathlib.Path(text)
        if not path.is_absolute() and base_dir is not None:
            path = pathlib.Path(base_dir) / text
        return str(path) if path.is_file() else None
    except Exception:
        return None


class ClickResolver:
    """280ms 单双击判定的状态机（时钟可注入，方便无头测试）。

    宿主用法：
      左键 release（非拖拽）-> press()；返回 "pending" 就起一个 280ms 单发
      QTimer，超时回调里调 timeout()，返回 "single" 就放单击演出；
      mouseDoubleClickEvent 里先 cancel() 再放双击演出。
    """

    def __init__(self, now=time.monotonic, window_ms: int = DOUBLE_CLICK_MS):
        self._now = now
        self._window_s = max(0.0, window_ms / 1000.0)
        self._first_at: float | None = None   # 第一击落下的时刻；None=未挂起

    def armed(self) -> bool:
        """是否挂着一条「等第二击」的窗口。"""
        return self._first_at is not None

    def press(self) -> str:
        """左键一击落下：首击开窗挂起，窗内二击成交，出窗二击重开新窗。"""
        now = self._now()
        if self._first_at is None:
            self._first_at = now
            return "pending"
        if now - self._first_at <= self._window_s:
            self._first_at = None
            return "double"
        # 出窗的第二击其实是新一轮的第一击
        self._first_at = now
        return "pending"

    def timeout(self) -> str:
        """窗口到点：挂起中的判单击，没挂着（重复超时/已取消）不算数。"""
        if self._first_at is None:
            return "pending"
        self._first_at = None
        return "single"

    def cancel(self) -> None:
        """双击路径：取消挂起的窗口，让随后到来的 timeout 落空。"""
        self._first_at = None
