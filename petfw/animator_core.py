"""逐帧动画引擎的纯逻辑核心：抽帧、双档节奏、BGM 变速校验。

不依赖 Qt、不读文件——GIF 可能几百帧，GUI 拿到的永远是抽稀后的少量帧与
一个换帧间隔。表现层（host._tick / prep_assets）只消费这里的结论。
"""
from __future__ import annotations

# 单帧动画上限：GIF 再长也只取这些帧（内存友好 + 换帧开销可控）
DEFAULT_CAP = 6
# BGM 变速的合法区间（config.ini 的 bgm_rate 落在界外一律视为没配）
MIN_RATE = 0.5
MAX_RATE = 4.0
# GIF 帧时长缺失时的兜底节奏（毫秒）
DEFAULT_FRAME_MS = 120


def sample_frames(total_frames: int, cap: int = DEFAULT_CAP) -> list[int]:
    """从 total_frames 帧里均匀抽出 <=cap 个下标。

    - total <= cap：原样全量（list(range(total))），一帧不丢；
    - total >  cap：等距抽稀，必含首帧、必含尾帧、严格单调递增。
    非法输入（<=0 帧 / cap<=0）返回空列表，绝不抛错。
    """
    if total_frames is None or total_frames <= 0 or cap is None or cap <= 0:
        return []
    total, cap = int(total_frames), int(cap)
    if total <= cap:
        return list(range(total))
    picks = sorted({round(i * (total - 1) / (cap - 1)) for i in range(cap)})
    return [int(p) for p in picks]


def schedule(frames_count: int, base_ms: int, celebrate: bool) -> int:
    """双档节奏：返回换帧间隔（毫秒）。

    - celebrate=False 平时慢速卖萌：interval = base_ms * 2；
    - celebrate=True  结算/蹦跶全速狂欢：interval = base_ms。
    frames_count 目前仅用于完整性校验；没帧或 base_ms 非法时返回 0，
    表示"别动"，调用方跳过换帧即可。
    """
    try:
        n, base = int(frames_count), int(base_ms)
    except (TypeError, ValueError):
        return 0
    if n <= 0 or base <= 0:
        return 0
    return base if celebrate else base * 2


def next_index(i: int, n: int) -> int:
    """帧下标循环步进：i+1 后对 n 取模；n 为 0/负数时原地不动返回 0。"""
    try:
        i, n = int(i), int(n)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    return (i + 1) % n


def validate_rate(rate) -> float | None:
    """把配置里的变速倍率洗成合法 float；限 MIN_RATE~MAX_RATE，越界返 None。

    接受数字或数字字符串（ini 读出来全是字符串）；None/空串/乱码/bool 一律
    None，调用方 `validate_rate(x) or 1.0` 即可无缝降级为原速。
    """
    if isinstance(rate, bool):
        return None
    try:
        value = float(str(rate).strip())
    except (TypeError, ValueError):
        return None
    if MIN_RATE <= value <= MAX_RATE:
        return value
    return None


class FrameClock:
    """把「当前帧下标」和「双档节拍」打包成一个小状态机。

    GUI 只需要两步：interval_ms(n, celebrate) 决定多久换一次，
    advance(n) 在到点时拿下一个帧号。
    """

    def __init__(self, base_ms: int, start: int = 0):
        self.base_ms = max(1, int(base_ms))
        self.index = max(0, int(start))

    def interval_ms(self, frames_count: int, celebrate: bool) -> int:
        return schedule(frames_count, self.base_ms, celebrate)

    def advance(self, frames_count: int) -> int:
        self.index = next_index(self.index, frames_count)
        return self.index

    def reset(self, start: int = 0) -> None:
        self.index = max(0, int(start))
