"""剪纸动画生成器：让单帧立绘按配方做出表情演出，产出透明底帧序列。

纯逻辑、无 Qt、无文件读取——底图由调用方传入（tests 用程序合成替身，
tools/local/gifgen.py 读本地素材）。同 seed 必出逐字节相同的帧序列，
方便测试锁定与日后复现样片。

帧数公式（tests 锁定）：内部时间线固定 TIMELINE_FPS=30fps，一个「拍」按
配方 fps 定节奏，每个 op 的 beats 展开 frames = round(beats × 30 / fps)
且至少 1 帧；总帧数 = 各 op 展开帧数之和。首尾两帧强制回贴底图原姿态。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageDraw

# 内部合成时间线固定 30fps（GIF 流畅上限附近），拍节奏由配方 fps 控制
TIMELINE_FPS = 30
# 配方缺省 fps 与非法 fps 的兜底值
DEFAULT_FPS = 15
# 输出统一缩放：高度不超过该值（宽随纵横比）
TARGET_HEIGHT = 256
# 四周留白：容纳弹跳顶空、抖动位移与粒子飘出的画布余量（像素）
MARGIN = 72

# 合法 op 名单：Recipe 校验与 --overrides JSON 共用这份白名单
KNOWN_OPS = (
    "hold", "shake", "squash", "lean_back", "bounce", "wiggle",
    "spin", "particles", "pulse", "blink", "shake_head_blue",
    "sweep", "stretch",
)


# ---------------------------------------------------------------------------
# 缓动函数
# ---------------------------------------------------------------------------

def linear(t: float) -> float:
    """线性：f(0)=0，f(1)=1，严格单调。"""
    return float(t)


def ease_in_out(t: float) -> float:
    """先缓后急再缓（三次曲线），f(0)=0，f(1)=1，单调且前后对称。"""
    t = min(max(float(t), 0.0), 1.0)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - pow(-2.0 * t + 2.0, 3) / 2.0


def ease_out_back(t: float) -> float:
    """带回弹的快出缓停：中段越过 1 再回落，f(0)=0，f(1)=1。"""
    t = min(max(float(t), 0.0), 1.0)
    c1, c3 = 1.70158, 2.70158
    y = 1.0 + c3 * pow(t - 1.0, 3) + c1 * pow(t - 1.0, 2)
    # 抹掉端点处的浮点噪声，保证 f(0)/f(1) 恰为精确值
    if abs(y) < 1e-9:
        return 0.0
    if abs(y - 1.0) < 1e-9:
        return 1.0
    return y


def beats_to_frames(beats: int, fps: int = DEFAULT_FPS) -> int:
    """一个 op 的拍数在内部 30fps 时间线上展开成多少帧。

    frames = round(beats × 30 / fps)，至少 1 帧（有拍就得动）；
    拍数为 0 返回 0 帧纯效果位；fps 非法回落 DEFAULT_FPS。
    """
    try:
        b = int(beats)
    except (TypeError, ValueError):
        b = 0
    try:
        f = int(fps)
    except (TypeError, ValueError):
        f = DEFAULT_FPS
    if f <= 0:
        f = DEFAULT_FPS
    if b <= 0:
        return 0
    return max(1, int(round(b * TIMELINE_FPS / f)))


# ---------------------------------------------------------------------------
# 符号绘制（程序化 ImageDraw，绝不读任何图片素材）
# ---------------------------------------------------------------------------

_SYMBOL_PALETTE = {
    "star": {"fill": (255, 216, 77, 235), "line": (214, 158, 0, 255)},
    "heart": {"fill": (255, 127, 168, 240), "line": (222, 66, 116, 255)},
    "tear": {"fill": (134, 201, 247, 230), "line": (78, 148, 210, 255)},
    "steam": {"fill": (238, 238, 238, 150), "line": (200, 200, 200, 90)},
    "spark": {"fill": (255, 255, 255, 245), "line": (255, 245, 190, 220)},
}


def _star_points(cx, cy, r, rot=-math.pi / 2):
    """五角星十顶点。"""
    pts = []
    for i in range(10):
        ang = rot + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.42
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    return pts


def draw_symbol(kind: str, size: int = 16, color=None) -> Image.Image:
    """画一枚粒子符号，返回 size×size 透明底 RGBA 图。

    kind ∈ {star 五角星黄, heart 粉红心形双圆+三角, tear 水蓝泪滴,
    steam 灰白雾团, spark 白色十字闪}；color 可整体换色。
    """
    pal = dict(_SYMBOL_PALETTE[kind])
    if color is not None:
        pal["fill"] = tuple(color[:3]) + (pal["fill"][3],)
        pal["line"] = tuple(min(255, c // 2 + 40) for c in color[:3]) + (
            pal["line"][3],)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = cy = size / 2
    if kind == "star":
        d.polygon(_star_points(cx, cy, size * 0.48), fill=pal["fill"],
                  outline=pal["line"])
    elif kind == "heart":
        r = size * 0.24
        off = size * 0.14
        top = size * 0.28
        d.ellipse((cx - 2 * r - off * 0.2, top, cx - off * 0.2, top + 2 * r),
                  fill=pal["fill"], outline=pal["line"])
        d.ellipse((cx + off * 0.2, top, cx + 2 * r + off * 0.2, top + 2 * r),
                  fill=pal["fill"], outline=pal["line"])
        d.polygon(
            [(cx - 2 * r * 0.86, top + r * 1.15),
             (cx + 2 * r * 0.86, top + r * 1.15),
             (cx, cy + size * 0.46)],
            fill=pal["fill"])
    elif kind == "tear":
        # 泪滴：上尖三角 + 下圆
        br = size * 0.27
        cyb = cy + size * 0.14
        d.ellipse((cx - br, cyb - br, cx + br, cyb + br), fill=pal["fill"],
                  outline=pal["line"])
        d.polygon([(cx - br * 0.92, cyb - br * 0.32),
                   (cx + br * 0.92, cyb - br * 0.32),
                   (cx, cy - size * 0.44)], fill=pal["fill"])
    elif kind == "steam":
        # 雾团：三个错位椭圆叠成一团软雾
        for ox, oy, rr in ((-size * 0.18, size * 0.08, size * 0.24),
                           (size * 0.16, size * 0.02, size * 0.20),
                           (0.0, -size * 0.14, size * 0.17)):
            d.ellipse((cx + ox - rr, cy + oy - rr, cx + ox + rr, cy + oy + rr),
                      fill=pal["fill"], outline=pal["line"])
    elif kind == "spark":
        # 十字闪：横竖两枚细长菱形
        ln, wd = size * 0.46, size * 0.11
        d.polygon([(cx - ln, cy), (cx, cy - wd), (cx + ln, cy),
                   (cx, cy + wd)], fill=pal["fill"])
        d.polygon([(cx, cy - ln), (cx - wd, cy), (cx, cy + ln),
                   (cx + wd, cy)], fill=pal["fill"])
    else:
        raise ValueError(f"未知符号: {kind!r}")
    return im


def draw_vignette(width: int, height: int) -> Image.Image:
    """左上角红色渐晕（生气占位演出）：同心圆阶由外向内加深。"""
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    rmax = 0.9 * min(width, height)
    cx, cy = width * 0.02, height * 0.02
    steps = 26
    for k in range(steps - 1, -1, -1):  # 大圆淡 → 小圆浓，后画的覆盖前画
        t = k / max(1, steps - 1)
        r = 3.0 + rmax * t
        a = int(216 * (1.0 - t) ** 1.35) + 4
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(224, 49, 49, a))
    return im


def draw_steam_puffs(width: int, height: int, t: int = 0,
                     cx: float = None, top_y: float = None) -> Image.Image:
    """头顶两团白色蒸气，随 t 缓慢上升、渐隐循环（生气/犯困共用）。"""
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if cx is None:
        cx = width / 2
    if top_y is None:
        top_y = 6
    cyc = t % 12
    rise = cyc * 1.4
    fade = max(0.25, 1.0 - rise / 17.0)
    for dx, phase in ((-34, 0.35), (26, 0.65)):
        sym = draw_symbol("steam", 30)
        sym = _faded(sym, int(255 * fade * (1.0 - 0.2 * phase)))
        y = top_y - rise * (0.6 + 0.4 * phase)
        im.alpha_composite(sym, (int(cx + dx), int(y)))
    return im


def _faded(img: Image.Image, alpha: int) -> Image.Image:
    """把整张图透明通道乘以 alpha（0..255），用于粒子淡出。"""
    if alpha >= 255:
        return img
    if alpha <= 0:
        return Image.new("RGBA", img.size, (0, 0, 0, 0))
    lut = img.getchannel("A").point(lambda v: v * alpha // 255)
    out = img.copy()
    out.putalpha(lut)
    return out


def apply_blink(frame: Image.Image) -> Image.Image:
    """眨眼模拟：内容上部横带用上下相邻行竖向涂抹覆盖（闭目近似）。"""
    x0, y0, x1, y1 = frame.getbbox() or (0, 0, frame.width, frame.height)
    ch = max(1, y1 - y0)
    band_h = max(3, int(ch * 0.16))
    yy = min(max(y0 + int(ch * 0.26), 1), frame.height - band_h - 2)
    xa, xb = max(x0 - 2, 0), min(x1 + 2, frame.width)
    w = xb - xa
    top = frame.crop((xa, yy - 1, xb, yy)).resize((w, band_h))
    bot = frame.crop((xa, yy + band_h, xb, yy + band_h + 1)).resize((w, band_h))
    band = Image.blend(top, bot, 0.5)
    keep = frame.crop((xa, yy, xb, yy + band_h)).getchannel("A")
    band.putalpha(ImageChops.multiply(band.getchannel("A"), keep))
    frame.paste(band, (xa, yy), band)
    return frame


# ---------------------------------------------------------------------------
# op 构造器与 Recipe
# ---------------------------------------------------------------------------

def hold(beats: int) -> dict:
    """静止帧。"""
    return {"op": "hold", "beats": int(beats)}


def shake(px: int, beats: int) -> dict:
    """水平高频抖动，两端归零。"""
    return {"op": "shake", "px": int(px), "beats": int(beats)}


def squash(ratios, beats: int) -> dict:
    """垂直压扁拉伸序列；宽度反向补偿保持面积守恒。"""
    return {"op": "squash", "ratios": [float(r) for r in ratios],
            "beats": int(beats)}


def lean_back(deg: float, beats: int) -> dict:
    """后仰旋转再复位（deg 为峰值角度）。"""
    return {"op": "lean_back", "deg": float(deg), "beats": int(beats)}


def sweep(deg: float, beats: int, trail: bool = False) -> dict:
    """斜向大扫摆：0→deg→0 的往返弧（前半程 ease 出弧、后半程 ease 收势）。

    与 lean_back 的正弦包络不同，sweep 前半程用 ease_in_out 更「甩」，
    trail=True 时沿弧线叠印半透明残影（大划弧的剪影拖尾）。
    """
    return {"op": "sweep", "deg": float(deg), "beats": int(beats),
            "trail": bool(trail)}


def stretch(sx: float, sy: float, beats: int, dy: int = 0,
            symbol: str | None = None, count: int = 0) -> dict:
    """定向拉伸/位移脉冲：1→(sx, sy, dy)→1 正弦往返（展开/高举这类
    「胀一下又回位」的拍点，端点归中保证循环无缝）。

    symbol + count 可选：粒子随本拍帧窗同步迸发（如高举顶点爆星）。
    """
    op = {"op": "stretch", "sx": float(sx), "sy": float(sy),
          "beats": int(beats), "dy": int(dy)}
    if symbol is not None:
        if symbol not in _SYMBOL_PALETTE:
            raise ValueError(f"未知符号: {symbol!r}，"
                             f"可选 {tuple(_SYMBOL_PALETTE)}")
        op["symbol"] = symbol
        op["count"] = int(count)
    return op


def bounce(amp: int, beats: int) -> dict:
    """上下弹跳（ease_out_back 起跳/落地回弹），每拍一个弧。"""
    return {"op": "bounce", "amp": int(amp), "beats": int(beats)}


def wiggle(deg: float, beats: int) -> dict:
    """左右摆头。"""
    return {"op": "wiggle", "deg": float(deg), "beats": int(beats)}


def spin(turns: int, beats: int) -> dict:
    """原地整圈旋转（透明背景旋转合成）。"""
    return {"op": "spin", "turns": int(turns), "beats": int(beats)}


def particles(symbol: str, count: int, when: str = "end", spread: int = 40,
              color=None) -> dict:
    """程序化符号从角色边缘随机角度飘出淡出。

    when 控制在全片时间轴上的活跃窗口：all/start/middle/end。
    """
    if symbol not in _SYMBOL_PALETTE:
        raise ValueError(f"未知符号: {symbol!r}，可选 {tuple(_SYMBOL_PALETTE)}")
    if when not in ("all", "start", "middle", "end"):
        raise ValueError(f"when 应为 all/start/middle/end，收到 {when!r}")
    op = {"op": "particles", "symbol": symbol, "count": int(count),
          "when": when, "spread": int(spread)}
    if color is not None:
        op["color"] = list(color[:3])
    return op


def pulse(color, pos, rad: int, beats: int) -> dict:
    """指定圆形区域脉冲（如腮红）：pos 为相对内容包围盒的分数坐标。"""
    return {"op": "pulse", "color": list(color[:3]),
            "pos": [float(pos[0]), float(pos[1])], "rad": int(rad),
            "beats": int(beats)}


def blink(beats: int = 2) -> dict:
    """眨眼模拟（上方内容区横向涂抹一条闭合线）。"""
    return {"op": "blink", "beats": int(beats)}


def shake_head_blue(beats: int = 0) -> dict:
    """生气占位专用：头顶白色蒸气两团 + 左上角红色渐晕（全程常驻）。"""
    return {"op": "shake_head_blue", "beats": int(beats)}


@dataclass
class Recipe:
    """一套剪纸动画配方：状态名 + 节奏 + 动作链。"""
    state: str
    fps: int = DEFAULT_FPS
    cycles: int = 1
    steps: list = field(default_factory=list)

    def validate(self) -> None:
        if self.fps is None or int(self.fps) <= 0:
            raise ValueError(f"{self.state}: fps 必须 >0，收到 {self.fps!r}")
        if int(self.cycles) < 1:
            raise ValueError(f"{self.state}: cycles 必须 >=1")
        if not self.steps:
            raise ValueError(f"{self.state}: steps 不能为空")
        for op in self.steps:
            name = op.get("op") if isinstance(op, dict) else None
            if name not in KNOWN_OPS:
                raise ValueError(
                    f"{self.state}: 未知 op {name!r}，可选 {KNOWN_OPS}")


def _as_recipe(recipe) -> Recipe:
    """接受 Recipe 或等价 dict（用户口述 JSON 的直接产物）。"""
    if isinstance(recipe, Recipe):
        return recipe
    if isinstance(recipe, dict):
        return Recipe(state=recipe.get("state", "?"),
                      fps=recipe.get("fps", DEFAULT_FPS),
                      cycles=recipe.get("cycles", 1),
                      steps=list(recipe.get("steps", [])))
    raise TypeError(f"recipe 应为 Recipe 或 dict，收到 {type(recipe)!r}")


RECIPES = {
    "laugh": Recipe("laugh", fps=15, steps=[
        hold(3),                                  # 起手蓄力
        shake(2, 3),                              # 高频小抖
        lean_back(-14, 4),                        # 后仰大笑
        particles("star", 5, when="end"),         # 同时触发星星
        bounce(6, 2),                             # 收势一弹
    ]),
    "cry": Recipe("cry", fps=15, cycles=2, steps=[
        bounce(4, 6),                             # 抽泣起伏
        hold(2),
        particles("tear", 4, when="all"),         # 泪珠全程喷洒
    ]),
    "shock": Recipe("shock", fps=15, steps=[
        squash([1, 0.86, 1.08, 0.94, 1], 5),      # 定格式惊弹
        hold(2),
    ]),
    "eat": Recipe("eat", fps=15, steps=[
        squash([1, 0.93, 1.05, 0.95, 1.03, 0.97, 1], 6),  # 咬合微振 ×3
        particles("spark", 3, when="end", color=(196, 148, 60)),  # 右下碎屑
        hold(1),
    ]),
    "sleep": Recipe("sleep", fps=15, steps=[
        squash([1, 0.92, 0.86, 0.92, 1], 8),      # 融化呼吸
        particles("steam", 2, when="all"),        # 缓慢鼾气
        hold(3),
    ]),
    "idle": Recipe("idle", fps=15, steps=[
        wiggle(3, 4),                             # 左右摆头
        hold(6),
        blink(2),                                 # 彩蛋：眨一次眼
        hold(6),
    ]),
    "cheer": Recipe("cheer", fps=15, steps=[
        shake(4, 2),
        bounce(8, 3),                             # 打气蹦跶
        particles("star", 3, when="end"),
        hold(2),
    ]),
    "angry": Recipe("angry", fps=15, steps=[
        shake_head_blue(),                        # 占位怒演出：蒸气+红晕
        shake(5, 4),
        hold(3),
    ]),
    # 程序剪纸六拍舞（主人钦定的千问阅舞分析）：1垂臂起拍 2斜上大划弧
    # 3回拳卡重拍 4挥臂蓄力 5双臂展开 6高举过头顶；手臂轨迹开放夸张，
    # 循环点在第 6 拍后（每个 op 端点归中 → 拍间与循环点天然无缝）。
    "six_beat": Recipe("six_beat", fps=30, cycles=3, steps=[
        squash([1, 0.88, 1], 7),                  # 拍1 垂臂起拍：下蹲压扁
        sweep(-22, 10, trail=True),               # 拍2 斜上大划弧+弧线残影
        squash([1, 0.85, 1], 3),                  # 拍3 回拳卡重拍：快压 0.85
        wiggle(7, 3),                             # 拍4 挥臂蓄力：高频小摆
        stretch(1.15, 0.94, 6, symbol="star", count=2),   # 拍5 展开+双星
        stretch(0.94, 1.15, 8, dy=-12, symbol="star",
                count=4),                         # 拍6 高举+顶点爆星
    ]),
}

for _name, _r in RECIPES.items():
    _r.validate()


# ---------------------------------------------------------------------------
# compose：配方 + 底图 → 帧序列
# ---------------------------------------------------------------------------

def _particle_window(when: str, total: int):
    third = max(1, total // 3)
    if when == "all":
        return 0, total
    if when == "start":
        return 0, third
    if when == "middle":
        return total // 3, max(total // 3 + 1, 2 * total // 3)
    return max(0, total - third), total


def _hop(p: float) -> float:
    """单次弹跳高度比：起跳 ease_out_back 冲顶，落地回弹下压再归零。"""
    if p < 0.5:
        return ease_out_back(p / 0.5)
    return 1.0 - ease_out_back((p - 0.5) / 0.5)


def _swing(p: float) -> float:
    """往返弧包络：前半程 ease_in_out 甩到峰值，后半程 ease_in_out 收回。"""
    if p < 0.5:
        return ease_in_out(p / 0.5)
    return 1.0 - ease_in_out((p - 0.5) / 0.5)


class _Plan:
    """把配方展开为带帧区间的计划表，逐帧求姿态参数。"""

    def __init__(self, recipe: Recipe, seed: int):
        self.recipe = recipe
        self.seed = seed
        fps = recipe.fps if recipe.fps and int(recipe.fps) > 0 else DEFAULT_FPS
        self.fps = fps
        self.plan = []          # (op, start_frame, n_frames)
        g = 0
        for op in list(recipe.steps) * int(recipe.cycles):
            b = int(op.get("beats", 0) or 0)
            n = beats_to_frames(b, fps) if b > 0 else 0
            if op["op"] != "hold" and n == 0 and b > 0:
                n = 1  # 有拍但被舍入抹掉的兜底
            self.plan.append((op, g, n))
            g += n
        self.total = max(g, 1)  # 全静止兜底至少一帧

    # -- 逐帧姿态 ------------------------------------------------------------
    def state_at(self, f: int) -> dict:
        st = {"dx": 0, "dy": 0, "rot": 0.0, "sx": 1.0, "sy": 1.0,
              "ghosts": ()}
        for op, s, n in self.plan:
            if n <= 0 or not (s <= f < s + n):
                continue
            u = (f - s) / max(1, n - 1)
            self._apply(op, u, f - s, st)
        return st

    def blink_window(self, f: int) -> bool:
        return any(op["op"] == "blink" and n > 0 and s <= f < s + n
                   for op, s, n in self.plan)

    def _apply(self, op: dict, u: float, local: int, st: dict) -> None:
        name = op["op"]
        if name == "shake":
            env = math.sin(math.pi * u)
            st["dx"] += int(round(op["px"] * (1 if local % 2 == 0 else -1)
                                  * env))
        elif name == "squash":
            rs = op["ratios"]
            pos = u * (len(rs) - 1)
            i = min(int(pos), len(rs) - 2) if len(rs) > 1 else 0
            frac = pos - i
            sy = rs[i] + (rs[i + 1] - rs[i]) * frac
            st["sy"] *= max(0.05, sy)
            st["sx"] /= max(0.05, sy)  # 面积守恒的反向补偿
        elif name == "lean_back":
            st["rot"] += op["deg"] * math.sin(math.pi * u)
        elif name == "bounce":
            hops = max(1, int(op.get("beats", 1)))
            p = (u * hops) % 1.0 if u < 1.0 else 0.0
            st["dy"] -= int(round(op["amp"] * _hop(p)))
        elif name == "wiggle":
            waves = max(1, int(op.get("beats", 2)) // 2)
            st["rot"] += op["deg"] * math.sin(2 * math.pi * waves * u)
        elif name == "spin":
            st["rot"] += 360.0 * op["turns"] * u
        elif name == "sweep":
            st["rot"] += op["deg"] * _swing(u)
            if op.get("trail"):
                # 弧线残影：沿来路弧线取两帧旧角度，越近越浓（端点近 0 滤掉）
                st["ghosts"] = tuple(
                    (op["deg"] * _swing(max(0.0, u - lag)), 1.0 - k * 0.42)
                    for k, lag in ((1, 0.14), (2, 0.28))
                    if abs(op["deg"] * _swing(max(0.0, u - lag))) > 2.0)
        elif name == "stretch":
            env = math.sin(math.pi * u)
            st["sx"] *= 1.0 + (op["sx"] - 1.0) * env
            st["sy"] *= 1.0 + (op["sy"] - 1.0) * env
            st["dy"] += int(round(op.get("dy", 0) * env))

    # -- 粒子与常驻特效 -------------------------------------------------------
    def particle_specs(self, total: int, box):
        specs = []
        bbx0, bby0, bbx1, bby1 = box
        ccx, ccy = (bbx0 + bbx1) / 2, (bby0 + bby1) / 2
        radius = max(bbx1 - bbx0, bby1 - bby0) / 2
        # particles 是全局 when 窗口；stretch 等自带 symbol 的 op 随本拍帧窗
        seq = [(op, s, n) for op, s, n in self.plan
               if op["op"] == "particles" or op.get("symbol")]
        for gi, (op, s, n) in enumerate(seq):
            if op["op"] == "particles":
                win_s, win_e = _particle_window(op["when"], total)
            else:
                win_s, win_e = s, s + max(1, n)
            span = max(1, win_e - win_s)
            rng = random.Random(self.seed * 1000003 + gi * 7919 + 17)
            color = op.get("color")
            for i in range(int(op.get("count", 0))):
                theta = rng.uniform(0, 2 * math.pi)
                birth = win_s + rng.uniform(0.0, 0.55) * span
                life = max(4, min(span, int(rng.uniform(10, 18))))
                r0 = radius * rng.uniform(0.9, 1.15) + rng.uniform(
                    0, op.get("spread", 40))
                o = (ccx + math.cos(theta) * r0,
                     ccy + math.sin(theta) * r0)
                if op["symbol"] == "tear":  # 泪珠从脸部上方往下掉
                    o = (ccx + rng.uniform(-radius * 0.3, radius * 0.3),
                         ccy - radius * 0.25)
                    v = (rng.uniform(-0.4, 0.4), rng.uniform(1.6, 2.6))
                elif op["symbol"] == "steam":  # 雾团缓慢上飘
                    v = (rng.uniform(-0.2, 0.2), rng.uniform(-1.1, -0.6))
                else:  # star/spark/heart 从边缘向外抛并整体上偏
                    v = (math.cos(theta) * rng.uniform(1.4, 2.6),
                         math.sin(theta) * rng.uniform(0.6, 1.8) - 1.0)
                specs.append({
                    "o": o, "v": v, "birth": birth, "life": life,
                    "symbol": op["symbol"],
                    "size": int(rng.uniform(13, 21)),
                    "color": color,
                })
        return specs


def _pulse_overlay(size_wh, color, pos_abs, rad, u):
    ov = Image.new("RGBA", size_wh, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    a = int(170 * math.sin(math.pi * u) ** 1.2)
    rr = rad * (0.82 + 0.28 * math.sin(2 * math.pi * u))
    if a > 2 and rr > 1:
        d.ellipse((pos_abs[0] - rr, pos_abs[1] - rr,
                   pos_abs[0] + rr, pos_abs[1] + rr),
                  fill=tuple(color[:3]) + (a,))
    return ov


def compose(recipe, base: Image.Image, seed: int = 42) -> list:
    """按配方驱动底图，返回确定性透明底 RGBA 帧序列（高度 ≤256）。

    - 同 seed 同字节；首尾两帧强制回贴底图原姿态；
    - 所有形变以底边中心为锚（脚踩地），粒子向后叠印。
    """
    r = _as_recipe(recipe)
    r.validate()
    base = base.convert("RGBA")
    bw, bh = base.size
    W, H = bw + 2 * MARGIN, bh + 2 * MARGIN
    floor_y = H - MARGIN
    cx = W // 2
    box = base.getbbox() or (0, 0, bw, bh)

    plan = _Plan(r, seed)
    total = plan.total
    particles_all = plan.particle_specs(total, box)
    wants_vignette = any(op["op"] == "shake_head_blue"
                         for op, _, _ in plan.plan)
    pulse_ops = [(op, s, n) for op, s, n in plan.plan
                 if op["op"] == "pulse" and n > 0]
    sym_cache = {}

    def _sprite(f):
        spr = base.copy()
        if plan.blink_window(f):
            spr = apply_blink(spr)
        return spr

    def _paste_figure(layer, f):
        st = plan.state_at(f)
        # 弧线残影先印（低透明度旧角度剪影），正身叠在上面盖过交叠区
        for gang, strength in st.get("ghosts", ()):
            ghost = base.rotate(gang, resample=Image.Resampling.BICUBIC,
                                expand=True)
            gw, gh = ghost.size
            layer.alpha_composite(
                _faded(ghost, int(110 * strength)),
                (int(cx - gw / 2), int(floor_y - gh)))
        spr = _sprite(f)
        if abs(st["sx"] - 1.0) > 1e-4 or abs(st["sy"] - 1.0) > 1e-4:
            nw = max(1, int(round(bw * st["sx"])))
            nh = max(1, int(round(bh * st["sy"])))
            spr = spr.resize((nw, nh), Image.Resampling.LANCZOS)
        if abs(st["rot"]) > 0.01:
            spr = spr.rotate(st["rot"], resample=Image.Resampling.BICUBIC,
                             expand=True)
        fw, fh = spr.size
        layer.alpha_composite(
            spr, (int(cx - fw / 2 + st["dx"]), int(floor_y - fh + st["dy"])))
        return st

    def _particles_for(f):
        outs = []
        for p in particles_all:
            t = f - p["birth"]
            if t < 0 or t > p["life"]:
                continue
            key = (p["symbol"], p["size"])
            if key not in sym_cache:
                sym_cache[key] = draw_symbol(key[0], key[1], p["color"])
            q = t / max(1, p["life"])
            faded = _faded(sym_cache[key], int(255 * (1.0 - q)))
            if faded.getbbox():
                outs.append((faded,
                             (int(p["o"][0] + p["v"][0] * t),
                              int(p["o"][1] + p["v"][1] * t))))
        return outs

    def _overlay_pass(layer, f, st):
        if wants_vignette:
            layer.alpha_composite(draw_vignette(W, H), (0, 0))
            steam = draw_steam_puffs(W, H, t=f, cx=cx + st["dx"],
                                     top_y=MARGIN + box[1] + st["dy"] - 8)
            layer.alpha_composite(steam, (0, 0))
        for op, s, n in pulse_ops:
            if s <= f < s + n:
                u = (f - s) / max(1, n - 1)
                px = MARGIN + box[0] + op["pos"][0] * (box[2] - box[0])
                py = MARGIN + box[1] + op["pos"][1] * (box[3] - box[1])
                layer.alpha_composite(
                    _pulse_overlay((W, H), op["color"], (px + st["dx"],
                                                         py + st["dy"]),
                                   op["rad"], u), (0, 0))
        for img, pos in _particles_for(f):
            x = min(max(pos[0], -img.width), W - 1)
            y = min(max(pos[1], -img.height), H - 1)
            layer.alpha_composite(img, (int(x), int(y)))

    def _scaler(img):
        if img.height <= TARGET_HEIGHT:
            return img
        sc = TARGET_HEIGHT / img.height
        return img.resize((int(round(img.width * sc)), TARGET_HEIGHT),
                          Image.Resampling.LANCZOS)

    def _render(f):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        st = _paste_figure(layer, f)
        _overlay_pass(layer, f, st)
        return _scaler(layer)

    def _render_still():
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer.alpha_composite(base.copy(), (int(cx - bw / 2), int(floor_y - bh)))
        return _scaler(layer)

    # 首尾两帧强制回贴底图原姿态（含缩放路径一致，保证逐字节可对比）
    return [_render(f) if 0 < f < total - 1 else _render_still()
            for f in range(total)]
