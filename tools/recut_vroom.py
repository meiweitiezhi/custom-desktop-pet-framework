# -*- mode: python ; coding: utf-8 -*-
"""骑摩托（vroom）母图重切器：治旧帧「白边光晕/灰杂边/锯齿/烟雾破碎」。

旧 4 帧是历史会话的一次性脚本烘的（生成器已丢），且用的是仓库通用
flood_clear_background（中位边界色洪水填充）——对这张母图水土不服：
背景是浅米白带淡红棕虚线圆圈装饰 + JPEG 压缩噪点，纯色洪水要么漏过
噪点留下毛边、要么切不干净装饰圆圈。

本脚本的对策（全离线、纯 PIL、确定性，主人拍板 2026-09）：
1. 色距掩码 + 连通性双保险：只有「颜色接近背景 且 与画布边连通」的
   像素才算背景——白色身体即使色近背景也因不连通而保命；
2. 孤岛清理：主体是最大连通块，虚线圆圈碎片/噪点孤岛全数丢弃；
3. 边缘三连治疗：alpha 蚀缩杀白边 -> 高斯羽化抗锯齿 -> 半透明像素
   颜色从不透明邻居重采样（去污重上色，杀灰边）；
4. 尾气烟雾按主人拍板切干净，改程序化粒子（软圆渐隐）重画；
5. 怠速抖动 4 帧重烘（[0,+2,0,-2]px，R00==R02 与旧版节拍一致）。

用法：
  python tools/recut_vroom.py --preview   # 只出母版+对比预览，肉眼验收
  python tools/recut_vroom.py             # 全量：重切+烘 4 帧落 states/
"""
import sys
import pathlib
from collections import deque

from PIL import Image, ImageDraw, ImageFilter, ImageChops

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "raw" / "74044499058e09a55d0f1db64c7973ee.jpg"
OUT_DIR = ROOT / "assets" / "states"
DRAFTS = ROOT / "assets" / "raw" / "drafts"
OLD_FRAMES = [ROOT / f"assets/states/vroom_R{i:02d}.png" for i in range(4)]

# —— 可调参数（调完看 --preview 迭代）——
BG_DIST_T = 95          # 色距平方和阈值：与背景中位色的距离小于它算「背景色系」
ERODE_PX = 3            # alpha 蚀缩像素（杀白边光晕，源图 1440 宽口径）
FEATHER_PX = 2          # alpha 羽化半径（抗锯齿）
DESPILL_BLUR = 9        # 去污重上色的邻域采样半径
FINAL_WIDTH = 549       # 成品宽（对齐旧帧口径，显示层会再缩到 display_size）
MARGIN = 16             # 裁剪主体 bbox 外留边
SHAKE = (0, 2, 0, -2)   # 怠速抖动：4 帧竖向偏移（0,+2,0,-2 -> R00==R02）
# 尾气粒子锚点（相对切完 bbox 的比例；面向定夺后写死，见 --preview 流程）
PUFF_ANCHOR = (0.10, 0.65)
PUFF_DIR = (-1, -0.15)  # 粒子漂移方向（车尾朝左上）
PUFF_COUNT = 3


