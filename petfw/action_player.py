"""动作点播播放器的纯逻辑核心：一拍走一步、双模式收摊、误差清账。

不依赖 Qt、不读文件——宿主（host.PetWindow）在 _tick 里只做一件事：
action.tick(dt) 拿当前应显示的帧下标；返回 None 即谢幕。
"""
from __future__ import annotations

# 浮点残渣容忍度：dt 累积到帧边界附近时按到达处理，绝不让它赖场
_EPS = 1e-9


def action_duration_seconds(spec: dict) -> float:
    """once 模式的单轮时长：len(frames) * frame_ms / 1000 + 尾部定格时长。

    尾部定格只认 spec 的 hold_tail_ms 字段（毫秒）；loop 模式与垃圾输入
    一律 0.0，调用方无需判空。
    """
    try:
        n = len(spec.get("frames") or ())
        frame_ms = float(spec.get("frame_ms") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0
    if n <= 0 or frame_ms <= 0:
        return 0.0
    return n * frame_ms / 1000.0 + _spec_hold_seconds(spec)


def _spec_hold_seconds(spec: dict) -> float:
    """读 spec 的 hold_tail_ms 字段（毫秒），垃圾值一律 0。"""
    try:
        return max(0.0, int(spec.get("hold_tail_ms") or 0) / 1000.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


class ActionPlayer:
    """一段动作的时间线播放器。

    用法：
        player.start(manifest条目, on_finish_state="idle")
        idx = player.tick(dt)   # 每渲染帧调用；None = 已谢幕
    属性：
        alive            是否正在表演（start 成功后为 True）
        on_finish_state  谢幕后的建议去向（宿主自行决定是否采纳）
    """

    def __init__(self):
        self.spec: dict | None = None
        self.play = "loop"
        self.pingpong = False
        self.frame_count = 0
        self.frame_s = 0.0
        self.total_s = 0.0
        self.hold_s = 0.0        # once 尾部定格时长（秒）；loop 恒 0
        self.on_finish_state = "idle"
        self.alive = False
        self.done = False
        self._cycles = 0     # 已完整跨过的帧边界数（永不回退）
        self._accum = 0.0    # 本帧内的残留时长（清账式记账）
        self._hold_left = 0.0    # 定格剩余时长；<=0 即谢幕

    def start(self, spec: dict, on_finish_state: str = "idle",
              hold_tail_ms: int = 0) -> None:
        """装填一段动作并干净重置计时：重复 start 等于中途换节目。

        hold_tail_ms：once 序列播完末帧后再定格的时长（毫秒）。显式传
        >0 时覆盖 spec；传 0/缺省则回落 spec 的 hold_tail_ms 字段。
        loop 模式无视定格（永续循环不存在「播完」）。
        """
        self.spec = spec if isinstance(spec, dict) else None
        mode = str((self.spec or {}).get("play") or "loop").strip().lower()
        # 向后兼容：老 manifest 条目没写 play 就按循环待机处理
        self.play = "once" if mode == "once" else "loop"
        # 乒乓只属于循环档：once+pingpong 仍播到尾即谢幕，不反向
        self.pingpong = self.play == "loop" \
            and bool((self.spec or {}).get("pingpong"))
        self.frame_count = len((self.spec or {}).get("frames") or [])
        try:
            frame_ms = float((self.spec or {}).get("frame_ms") or 0)
        except (TypeError, ValueError):
            frame_ms = 0.0
        valid = self.frame_count > 0 and frame_ms > 0
        self.frame_s = frame_ms / 1000.0 if valid else 0.0
        # 定格裁决：显式参数说了算，没给（或给了 0/负数/乱码）才听 spec 的
        try:
            explicit = int(hold_tail_ms or 0)
        except (TypeError, ValueError):
            explicit = 0
        if explicit <= 0:
            explicit = int(_spec_hold_seconds(self.spec) * 1000)
        self.hold_s = max(0.0, explicit / 1000.0) if self.play == "once" \
            else 0.0
        self.total_s = action_duration_seconds(self.spec) \
            if self.play == "once" else 0.0
        if self.play == "once" and explicit > 0:
            # spec 自带的定格已被显式参数覆盖时，时长按显式值重算
            self.total_s = self.frame_count * self.frame_s + self.hold_s
        self.on_finish_state = on_finish_state or "idle"
        self._cycles = 0
        self._accum = 0.0
        self._hold_left = self.hold_s
        self.alive = bool(valid)
        self.done = not self.alive

    def tick(self, dt: float) -> int | None:
        """推进 dt 秒，返回当前应显示的帧下标；未开始/已结束/垃圾输入均 None。

        once 播完末帧后若带尾部定格：定格期内一直返回末帧下标，时长耗尽
        才返回 None 谢幕——宿主画面因此毫无重播/重启感。
        """
        if not self.alive:
            return None
        try:
            step = max(0.0, float(dt))
        except (TypeError, ValueError):
            return None
        if self.frame_s <= 0:
            return None
        # 清账式累积：一次吞下多少个整帧就记多少笔账，再大的 dt 也不漂移
        self._accum += step
        steps = int((self._accum + _EPS) // self.frame_s)
        if steps > 0:
            self._accum -= steps * self.frame_s
            if self._accum < 0.0:
                self._accum = 0.0
            self._cycles += steps
        if self.play == "once" and self._cycles >= self.frame_count:
            # 帧段播完：先赖完尾部定格再谢幕
            if self._hold_left > _EPS:
                self._hold_left -= step
                if self._hold_left > _EPS:
                    return self.frame_count - 1    # 末帧定格
            self.alive = False
            self.done = True
            return None
        n = self.frame_count
        if self.pingpong and n > 1:
            # 乒乓往返：周期 2(n-1)，越过折返点按下标镜像取帧
            period = 2 * (n - 1)
            pos = self._cycles % period
            return pos if pos < n else period - pos
        return self._cycles % n
