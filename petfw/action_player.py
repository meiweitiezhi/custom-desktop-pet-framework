"""动作点播播放器的纯逻辑核心：显式三段拼接时间线（v4）。

不依赖 Qt、不读文件——宿主（host.PetWindow）在 _tick 里只做一件事：
action.tick(dt) 拿当前应显示的帧下标；返回 None 即谢幕。

回发呆逻辑是「记秒数 + if 判断」的直白形态（主人最高指令）：
    start 时把 spec 组装成显式段列表 self.segments：
        [("perform", 表演时长), ("hold", 定格时长), ("transition", 转场时长)]
    tick 每拍把秒表 self.elapsed_seconds += dt 往前拨，然后用 if 依次判断：
        秒数没过当前段终点   -> 留在本段（perform/transition 按 frame_ms
                                 推进帧下标；hold 恒亮末帧）；
        秒数过了当前段终点   -> 切进下一段；
        三段全走完           -> 返回 None 谢幕，宿主据此切回 return_to。

loop 模式没有三段概念：segments 恒空、永续循环（含乒乓），维持旧行为。

【v5 表演窗口】once 表演段可选两个新 manifest 字段（优先级
rounds > perform_seconds > 缺省一轮）：
    "rounds": 2          -> 表演段演满 2 轮（帧下标每轮取模从头再演）
    "perform_seconds": 5 -> 表演段持续 5 秒（帧下标在窗口内取模循环）
开了窗口的表演段里 pingpong 照常生效（窗口内连续往返不断轮）；乒乓
与 hold/transition/max_seconds 联动不变：max_seconds = 三段和 + 1 秒宽限。
不写新字段的旧状态一轮照旧（dance 等回归零变化）。

【旧实现（帧边界累积清账），注释保留供回滚对照】
    self._accum += dt; steps = int((self._accum + _EPS) // self.frame_s)
    self._cycles += steps              # 已跨过的帧边界数，永不回退
    once 播完（_cycles >= 帧数）进 _hold_left 倒计时，赖完才谢幕。
    转场帧当年直接追加在 frames 尾部，播完自然结束——隐式回发呆，已废弃。
"""
from __future__ import annotations

# 浮点残渣容忍度：秒数/帧数压线时按到达处理，绝不让它赖场
_EPS = 1e-9

# v4 段名：显式三段拼接时间线的三个站点（也是 self.segment 的取值）
SEG_PERFORM = "perform"
SEG_HOLD = "hold"
SEG_TRANSITION = "transition"


def _spec_hold_seconds(spec: dict) -> float:
    """读 spec 的定格时长（秒）：v4 的 hold_seconds 优先，旧 hold_tail_ms
    （毫秒）向后兼容兜底；垃圾值一律 0。"""
    if not isinstance(spec, dict):
        return 0.0
    try:
        hold = float(spec.get("hold_seconds") or 0)
    except (TypeError, ValueError):
        hold = 0.0
    if hold <= 0:
        try:
            hold = max(0.0, int(spec.get("hold_tail_ms") or 0) / 1000.0)
        except (TypeError, ValueError):
            hold = 0.0
    return hold


def _spec_rounds(spec: dict) -> int:
    """读 spec 的表演轮数（v5 新字段 rounds）：非正数/缺写/乱码一律 0。"""
    if not isinstance(spec, dict):
        return 0
    try:
        rounds = int(spec.get("rounds") or 0)
    except (TypeError, ValueError):
        return 0
    return rounds if rounds > 0 else 0


def _spec_perform_seconds(spec: dict) -> float:
    """读 spec 的表演窗口秒数（v5 新字段 perform_seconds）：非正数/乱码 0。"""
    if not isinstance(spec, dict):
        return 0.0
    try:
        seconds = float(spec.get("perform_seconds") or 0)
    except (TypeError, ValueError):
        return 0.0
    return seconds if seconds > 0 else 0.0


