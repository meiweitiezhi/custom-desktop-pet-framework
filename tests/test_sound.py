"""程序合成音效核心测试：全程无 GUI、无网络、零素材文件。

覆盖 petfw/sound_core.py 的纯逻辑契约：
- 七种音效输出完整合法 WAV 字节（RIFF 头 + wave 模块可解析）
- 未知名安全兜底返回空 bytes；总时长一律 ≤0.5s；峰值限幅 ≤0.85
- 同参数两次生成字节完全一致（确定性），且合成过程不落任何盘
"""
import io
import pathlib
import sys
import unittest
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from petfw.sound_core import available_names, synthesize  # noqa: E402

# 规格表：每种音效的目标时长（秒）；全部音效必须在 0.5s 内收尾
EXPECTED_SECONDS = {
    "pop": 0.008,
    "boing": 0.15,
    "ding": 0.25,
    "chime": 0.24,   # 两音各 120ms
    "wah": 0.40,
    "tada": 0.28,    # 四连琶音各 70ms
    "kiss": 0.08,
    "suck": 0.30,    # 外星吸入：600→1600Hz 上扫 + 微光 shimmer
}


def _wave_info(data: bytes):
    """用标准库把 WAV 字节解成 (nframes, framerate, samples)，非法即抛错。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        nframes = w.getnframes()
        rate = w.getframerate()
        raw = w.readframes(nframes)
        return nframes, rate, w.getsampwidth(), w.getnchannels(), raw


class TestSoundCore(unittest.TestCase):

    # ---------------------------------------------------------- 词表与兜底
    def test_available_names_matches_registry(self):
        names = available_names()
        self.assertIsInstance(names, tuple)
        self.assertEqual(set(names),
                         {"pop", "boing", "ding", "chime", "wah", "tada",
                          "kiss", "suck", "vroom"})

    def test_suck_is_a_rising_sweep(self):
        # 吸入感来自上扫：前半段主频必须低于后半段主频（过零间隔变短）
        from petfw.sound_core import _gen_suck
        rate = 22050
        samples = _gen_suck(rate)
        first = samples[:len(samples) // 4]
        last = samples[-len(samples) // 4:]
        zero_a = sum(1 for a, b in zip(first, first[1:])
                     if a <= 0 < b or b <= 0 < a)
        zero_b = sum(1 for a, b in zip(last, last[1:])
                     if a <= 0 < b or b <= 0 < a)
        self.assertGreater(zero_b * 2, zero_a, "后段过零数必须显著更多（上扫）")

    def test_unknown_name_returns_empty_bytes(self):
        for bad in ("", "boom", "POP", "ding!", None):
            self.assertEqual(synthesize(bad), b"", repr(bad))

    def test_no_files_written_by_synthesize(self):
        # 红线：synthesize 是纯函数，绝不写任何文件（wav 只在播放时落临时目录）
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        before = {p.name for p in tmp.iterdir()}
        synthesize("ding")
        after = {p.name for p in tmp.iterdir()}
        self.assertEqual(before, after)

    # -------------------------------------------------------- WAV 合法性
    def test_all_names_produce_valid_riff_wave(self):
        for name in available_names():
            data = synthesize(name)
            self.assertGreater(len(data), 44, f"{name} 连文件头都装不下")
            self.assertEqual(data[:4], b"RIFF", name)
            self.assertIn(b"WAVE", data[:16], name)

    def test_all_names_parse_as_pcm16_mono(self):
        for name in available_names():
            nframes, _rate, sampwidth, nch, raw = _wave_info(synthesize(name))
            self.assertEqual((sampwidth, nch), (2, 1), name)
            self.assertGreater(nframes, 0, name)
            self.assertEqual(len(raw), nframes * 2, name)

    def test_sample_rate_parameter_is_respected(self):
        _, rate, _, _, _ = _wave_info(synthesize("ding", sample_rate=16000))
        self.assertEqual(rate, 16000)
        _, rate, _, _, _ = _wave_info(synthesize("wah", sample_rate=44100))
        self.assertEqual(rate, 44100)

    # ------------------------------------------------- 时长 / 峰值 / 确定性
    def test_durations_match_spec_and_under_half_second(self):
        for name in available_names():
            nframes, rate, _, _, _ = _wave_info(synthesize(name))
            dur = nframes / rate
            self.assertLessEqual(dur, 0.5, f"{name} 超过半秒")
            expect = EXPECTED_SECONDS[name]
            self.assertAlmostEqual(dur, expect, delta=expect * 0.5 + 1e-3,
                                   msg=f"{name} 时长漂移")

    def test_peak_limited_to_avoid_clipping(self):
        limit = int(0.85 * 32767) + 8   # 限幅值允许 ±1 LSB 的舍入余量
        for name in available_names():
            _, _, _, _, raw = _wave_info(synthesize(name))
            peak = max(abs(int.from_bytes(raw[i:i + 2], "little", signed=True))
                       for i in range(0, len(raw), 2))
            self.assertLessEqual(peak, limit, name)
            self.assertGreater(peak, 3000, f"{name} 几乎听不见")

    def test_deterministic_bytes(self):
        for name in available_names():
            first = synthesize(name)
            second = synthesize(name)
            self.assertEqual(first, second, f"{name} 两次生成不一致")

    def test_distinct_sounds_differ_in_bytes(self):
        blobs = [synthesize(n) for n in available_names()]
        for i in range(len(blobs)):
            for j in range(i + 1, len(blobs)):
                self.assertNotEqual(blobs[i], blobs[j],
                                    "两种音效居然是同一份字节")


if __name__ == "__main__":
    unittest.main()
