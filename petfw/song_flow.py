"""点歌流程的纯逻辑决策：播歌忽略条款 + 伴舞循环规格。

无 Qt、无时钟、绝不抛错——宿主（host.PetWindow）在点击路径上只做搬运：
所有「这一下点不点、舞怎么跳」的裁决都收敛在本文件，方便无头测试。
"""
from __future__ import annotations


def should_ignore_click(music_playing) -> bool:
    """播歌忽略条款：歌播着的时候，单击/双击一律忽略。

    True = 点了也当没点：不重播、不重置、不触发别的演出。
    真值宽松处理（1/非空对象都算在播），宁可多忽略也不误触发。
    """
    return bool(music_playing)


def _file_name(item) -> str:
    """帧条目的文件名归一化：反斜杠转正斜杠、取末段、小写比较。

    帧条目既可能是路径字符串（manifest 原始条目），也可能是已加载的
    QPixmap 等对象（宿主 states 条目）——后者 str() 出的对象描述串
    天然不会与任何转场文件名撞车，等于原样保留。
    """
    text = str(item).replace("\\", "/").strip().lower()
    return text.rsplit("/", 1)[-1]


def dance_loop_spec(state_entry, loop_seconds: float, frame_ms: int) -> dict:
    """伴舞循环规格：给定 dance 条目与目标循环秒数（=歌长），
    返回 ActionPlayer 需要的规格字典。

    - play="loop"：循环档永续循环（含乒乓），歌完由宿主回调收舞；
    - frames 剔除转场尾：凡是与 transition_frames 同名（归一化后）的
      文件都不进循环——压扁转场只属于「跳完回发呆」的谢幕段，循环
      伴舞时绝不能一圈一压扁；
    - loop_seconds / frame_ms 原样带上（秒表元数据 + 换帧节拍）；
    - 条目缺失/乱码一律安静退化：frames=[]、loop_seconds=0、frame_ms=0。
    """
    entry = state_entry if isinstance(state_entry, dict) else {}
    try:
        transition = entry.get("transition_frames") or ()
        trans_names = {_file_name(t) for t in transition}
        frames = [f for f in (entry.get("frames") or ())
                  if _file_name(f) not in trans_names]
    except Exception:
        frames = []
    try:
        seconds = max(0.0, float(loop_seconds))
    except (TypeError, ValueError):
        seconds = 0.0
    try:
        fms = max(0, int(frame_ms))
    except (TypeError, ValueError):
        fms = 0
    return {
        "play": "loop",
        "frames": frames,
        "loop_seconds": seconds,
        "frame_ms": fms,
    }