def action_duration_seconds(spec: dict) -> float:
    """once 模式的完整时长 = 表演段 + 定格段 + 转场段（全按秒计）。

    表演段按窗口口径（v5）：rounds 轮 > perform_seconds 秒 > 缺省一轮。
    loop 模式与垃圾输入一律 0.0，调用方无需判空。
    """
    if not isinstance(spec, dict):
        return 0.0
    try:
        n = len(spec.get("frames") or ())
        t = len(spec.get("transition_frames") or ())
        frame_ms = float(spec.get("frame_ms") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0
    if n <= 0 or frame_ms <= 0:
        return 0.0
    perform_s = n * frame_ms / 1000.0
    rounds = _spec_rounds(spec)
    if rounds > 0:
        perform_s = rounds * perform_s
    else:
        window_s = _spec_perform_seconds(spec)
        if window_s > 0:
            perform_s = window_s
    return perform_s + _spec_hold_seconds(spec) \
        + t * frame_ms / 1000.0


class ActionPlayer:
    """一段动作的时间线播放器（显式三段拼接：perform → hold → transition）。

    用法：
        player.start(manifest条目, on_finish_state="idle")
        idx = player.tick(dt)   # 每渲染帧调用；None = 已谢幕
    属性：
        alive            是否正在表演（start 成功后为 True）
        on_finish_state  谢幕后的建议去向（宿主自行决定是否采纳）
        segments         显式段列表 [(段名, 时长秒)]；loop 恒空
        elapsed_seconds  显式秒表（秒）：每 tick 拨一拍，只加不减
        segment          当前段名（perform/hold/transition），宿主据此选帧列表
    """

    def __init__(self):
        self.spec: dict | None = None
        self.play = "loop"
        self.pingpong = False
        self.frame_count = 0
        self.frame_s = 0.0
        self.hold_s = 0.0          # 定格段时长（秒）；loop 恒 0
        self.transition_count = 0  # 转场段帧数
        self.total_s = 0.0         # once 全时间线（三段之和）；loop 恒 0
        self.on_finish_state = "idle"
        self.alive = False
        self.done = False
        # —— v4 显式三段拼接时间线 ——
        self.segments = []            # [(段名, 时长秒)]；0 长段不装填
        self.elapsed_seconds = 0.0    # 显式秒表：只加不减
        self.segment = None           # 当前段名；未上场/谢幕后无意义
        self._seg_idx = 0             # 当前段下标
        self._segment_start = 0.0     # 当前段起点（秒）
        self._segment_end = 0.0       # 当前段终点（秒）
        # —— v5 表演窗口：rounds > perform_seconds > 缺省一轮 ——
        self.perform_rounds = 0        # 表演段轮数；0 = 没开窗口
        self.perform_seconds = 0.0     # 表演段窗口秒数；0 = 没开窗口
        self.perform_pingpong = False  # 窗口内乒乓往返（只属窗口表演）

    def start(self, spec: dict, on_finish_state: str = "idle",
              hold_tail_ms: int = 0) -> None:
        """装填一段动作并干净重置秒表：重复 start 等于中途换节目。

        hold_tail_ms：定格段时长（毫秒）。显式传 >0 时覆盖 spec 的
        hold_seconds/hold_tail_ms；传 0/缺省则回落 spec 字段。
        loop 模式没有定格与转场概念（永续循环不存在「播完」）。
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
        self.transition_count = len(
            (self.spec or {}).get("transition_frames") or [])
        # 定格时长裁决：显式参数说了算，没给（或给了 0/负数/乱码）才听 spec 的
        try:
            explicit_ms = int(hold_tail_ms or 0)
        except (TypeError, ValueError):
            explicit_ms = 0
        if self.play == "once":
            self.hold_s = max(0.0, explicit_ms / 1000.0) if explicit_ms > 0 \
                else _spec_hold_seconds(self.spec)
        else:
            self.hold_s = 0.0
        # —— 组装显式段列表：0 长段直接跳过，时间线上不留空站 ——
        self.segments = []
        # 表演窗口裁决（v5）：rounds > perform_seconds > 缺省一轮；
        # 只有真开了窗口的表演段才在 once 里认乒乓（窗口内连续往返）
        self.perform_rounds = 0
        self.perform_seconds = 0.0
        self.perform_pingpong = False
        if self.play == "once" and valid:
            rounds = _spec_rounds(self.spec)
            if rounds > 0:
                self.perform_rounds = rounds
            else:
                self.perform_seconds = _spec_perform_seconds(self.spec)
            self.perform_pingpong = \
                bool((self.spec or {}).get("pingpong")) \
                and (self.perform_rounds > 0 or self.perform_seconds > 0)
            perform_s = self.frame_count * self.frame_s
            if self.perform_rounds > 0:
                perform_s = self.perform_rounds * perform_s
            elif self.perform_seconds > 0:
                perform_s = self.perform_seconds
            self.segments.append((SEG_PERFORM, perform_s))
            if self.hold_s > 0:
                self.segments.append((SEG_HOLD, self.hold_s))
            if self.transition_count > 0:
                self.segments.append(
                    (SEG_TRANSITION, self.transition_count * self.frame_s))
        self.total_s = sum(dur for _, dur in self.segments)
        self.on_finish_state = on_finish_state or "idle"
        # 秒表归零，从第一段第一帧开始
        self.elapsed_seconds = 0.0
        self._seg_idx = 0
        self._segment_start = 0.0
        self._segment_end = self.segments[0][1] if self.segments else 0.0
        self.segment = self.segments[0][0] if self.segments else None
        self.alive = bool(valid)
        self.done = not self.alive

    def tick(self, dt: float) -> int | None:
        """推进 dt 秒，返回当前应显示的帧下标；未开始/已结束/垃圾输入均 None。

        帧下标按段归属：perform/hold 段返回 frames 的下标（hold 恒为末帧
        下标）；transition 段返回 transition_frames 的下标——宿主按
        self.segment 选对应帧列表上屏。
        """
        if not self.alive:
            return None
        try:
            step = max(0.0, float(dt))
        except (TypeError, ValueError):
            return None
        # —— 显式记秒数：秒表只加不减 ——
        self.elapsed_seconds += step
        if self.play != "once":
            return self._loop_frame()
        # —— 依次 if 判断：秒数过了当前段终点就切进下一段 ——
        while self._seg_idx < len(self.segments) \
                and self.elapsed_seconds >= self._segment_end - _EPS:
            self._seg_idx += 1
            if self._seg_idx < len(self.segments):
                self._segment_start = self._segment_end
                self._segment_end = self._segment_start \
                    + self.segments[self._seg_idx][1]
        if self._seg_idx >= len(self.segments):
            # 三段全走完：谢幕（宿主随即切 return_to）
            self.alive = False
            self.done = True
            return None
        name, _dur = self.segments[self._seg_idx]
        self.segment = name
        if name == SEG_HOLD:
            return self.frame_count - 1     # 定格段：恒亮末帧
        local = self.elapsed_seconds - self._segment_start
        if name == SEG_PERFORM:
            # 表演段：按 frame_ms 推进 frames 下标
            if self.perform_rounds > 0 or self.perform_seconds > 0:
                # 窗口表演：帧下标取模续杯，乒乓在窗口内连续往返
                return self._cycle_index(
                    int((local + _EPS) / self.frame_s),
                    self.perform_pingpong)
            return min(int((local + _EPS) / self.frame_s),
                       self.frame_count - 1)
        # 转场段：按 frame_ms 推进 transition_frames 下标
        return min(int((local + _EPS) / self.frame_s),
                   self.transition_count - 1)

    def _cycle_index(self, cycles: int, pingpong: bool) -> int:
        """帧边界计数换帧下标：乒乓按 2(n-1) 周期镜像往返，否则取模循环。"""
        n = self.frame_count
        if pingpong and n > 1:
            period = 2 * (n - 1)
            pos = cycles % period
            return pos if pos < n else period - pos
        return cycles % n

    def _loop_frame(self) -> int | None:
        """loop 档：维持旧循环行为（含乒乓），秒表换算成帧边界计数。"""
        if self.frame_s <= 0:
            return None
        self.segment = SEG_PERFORM
        cycles = int((self.elapsed_seconds + _EPS) / self.frame_s)
        return self._cycle_index(cycles, self.pingpong)
