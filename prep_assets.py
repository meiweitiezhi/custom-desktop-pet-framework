"""素材预处理：把 assets/raw/*.png 抠成透明底，输出到 assets/states/。

做法：
1. 图片已有 alpha 且大量透明 -> 认为是免抠素材，直接过；
2. 否则以四边中点像素的中位色为背景色，从边界泛洪填充(BFS)，
   只清除与背景连通的区域——白色圆身子内部的白色不会被误伤。
最后自动裁剪到不透明区域的包围盒。

多帧管线（同名多源时**全帧视频优先**）：
- assets/raw/<状态名>.mp4 等源视频存在 -> extract_full_frames 全帧率切片，
  输出 <状态>_F{index:03d}.png（与旧 6 帧 _f{i} 区分）；
- 否则回退 GIF：Pillow 取 n_frames -> animator_core.sample_frames
  抽稀 <=6 帧 -> 逐帧同一容差抠图 -> 所有帧透明区联合包围盒统一裁剪（防帧间抖动）
  -> 输出 <状态>_f{i}.png。
两类产物都会把 frames/frame_ms 合并进 assets/manifest.json。
"""
import json
import math
from collections import deque
from pathlib import Path

from PIL import Image, ImageChops

from petfw.animator_core import DEFAULT_CAP, sample_frames

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "assets" / "raw"
OUT = ROOT / "assets" / "states"
MANIFEST = ROOT / "assets" / "manifest.json"

TOLERANCE = 30  # 每通道容差
DEFAULT_FRAME_MS = 120  # GIF duration 缺失时的兜底节奏