def bg_median(im: Image.Image) -> tuple:
    """取画布 8px 边框环的中位色（JPEG 噪点下中位数稳）。"""
    w, h = im.size
    ring = []
    px = im.load()
    step = 4
    for x in range(0, w, step):
        for y in list(range(0, 8)) + list(range(h - 8, h)):
            ring.append(px[x, y])
    for y in range(0, h, step):
        for x in list(range(0, 8)) + list(range(w - 8, w)):
            ring.append(px[x, y])
    chans = zip(*ring)
    return tuple(sorted(c)[len(c) // 2] for c in chans)


def flood_background(im: Image.Image, bg: tuple) -> set:
    """从画布边出发，经「色距够近」像素扩散——返回连通背景像素集合。"""
    w, h = im.size
    px = im.load()
    bgset = bytearray(w * h)      # 1=背景色系
    for i in range(w * h):
        pass
    for y in range(h):
        row = px[0, 0]
        break
    # 先标背景色系
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y][:3]
            d = (r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2
            if d < BG_DIST_T:
                bgset[y * w + x] = 1
    # 再从四边洪水扩散（只走背景色系像素）
    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            i = y * w + x
            if bgset[i] and not seen[i]:
                seen[i] = 1
                q.append(i)
    for y in range(h):
        for x in (0, w - 1):
            i = y * w + x
            if bgset[i] and not seen[i]:
                seen[i] = 1
                q.append(i)
    while q:
        i = q.popleft()
        x, y = i % w, i // w
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h:
                j = ny * w + nx
                if bgset[j] and not seen[j]:
                    seen[j] = 1
                    q.append(j)
    return set(i for i in range(w * h) if seen[i])


def keep_significant_components(subject: set, w: int, h: int,
                                 ratio: float = 0.06) -> set:
    """保「显著」连通块：主体会被背景缝隙拆成几块（车/双熊/轮），只丢
    虚线圆圈碎片和噪点孤岛——面积 ≥ 最大块 3% 的一律保留。"""
    subj = bytearray(w * h)
    for i in subject:
        subj[i] = 1
    seen = bytearray(w * h)
    comps = []
    for start in subject:
        if seen[start]:
            continue
        comp, q = set(), deque([start])
        seen[start] = 1
        while q:
            i = q.popleft()
            comp.add(i)
            x, y = i % w, i // w
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if subj[j] and not seen[j]:
                        seen[j] = 1
                        q.append(j)
        comps.append(comp)
    if not comps:
        return set()
    biggest = max(len(c) for c in comps)
    floor = biggest * ratio
    return {i for c in comps if len(c) >= floor for i in c}


def cutout_master() -> Image.Image:
    """母图 -> 治疗完的透明底主体立绘（源图原分辨率处理，末了才缩）。"""
    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    bg = bg_median(im)
    flooded = flood_background(im, bg)
    subject_idx = set(range(w * h)) - flooded
    kept = keep_significant_components(subject_idx, w, h)

    alpha = Image.new("L", (w, h), 0)
    apx = alpha.load()
    for i in kept:
        apx[i % w, i // w] = 255

    # 边缘三连治疗：蚀缩 -> 羽化 -> 去污重上色
    alpha = alpha.filter(ImageFilter.MinFilter(ERODE_PX * 2 + 1))
    alpha = alpha.filter(ImageFilter.GaussianBlur(FEATHER_PX))

    solid = alpha.point(lambda v: 255 if v > 200 else 0)
    rgb_masked = ImageChops.multiply(im, solid.convert("RGB"))
    ref = rgb_masked.filter(ImageFilter.BoxBlur(DESPILL_BLUR))
    wgt = solid.filter(ImageFilter.BoxBlur(DESPILL_BLUR)).convert("RGB")

    rgba = im.convert("RGBA")
    rgba.putalpha(alpha)
    rp, fp, wp, sp = rgba.load(), ref.load(), wgt.load(), solid.load()
    for y in range(h):
        for x in range(w):
            if not sp[x, y]:          # 半透明边：颜色换成不透明邻域的采样
                wr, wg, wb = wp[x, y]
                if wr + wg + wb > 30:
                    r, g, b = fp[x, y]
                    rp[x, y] = (int(r * 255 / wr), int(g * 255 / wg),
                                int(b * 255 / wb), rp[x, y][3])
    return rgba


def crop_and_scale(rgba: Image.Image) -> Image.Image:
    bbox = rgba.getchannel("A").getbbox()
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0 - MARGIN), max(0, y0 - MARGIN)
    x1, y1 = min(rgba.width, x1 + MARGIN), min(rgba.height, y1 + MARGIN)
    crop = rgba.crop((x0, y0, x1, y1))
    scale = FINAL_WIDTH / crop.width
    return crop.resize((FINAL_WIDTH, max(1, round(crop.height * scale))),
                       Image.LANCZOS)


def draw_puffs(canvas: Image.Image, anchor: tuple, phase: int):
    """程序化尾气：软圆粒子随相位漂移/长大/渐隐（与旧烟雾位置无关，
    锚点按切完 bbox 比例写死）。"""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = canvas.size
    ax, ay = int(anchor[0] * w), int(anchor[1] * h)
    for k in range(PUFF_COUNT):
        t = (phase + k) / PUFF_COUNT          # 各粒子错相
        r = int(h * (0.05 + 0.07 * t))
        dx = int(PUFF_DIR[0] * w * 0.10 * t)
        dy = int(PUFF_DIR[1] * h * 0.22 * t)
        a = int(140 * (1.0 - t) + 25)
        d.ellipse([ax + dx - r, ay + dy - r, ax + dx + r, ay + dy + r],
                  fill=(236, 236, 236, max(45, a)))
    layer = layer.filter(ImageFilter.GaussianBlur(6))
    canvas.alpha_composite(layer)


def bake_frames(master: Image.Image):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    w = master.width + 24
    h = master.height + 24
    for i in range(4):
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(master, (12, 12 + SHAKE[i]), master)
        draw_puffs(canvas, PUFF_ANCHOR, i)
        out = OUT_DIR / f"vroom_R{i:02d}.png"
        canvas.save(out)
        print(f"[vroom重切] {out.name} {w}x{h}")


def make_preview(master: Image.Image):
    """新旧边缘对比条：上排旧帧、下排新切，等宽并排落 drafts 供肉眼验收。"""
    DRAFTS.mkdir(parents=True, exist_ok=True)
    old = Image.open(OLD_FRAMES[1]).convert("RGBA")
    strips = []
    for tag, img in (("旧", old), ("新", master)):
        s = img.resize((FINAL_WIDTH, round(img.height * FINAL_WIDTH
                                           / img.width)), Image.LANCZOS)
        strips.append((tag, s))
    ph = max(s.height for _, s in strips) + 28
    board = Image.new("RGB", (FINAL_WIDTH * 2 + 30, ph), (250, 250, 250))
    from PIL import ImageFont
    try:
        font = ImageFont.load_default(18)
    except Exception:
        font = ImageFont.load_default()
    d = ImageDraw.Draw(board)
    x = 10
    for tag, s in strips:
        d.text((x, 4), tag, fill=(180, 30, 30), font=font)
        board.paste(s, (x, 26), s)
        x += FINAL_WIDTH + 20
    out = DRAFTS / "vroom_recut_preview.png"
    board.save(out)
    print(f"[vroom重切] 预览 -> {out}")
    master.save(DRAFTS / "vroom_recut_master.png")


def main():
    preview_only = "--preview" in sys.argv
    master = crop_and_scale(cutout_master())
    make_preview(master)
    if not preview_only:
        bake_frames(master)
        print("[vroom重切] 4 帧已落 assets/states/，manifest 无需改动")


if __name__ == "__main__":
    main()
