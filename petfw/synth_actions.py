"""外星吸入动作的纯逻辑合成：角色沿光束升空缩小淡出 + 光束逐帧闪烁。

无 Qt、确定性（固定随机种子）：输入 PIL 底图，输出等尺寸 RGBA 帧序列。
前段 frames 帧：底图中心线性上移至画面顶部 10%、等比缩放 1.0→0.25、
alpha 255→70，同时自画面底部立起黄色光束梯形（透明度 60~200 逐帧闪烁）；
后段 hover 帧：角色已被吸走，只剩闪烁的光束——喜剧留白。
"""
import random

from PIL import Image, ImageDraw

# 光束黄：明亮暖黄，与任意角色色相都拉开距离
BEAM_RGB = (255, 230, 80)
# 确定性种子：同一底图每次烘焙逐字节一致（tests 有防漂移断言）
SEED = 0x5EED

SCALE_START, SCALE_END = 1.0, 0.25     # 等比缩放区间
ALPHA_START, ALPHA_END = 255, 70       # 末帧残影仍隐约可见
TOP_FRACTION = 0.10                    # 质心终点：画面顶部 10% 高度处
BEAM_ALPHA_LO, BEAM_ALPHA_HI = 60, 200  # 光束闪烁透明度区间


def _beam_trapezoid(size: tuple) -> tuple:
    """光束梯形的几何：顶窄底宽，从画面顶一直立到底边。"""
    w, h = size
    cx = w / 2.0
    top_half = w * 0.10
    bottom_half = w * 0.38
    return (cx - top_half, 0.0, cx + top_half,
            cx - bottom_half, h, cx + bottom_half)


def _draw_beam(canvas: Image.Image, alpha: int) -> None:
    """在画布上叠一段半透明黄色光束梯形。"""
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    x0, y0, x1, x2, y2, x3 = _beam_trapezoid(canvas.size)
    d.polygon([(x0, y0), (x1, y0), (x2, y2), (x3, y2)],
              fill=BEAM_RGB + (max(0, min(255, int(alpha))),))
    canvas.alpha_composite(overlay)


def build_suck_frames(base: Image.Image, frames: int = 26,
                      hover: int = 13) -> list:
    """烘焙「外星吸入」帧序列：frames 帧升空 + hover 帧空场悬停。

    约定：
    - 全部输出 RGBA、与 base 同尺寸；同输入两次调用逐字节一致（SEED 固定）；
    - 前段角色质心 y 单调不增（只升不降），末段完全无角色像素；
    - 每帧都有黄色光束像素（梯形 + 逐帧随机透明度闪烁）。
    """
    base = base.convert("RGBA")
    size = base.size
    rng = random.Random(SEED)
    out = []
    total = max(0, int(frames)) + max(0, int(hover))
    for i in range(total):
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        if i < frames and frames > 1:
            u = i / (frames - 1)
            scale = SCALE_START + (SCALE_END - SCALE_START) * u
            alpha = ALPHA_START + (ALPHA_END - ALPHA_START) * u
            new_size = (max(1, int(size[0] * scale)),
                        max(1, int(size[1] * scale)))
            sprite = base.resize(new_size, Image.LANCZOS)
            r, g, b, a = sprite.split()
            a = a.point(lambda v, k=alpha: (v * k) // 255)
            sprite = Image.merge("RGBA", (r, g, b, a))
            # 中心点：从画面中央线性上移到顶部 10% 高度处
            cy = size[1] * 0.5 + (size[1] * TOP_FRACTION - size[1] * 0.5) * u
            cx = size[0] / 2.0
            x = max(0, int(round(cx - new_size[0] / 2.0)))
            y = max(0, int(round(cy - new_size[1] / 2.0)))  # 越过顶边=被吸走
            canvas.alpha_composite(sprite, dest=(x, y))
        _draw_beam(canvas, rng.randint(BEAM_ALPHA_LO, BEAM_ALPHA_HI))
        out.append(canvas)
    return out