def median_border_color(im: Image.Image):
    w, h = im.size
    px = im.load()
    samples = [px[x, 0] for x in range(0, w, max(1, w // 20))]
    samples += [px[x, h - 1] for x in range(0, w, max(1, w // 20))]
    samples += [px[0, y] for y in range(0, h, max(1, h // 20))]
    samples += [px[w - 1, y] for y in range(0, h, max(1, h // 20))]
    channels = list(zip(*samples))
    return tuple(sorted(ch)[len(ch) // 2] for ch in channels)


def already_transparent(im: Image.Image) -> bool:
    if im.mode != "RGBA":
        return False
    alpha = im.getchannel("A")
    lo, hi = alpha.getextrema()
    return hi < 250 or lo == 0  # 有实打实的透明区域


def flood_clear_background(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    bg = median_border_color(im)

    def is_bg(p):
        return abs(p[0] - bg[0]) <= TOLERANCE and \
               abs(p[1] - bg[1]) <= TOLERANCE and \
               abs(p[2] - bg[2]) <= TOLERANCE and p[3] > 16

    visited = [[False] * w for _ in range(h)]
    dq = deque()
    for x in range(w):
        for y in (0, h - 1):
            dq.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            dq.append((x, y))
    while dq:
        x, y = dq.popleft()
        if x < 0 or y < 0 or x >= w or y >= h or visited[y][x]:
            continue
        visited[y][x] = True
        p = px[x, y]
        if not is_bg(p):
            continue
        px[x, y] = (p[0], p[1], p[2], 0)
        dq.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return im


def trim(im: Image.Image) -> Image.Image:
    bbox = im.getchannel("A").getbbox()
    return im.crop(bbox) if bbox else im


# ---------------------------------------------------------------- GIF 多帧管线
def union_bbox(images) -> tuple | None:
    """所有帧不透明区域包围盒的并集：多帧裁到同一画布防抖动。"""
    box = None
    for im in images:
        b = im.getchannel("A").getbbox()
        if not b:
            continue
        box = b if box is None else (
            min(box[0], b[0]), min(box[1], b[1]),
            max(box[2], b[2]), max(box[3], b[3]))
    return box


def median_frame_ms(durations, default: int = DEFAULT_FRAME_MS) -> int:
    """取帧时长中位数取整；缺失/为 0 的样本先剔除，全缺则退 default。"""
    vals = sorted(int(v) for v in durations or () if v and int(v) > 0)
    if not vals:
        return int(default)
    mid = len(vals) // 2
    if len(vals) % 2:
        return int(vals[mid])
    return int(round((vals[mid - 1] + vals[mid]) / 2))


def extract_gif_frames(path: Path, cap: int = DEFAULT_CAP) -> tuple[list, int]:
    """打开 GIF -> 均匀抽稀 <=cap 帧 -> 逐帧沿用 PNG 管线抠图。

    返回 (抠好背景的 RGBA 帧列表, 中位帧时长毫秒)。时长从被抽中的帧读取，
    取中位数取整；GIF 没写 duration 时退 120ms。
    """
    src = Image.open(path)
    total = int(getattr(src, "n_frames", 1))
    picks = sample_frames(total, cap)
    frames, durations = [], []
    for i in picks:
        src.seek(i)
        frame = src.convert("RGBA")
        durations.append(int(src.info.get("duration") or 0))
        # 与单图管线完全一致的判定与抠图（同容差、同边界中位色）
        frames.append(frame if already_transparent(frame)
                      else flood_clear_background(frame))
    return frames, median_frame_ms(durations)


def process_gif(path: Path, out_dir: Path = OUT,
                cap: int = DEFAULT_CAP) -> dict:
    """完整处理一个 raw GIF，输出 <stem>_f{i}.png 并返回 manifest 补丁条目。"""
    path = Path(path)
    frames, frame_ms = extract_gif_frames(path, cap=cap)
    if not frames:
        raise SystemExit(f"{path.name} 一帧都抽不出来")
    box = union_bbox(frames) or (0, 0, frames[0].width, frames[0].height)
    out_dir.mkdir(parents=True, exist_ok=True)
    rels = []
    for i, fr in enumerate(frames):
        cropped = fr.crop(box)   # 统一画布：帧之间绝不互相错位
        name = f"{path.stem}_f{i}.png"
        cropped.save(out_dir / name)
        rels.append(f"states/{name}")
    print(f"{path.name} {len(rels)}帧 -> 联合包围盒{box} "
          f"frame_ms={frame_ms}")
    return {"frames": rels, "frame_ms": int(frame_ms)}


def merge_manifest_entries(entries: dict,
                           manifest_path: Path = MANIFEST) -> dict:
    """把 {"状态名": {...补丁...}} 读改写进 manifest.json。

    json 全量读改写保证格式稳定；既有键与其它状态一概保留，
    只补/更新出现的字段。manifest 不存在时按最小骨架创建。
    """
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        data = {"pet": "my-pet", "states": {}}
    states = data.setdefault("states", {})
    for name, patch in entries.items():
        states.setdefault(name, {}).update(patch)
    manifest_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return data


# ---------------------------------------------------------------- 全帧动作管线
VIDEO_SUFFIXES = (".mp4", ".mov", ".avi")   # 同名源视频优先于 GIF 抽稀
MAX_ACTION_FRAMES = 240                     # 全帧动作的单段上限（内存友好）
DEDUP_GRAY_DIFF = 2.0                       # 相邻帧灰度均值差 < 该值视为静止


def _gray_mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    """相邻帧平均逐像素差异：完全相同=0，主体位移会显著大于阈值。

    逐像素差的均值（整图），纯 PIL 实现：difference 后按直方图加权求均值，
    不依赖 numpy。
    """
    da = a.convert("L")
    db = b.convert("L")
    if da.size != db.size:
        db = db.resize(da.size)
    h = ImageChops.difference(da, db).histogram()
    total = sum(h)
    if not total:
        return 0.0
    return sum(v * i for i, v in enumerate(h)) / float(total)


def find_source_video(stem: str, raw_dir: Path = RAW) -> Path | None:
    """找某状态的同名源视频母带；找不到返回 None。"""
    for suf in VIDEO_SUFFIXES:
        cand = raw_dir / f"{stem}{suf}"
        if cand.exists():
            return cand
    return None


def _read_video_frames(video_path):
    """MP4 解码薄封装（imageio_ffmpeg）。

    成功返回 (RGB 帧列表, 元数据 dict{fps,size})；解码器缺失、文件损坏、
    任何异常一律返回 ([], None)，绝不向上抛错——测试里直接 stub 本函数。
    """
    try:
        import imageio_ffmpeg
    except Exception:
        return [], None
    try:
        gen = imageio_ffmpeg.read_frames(str(video_path))
        meta = next(gen)
        width, height = meta["size"]
        fps = float(meta.get("fps") or 0.0)
        frames = [Image.frombytes("RGB", (width, height), chunk)
                  for chunk in gen]
        close = getattr(gen, "close", None)
        if close:
            close()
        return frames, {"fps": fps, "size": (width, height)}
    except Exception:
        return [], None


def build_action(frames_dir, out_dir: Path | None = None,
                 state: str | None = None, fps_est: float = 0.0,
                 fps_cap: float | None = None,
                 max_frames: int = MAX_ACTION_FRAMES) -> dict:
    """全帧动作核心：帧序列 -> 可选抽稀 -> 静止去重 -> 统一抠图画布。

    参数 frames_dir 可以是「装满 PNG 的目录」（按文件名排序读取），也可以
    直接给一叠 PIL Image（extract_full_frames 解码后的产物）。
    out_dir+state 同时给出时把成品写成 <state>_F{index:03d}.png——大写 F
    与旧 6 帧 <state>_f{i} 抽稀档区分。
    返回元数据 {"frames": [文件名...], "fps_est": 有效帧率估算, "count": n}；
    输入为空时 count=0、frames=[]，绝不抛错。
    """
    fps_est = float(fps_est or 0.0)
    # 目录路径：按文件名排序逐张读入；已是图片列表则原样使用
    if isinstance(frames_dir, (str, Path)):
        directory = Path(frames_dir)
        images = [Image.open(p)
                  for p in sorted(directory.glob("*.png"))]
    else:
        images = list(frames_dir or [])
    # fps_cap 抽稀：等距丢帧让有效帧率不超过上限，防止高帧率母带爆量
    if fps_cap and fps_cap > 0 and fps_est > fps_cap:
        stride = max(1, math.ceil(fps_est / fps_cap))
        images = images[::stride]
        fps_est = min(fps_est, float(fps_cap))
    # 相邻去重：平均逐像素差几乎为零的静止帧跳过，播放时不再"定格闪跳"
    kept: list[Image.Image] = []
    prev: Image.Image | None = None
    for im in images:
        if prev is not None and _gray_mean_abs_diff(prev, im) < DEDUP_GRAY_DIFF:
            continue
        prev = im
        kept.append(im)
        if len(kept) >= max_frames:
            break
    # 沿用 GIF 管线同款判定与抠图，再裁到透明区联合包围盒统一画布
    mats = []
    for im in kept:
        frame = im.convert("RGBA")
        mats.append(frame if already_transparent(frame)
                    else flood_clear_background(frame))
    names: list[str] = []
    if mats:
        box = union_bbox(mats) or (0, 0, mats[0].width, mats[0].height)
        if out_dir is not None and state:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
        for i, fr in enumerate(mats):
            name = f"{state or 'action'}_F{i:03d}.png"
            if out_dir is not None and state:
                fr.crop(box).save(Path(out_dir) / name)
            names.append(name)
    print(f"[全帧] state={state or '?'} 读入 {len(images)} 帧 -> "
          f"去重后 {len(names)} 帧 fps_est={fps_est:g}")
    return {"frames": names, "fps_est": fps_est, "count": len(names)}


def extract_full_frames(video_path, out_dir: Path, state: str,
                        fps_cap: float | None = None,
                        max_frames: int = MAX_ACTION_FRAMES) -> dict:
    """读一段源视频的全部帧并切成全帧动作；解码失败返回空元数据。

    返回 {"frames": [<state>_F000.png...], "fps_est": 容器帧率(可被 cap 压),
          "count": n}。
    """
    frames, meta = _read_video_frames(video_path)
    fps_est = float((meta or {}).get("fps") or 0.0)
    if not frames:
        print(f"[全帧] {Path(str(video_path)).name} 解不出帧"
              f"（缺 imageio_ffmpeg 或文件损坏），跳过")
        return {"frames": [], "fps_est": fps_est, "count": 0}
    return build_action(frames, out_dir=out_dir, state=state,
                        fps_est=fps_est, fps_cap=fps_cap,
                        max_frames=max_frames)


# ---------------------------------------------------------------- 单图管线
def process(path: Path, out_dir: Path = OUT) -> Image.Image:
    im = Image.open(path)
    if not already_transparent(im):
        im = flood_clear_background(im)
    im = trim(im)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{path.stem}.png"
    im.save(out)
    print(f"{path.name} {Image.open(path).size} -> {out.name} {im.size}")
    return im


def merge_full_frame_patch(md: dict, state: str) -> dict:
    """把 build_action/extract_full_frames 的元数据洗成 manifest 补丁条目。

    frame_ms = int(1000/fps_est)，上限 60ms（帧率再低也按 >=16.7fps 的
    节奏播，避免拖沓）；并声明 play=once——点播一次完整播放。
    """
    fps = float(md.get("fps_est") or 0)
    if fps > 0:
        frame_ms = max(8, min(60, int(round(1000.0 / fps))))
    else:
        frame_ms = DEFAULT_FRAME_MS
    return {
        "frames": [f"states/{name}" for name in md.get("frames", [])],
        "frame_ms": frame_ms,
        "play": "once",
    }


if __name__ == "__main__":
    png_files = sorted(RAW.glob("*.png"))
    gif_files = sorted(RAW.glob("*.gif"))
    video_stems = {p.stem for suf in VIDEO_SUFFIXES for p in RAW.glob(suf)}
    multi_stems = {f.stem for f in gif_files} | video_stems
    if not png_files and not multi_stems:
        raise SystemExit(f"{RAW} 下没有图片")
    for f in png_files:
        process(f)
    patch = {}
    # 同名多源：源视频母带全帧优先，解不出帧再回落 GIF 抽稀档
    for stem in sorted(multi_stems):
        vid = find_source_video(stem)
        md = extract_full_frames(vid, OUT, stem) if vid else None
        if md and md["count"]:
            patch[stem] = merge_full_frame_patch(md, stem)
            print(f"{stem}: 全帧 {md['count']} 帧 @ {md['fps_est']:g}fps")
            continue
        gif = RAW / f"{stem}.gif"
        if gif.exists():
            patch[stem] = process_gif(gif)
            if vid:
                print(f"[团子] {vid.name} 解不出帧，已回落 GIF 抽稀档")
    if patch:
        merge_manifest_entries(patch)
        print("manifest 已并入多帧条目:", ", ".join(sorted(patch)))
    print("完成。成品已输出到", OUT)
