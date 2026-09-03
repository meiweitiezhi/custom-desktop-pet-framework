"""运行期换装转场：谢幕目标非发呆时，压扁回弹尾段实时缩放目标立绘。

烘焙侧（prep_assets.bake_squash_return）把转场终点烘死成 idle——常驻
睡觉的宠物点一下惊讶，会先「弹回发呆」再硬跳回睡觉，途中多一次发呆
绕路（主人拍板 2026-09 修掉）。宿主 play_action 时若谢幕目标不是
idle，就按本模块复刻的三幕数学实时缩放目标状态的立绘图，替换换装点
之后的尾段：换装点之前的蓄力压扁帧照用烘焙产物（与目标无关）。

三幕结构与 bake_squash_return 逐帧同源（tests/test_transition_swap.py
锁两边常量一致，防漂移）：
1. 前 40% 帧把表演末姿态压到 (sy 0.78, sx 1.18)——烘焙帧，运行期不动；
2. 恰在最大压扁帧换装到目标立绘的同比例压扁帧（两图同压 78% 轮廓
   差异最小，切换无感、零比例错位）；
3. 其余帧 ease_out_back 式从 0.78 经 1.12 过冲回弹落定为目标原图；
   竖向过冲按目标头顶透明余量封顶（满画布绝不被裁头）。
纯数学无 Qt：宿主负责把 (sx, sy) 变成 QPixmap.transformed。
"""
from __future__ import annotations

# —— 与 prep_assets.SQUASH_* 同源同值（tests 有防漂移锁），别单边改 ——
SY_MIN = 0.78        # 第一幕终点：压到 78%（两图轮廓差异最小处）
SX_MAX = 1.18        # 压扁同时横向鼓出，保体积
OVERSHOOT = 1.12     # 第二幕过冲顶点
ACT1_SHARE = 0.4     # 第一幕帧数占比（其余归回弹幕）


def ease_out_back_peak(peak: float) -> float:
    """反解 ease_out_back 的过冲常数 s，使曲线峰值恰为 peak（二分，确定）。

    标准 ease_out_back: f(u)=1+(s+1)(u-1)^3+s(u-1)^2，其峰值满足
    4s^3 = 27(peak-1)(s+1)^2；s>=0 区间单调，64 轮二分收敛。
    """
    lo, hi = 0.0, 64.0
    for _ in range(64):
        mid = (lo + hi) / 2.0
        if 4.0 * mid ** 3 - 27.0 * (peak - 1.0) * (mid + 1.0) ** 2 < 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _back_ease(u: float, s: float) -> float:
    """ease_out_back：0→1 且中途经 1+s 比例的过冲再回落（皮筋回弹手感）。"""
    return 1.0 + (s + 1.0) * (u - 1.0) ** 3 + s * (u - 1.0) ** 2


def swap_index(total_frames: int) -> int:
    """换装点下标：与烘焙侧第一幕帧数同公式 max(2, round(n*0.4))。"""
    return max(2, int(round(max(1, int(total_frames)) * ACT1_SHARE)))


def runtime_pose(k: int, total_frames: int, headroom: float) \
        -> "tuple[float, float] | None":
    """转场第 k 帧的运行期姿态（sx, sy）；换装点之前返回 None（用烘焙帧）。

    k == 换装点：恰在最大压扁 (SX_MAX, SY_MIN)，与烘焙第一幕末帧姿态
    连续；k 之后按 ease_out_back 回弹，末帧落定为 (1.0, 1.0)。竖向峰值
    = min(OVERSHOOT, 1.0+headroom)，headroom 是目标立绘顶部的透明余量
    占比（0~1，宿主从 alpha 通道量出），横向鼓出始终按完整峰值回弹。
    """
    n = max(1, int(total_frames))
    swap = swap_index(n)
    if k < swap or k >= n:
        return None
    if k == swap:
        return (SX_MAX, SY_MIN)
    # 竖向余量自适应：B 顶上有余量才放行完整 1.12 过冲；满画布紧裁剪的
    # 素材把竖向过冲封顶到 1.0（绝不裁头），回弹能量由横向细拉表达
    peak_sy = min(OVERSHOOT, 1.0 + max(0.0, headroom))
    s_sx = ease_out_back_peak((OVERSHOOT - SY_MIN) / (1.0 - SY_MIN))
    s_sy = ease_out_back_peak((peak_sy - SY_MIN) / (1.0 - SY_MIN))
    u = (k - swap) / max(1, n - swap - 1)
    return (SX_MAX + (1.0 - SX_MAX) * _back_ease(u, s_sx),
            SY_MIN + (1.0 - SY_MIN) * _back_ease(u, s_sy))
