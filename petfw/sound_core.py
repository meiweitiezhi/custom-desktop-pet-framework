"""程序合成音效核心：运行期用标准库生成 PCM WAV 字节，零素材文件零依赖。

红线自检：
- 音频一律数学实时合成（wave+math+struct），仓库与 exe 里都不带任何音频素材；
- synthesize 是纯函数绝不落盘；播放用的临时 wav 由宿主写系统临时目录，
  属运行期产物，永不入库。

八种音效全部 ≤0.5 秒、峰值统一归一限幅 0.85，足够当桌宠的「口型拟声」。
"""
import io
import math
import struct
import wave

SAMPLE_RATE = 22050   # 反馈类短音效的甜点采样率：合成快、文件小、够清亮
PEAK_LIMIT = 0.85     # 全体样本峰值上限（相对满幅），杜绝爆音破声

# 八种音效词表；宿主触发与测试防漂移都以这里为准
SOUND_NAMES = ("pop", "boing", "ding", "chime", "wah", "tada", "kiss", "suck",
               "vroom")


def available_names() -> tuple:
    """当前支持程序合成的全部音效名。"""
    return SOUND_NAMES


# ---------------------------------------------------------- 波形小工具
def _fade_edges(samples: list, rate: int) -> list:
    """首尾各加约 4ms 余弦渐变，掐掉起止点切换造成的咔哒爆音。"""
    n = len(samples)
    if n < 8:
        return samples
    k = min(n // 2, max(2, int(0.004 * rate)))
    for i in range(k):
        g = 0.5 - 0.5 * math.cos(math.pi * (i + 1) / k)
        samples[i] *= g
        samples[n - 1 - i] *= g
    return samples


def _to_wav_bytes(samples: list, rate: int) -> bytes:
    """浮点样本 -> 归一限幅 -> 单声道 16bit PCM -> 完整合法 WAV 文件字节。"""
    peak = max((abs(s) for s in samples), default=0.0)
    gain = PEAK_LIMIT / peak if peak > 1e-9 else 0.0
    body = bytearray()
    pack = struct.Struct("<h").pack
    for s in samples:
        v = max(-PEAK_LIMIT, min(PEAK_LIMIT, s * gain))
        body += pack(int(v * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(body))
    return buf.getvalue()


# ---------------------------------------------------------- 七种发生器
# 约定：每个发生器输入采样率，返回 -1..1 附近的浮点样本列表（可超 1，
# 统一由 _to_wav_bytes 归一限幅）；相位走积分累加，保证扫频/滑音连续无跳变。

def _gen_pop(rate: int) -> list:
    """点击 pop：8ms 正弦扫频 600→1200Hz，指数衰减包络，短促一声「啵」。"""
    n = max(8, int(0.008 * rate))
    out = []
    ph = 0.0
    for i in range(n):
        t = i / rate
        f = 600.0 + 600.0 * i / (n - 1)
        ph += 2.0 * math.pi * f / rate
        out.append(math.exp(-t / 0.0028) * math.sin(ph))
    return _fade_edges(out, rate)


def _gen_boing(rate: int) -> list:
    """蹦跳 boing：150ms 三角波 300→90Hz 下滑 + 双重颤音，弹一下像果冻。"""
    n = max(8, int(0.15 * rate))
    out = []
    ph = 0.0
    for i in range(n):
        t = i / rate
        u = i / (n - 1)
        f = 300.0 - 210.0 * u + 14.0 * math.sin(2.0 * math.pi * 9.0 * t)
        ph += 2.0 * math.pi * f / rate
        jelly = 0.85 + 0.15 * math.sin(2.0 * math.pi * 9.0 * t)
        tri = 2.0 / math.pi * math.asin(math.sin(ph))
        out.append(jelly * (1.0 - 0.4 * u) * tri)
    return _fade_edges(out, rate)


def _gen_ding(rate: int) -> list:
    """喝水 ding：250ms G6(1568Hz) 正弦 + 双泛音铃感，指数余韵衰减。"""
    n = max(8, int(0.25 * rate))
    out = []
    f0 = 1568.0
    for i in range(n):
        t = i / rate
        x = 2.0 * math.pi * f0 * t
        bell = (math.sin(x) + 0.5 * math.sin(2.0 * x + 0.13)
                + 0.22 * math.sin(3.0 * x - 0.31))
        out.append(math.exp(-t / 0.08) * bell)
    return _fade_edges(out, rate)


def _ring_note(f0: float, ms: float, rate: int) -> list:
    """风铃单音：基音 + 少量二次谐波，12% 起振 18% 收尾的平滑窗。"""
    n = max(8, int(ms / 1000.0 * rate))
    out = []
    ph = 0.0
    for i in range(n):
        u = i / (n - 1)
        ph += 2.0 * math.pi * f0 / rate
        env = min(1.0, u / 0.12, (1.0 - u) / 0.18)
        out.append(env * (math.sin(ph) + 0.28 * math.sin(2.0 * ph)) / 1.28)
    return out


def _gen_chime(rate: int) -> list:
    """伸懒腰 chime：C6→E6 两音上行各 120ms，清脆风铃双响。"""
    c6, e6 = 1046.5, 1318.51
    return _fade_edges(_ring_note(c6, 120, rate) + _ring_note(e6, 120, rate),
                       rate)


def _gen_wah(rate: int) -> list:
    """报错 wah：400ms 锯齿波 500→200Hz 下滑带颤 + 抽泣式幅度调制，哭腔。"""
    n = max(8, int(0.40 * rate))
    out = []
    ph = 0.0
    for i in range(n):
        t = i / rate
        u = i / (n - 1)
        f = 500.0 - 300.0 * u + 26.0 * math.sin(2.0 * math.pi * 8.0 * t)
        ph += 2.0 * math.pi * f / rate
        saw = 2.0 * ((ph / (2.0 * math.pi)) % 1.0) - 1.0
        sob = 0.82 + 0.18 * math.sin(2.0 * math.pi * 5.5 * t + 0.4)
        env = min(1.0, u / 0.06, (1.0 - u) / 0.10)
        out.append(sob * env * saw)
    return _fade_edges(out, rate)


def _gen_tada(rate: int) -> list:
    """升级 tada：C6-E6-G6-C7 四连上行琶音各 70ms，软化的方波显得圆润喜庆。"""
    seq = (1046.5, 1318.51, 1567.98, 2093.0)
    seg = max(8, int(0.07 * rate))
    out = []
    for f0 in seq:
        ph = 0.0
        for i in range(seg):
            u = i / (seg - 1)
            ph += 2.0 * math.pi * f0 / rate
            env = min(1.0, u / 0.10, (1.0 - u) / 0.16)
            # 方波柔和化：tanh 圆角近似开关沿，亮而不刺
            out.append(env * math.tanh(2.6 * math.sin(ph)))
    return _fade_edges(out, rate)


def _gen_kiss(rate: int) -> list:
    """亲亲 kiss：80ms 内 880→1320Hz 相位连续圆滑上滑，被亲一口的「mua~」。"""
    n = max(8, int(0.08 * rate))
    out = []
    ph = 0.0
    for i in range(n):
        u = i / (n - 1)
        f = 880.0 + 440.0 * u
        ph += 2.0 * math.pi * f / rate
        env = math.sqrt(math.sin(math.pi * u))
        out.append(env * (math.sin(ph) + 0.25 * math.sin(2.0 * ph)) / 1.25)
    return _fade_edges(out, rate)


def _gen_suck(rate: int) -> list:
    """外星吸入 suck：300ms 相位连续上扫 600→1600Hz 正弦 + 高频微光 shimmer。

    上扫给出「被吸走」的升空感，shimmer 是叠在主音上的弱高频闪光，
    幅度随主音包络同涨同落，末端不炸耳。
    """
    n = max(8, int(0.30 * rate))
    out = []
    ph = 0.0
    for i in range(n):
        u = i / (n - 1)
        f = 600.0 + 1000.0 * u
        ph += 2.0 * math.pi * f / rate
        env = min(1.0, u / 0.10, (1.0 - u) / 0.18)
        shimmer = (0.20 * math.sin(2.0 * math.pi * 2400.0 * i / rate + 0.7)
                   * math.sin(math.pi * u))
        out.append(env * (math.sin(ph) + shimmer) / 1.2)
    return _fade_edges(out, rate)



def _gen_vroom(rate: int) -> list:
    """摩托突突 vroom：0.45s 低频引擎脉冲串。

    每 22ms 一个 90→70Hz 下滑锯齿脉冲（模拟单缸点火），脉冲间隙留
    气口，整体包络两头收中段饱——骑上小摩托的突突突。
    """
    n = max(8, int(0.45 * rate))
    out = []
    pulse_len = max(4, int(0.015 * rate))
    gap = max(2, int(0.007 * rate))
    period = pulse_len + gap
    ph = 0.0
    for i in range(n):
        pos = i % period
        if pos < pulse_len:
            u = pos / (pulse_len - 1)
            f = 90.0 - 20.0 * u
            ph += 2.0 * math.pi * f / rate
            env = math.sin(math.pi * u)
            out.append(env * math.tanh(2.2 * (2.0 * (u - 0.5))))
        else:
            out.append(0.0)
    return _fade_edges(out, rate)


_GENERATORS = {
    "pop": _gen_pop,
    "boing": _gen_boing,
    "ding": _gen_ding,
    "chime": _gen_chime,
    "wah": _gen_wah,
    "tada": _gen_tada,
    "kiss": _gen_kiss,
    "suck": _gen_suck,
    "vroom": _gen_vroom,
}


# -------------------------------------------------------------- 对外接口
def synthesize(name, sample_rate: int = SAMPLE_RATE) -> bytes:
    """按名合成一段完整合法的 WAV 文件字节；未知名等一切异常返回空 bytes。

    单声道 16bit PCM，峰值统一归一限幅到 PEAK_LIMIT，全名目时长 ≤0.5 秒。
    """
    try:
        gen = _GENERATORS.get(str(name))
        if gen is None:
            return b""
        rate = int(sample_rate)
        if rate <= 0:
            return b""
        return _to_wav_bytes(gen(rate), rate)
    except Exception:
        return b""  # 安全兜底：音效永远只无声，不许炸
