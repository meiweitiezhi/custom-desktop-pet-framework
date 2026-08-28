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

插帧烘焙管线（动作流畅度优化）：bake_all_smooth 把帧数 <=8 的多帧状态
（laugh/shock/cry/love/hide/alien/blushmax/eat/sleep）逐对相邻帧用
PIL.Image.blend 插出渐变帧，末尾融回 idle 收招，输出 <状态>_S{idx:03d}.png
并把新 frames/frame_ms/pingpong 合并进 manifest；eat/sleep 走特别慢速档。
dance 等 61 帧全帧档天生丝滑，明确跳过。
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


# ---------------------------------------------------------------- 插帧烘焙管线
# 新烘焙帧的命名标签：大写 S 与旧 _f（GIF 抽稀档）/_F（全帧档）区分
SMOOTH_TAG = "S"
# 烘焙后单帧时长的合法区间（毫秒）：目标 3 倍原时长，落界外一律钳回
SMOOTH_MS_MIN, SMOOTH_MS_MAX = 60, 120
# 多帧状态参与烘焙的帧数上限：dance 等 61 帧全帧档天生丝滑，明确跳过
SMOOTH_MAX_SOURCE_FRAMES = 8
# 特别慢速档：eat 咀嚼 / sleep 融化要"慢动作享受"的慵懒感——多插一档
# 渐变帧、单帧时长下限放宽到 140ms（循环总时长 3 秒以上），其余状态照旧轻快
SLOW_STATES = ("eat", "sleep")
SLOW_BLENDS = 3
SLOW_MS_MIN, SLOW_MS_MAX = 140, 200


def load_state_frames(states_dir, entry) -> list:
    """按 manifest 条目的 frames 顺序读入 RGBA 帧列表。

    frames 里的路径形如 states/laugh_f0.png（相对 assets/），这里只认
    文件名拼到 states_dir 下——states_dir 就是那个 states 目录本身。
    """
    frames = []
    for rel in entry.get("frames") or ():
        frames.append(
            Image.open(Path(states_dir) / Path(str(rel)).name).convert("RGBA"))
    return frames


def interpolate(a: Image.Image, b: Image.Image, steps: int) -> list:
    """a 与 b 之间生成 steps 张渐变帧（PIL.Image.blend）。

    含 0/1 端点但不重复：只返回中间帧，alpha 取 i/(steps+1)，i=1..steps，
    渐进单调逼近 b。steps<=0 返回空列表；两图尺寸不齐时对齐到 a。
    全程确定性：同样输入永远得到逐像素一致的输出。
    """
    try:
        steps = int(steps)
    except (TypeError, ValueError):
        return []
    if steps <= 0:
        return []
    a = a.convert("RGBA")
    b = b.convert("RGBA")
    if b.size != a.size:
        b = b.resize(a.size)
    return [Image.blend(a, b, (i + 1) / (steps + 1)) for i in range(steps)]


def bake_smooth(state_entry, states_dir, blends: int = 2,
                tail_to=None, ms_min: int = SMOOTH_MS_MIN,
                ms_max: int = SMOOTH_MS_MAX) -> dict:
    """把一个 6 帧骨折档状态烘焙成丝滑档，返回 manifest 补丁片段。

    1. 读原帧序列 F0..Fn（load_state_frames）；
    2. 相邻帧间插 blends 张渐变帧 -> 新序列 n + (n-1)*blends 帧；
    3. tail_to 给了 idle 图时，在末尾追加「收招余韵」：与末帧 50% 融合帧、
       idle 原图收尾帧，共 2 帧；
    4. 新帧逐张存 <state>_S{idx:03d}.png（大写 S 与旧 _f/_F 区分）；
    5. 返回 {"frames": 新帧列表, "frame_ms": ..., "pingpong": True}，
       frame_ms = clamp(round(原总时长*3/新帧数), ms_min, ms_max)——目标
       循环总时长约为原来的 3 倍，节奏放慢但仍在界限内。
    """
    src = load_state_frames(states_dir, state_entry)
    if not src:
        raise ValueError("条目没有可读的 frames，无法烘焙")
    # 相邻帧间各插 blends 张渐变帧，端点不重复
    seq = [src[0]]
    for prev, fr in zip(src, src[1:]):
        seq.extend(interpolate(prev, fr, blends))
        seq.append(fr)
    # 收招余韵：末帧与 idle 融合收尾（50% 过渡 + idle 原图定格）
    if tail_to is not None:
        idle = Image.open(tail_to).convert("RGBA")
        last = seq[-1]
        seq.extend(interpolate(last, idle, 1))
        seq.append(idle.resize(last.size) if idle.size != last.size else idle)
    states_dir = Path(states_dir)
    states_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(str(state_entry.get("file") or "state")).stem
    rels = []
    for i, fr in enumerate(seq):
        name = f"{stem}_{SMOOTH_TAG}{i:03d}.png"
        fr.save(states_dir / name)
        rels.append(f"states/{name}")
    try:
        old_ms = int(state_entry.get("frame_ms") or DEFAULT_FRAME_MS)
    except (TypeError, ValueError):
        old_ms = DEFAULT_FRAME_MS
    target_total = len(src) * old_ms * 3
    frame_ms = int(round(target_total / len(seq)))
    frame_ms = max(ms_min, min(ms_max, frame_ms))
    return {"frames": rels, "frame_ms": frame_ms, "pingpong": True}


def bake_all_smooth(manifest_path: Path = MANIFEST, states_dir: Path = OUT,
                    tail=None, blends: int = 2, limit: int =
                    SMOOTH_MAX_SOURCE_FRAMES) -> dict:
    """批量烘焙：只处理 0 < len(frames) <= limit 的多帧状态，dance 跳过。

    eat/sleep 走特别慢速档（SLOW_BLENDS/SLOW_MS_*），其余状态维持
    blends=2、60~120ms。每个状态独立容错——单状态素材缺失只警告跳过，
    绝不拖垮整批。返回 {"状态名": 补丁片段} 并经 merge_manifest_entries
    写回 manifest。
    """
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    patch = {}
    for name, entry in (data.get("states") or {}).items():
        frames = entry.get("frames")
        if not isinstance(frames, (list, tuple)) or not frames:
            continue
        if len(frames) > limit:
            print(f"[烘焙] {name}: {len(frames)} 帧全帧档天生丝滑，跳过")
            continue
        slow = name in SLOW_STATES
        try:
            patch[name] = bake_smooth(
                entry, states_dir,
                blends=SLOW_BLENDS if slow else blends,
                tail_to=tail,
                ms_min=SLOW_MS_MIN if slow else SMOOTH_MS_MIN,
                ms_max=SLOW_MS_MAX if slow else SMOOTH_MS_MAX)
            print(f"[烘焙] {name}: {len(frames)} 帧 -> "
                  f"{len(patch[name]['frames'])} 帧 "
                  f"frame_ms={patch[name]['frame_ms']}"
                  f"{'（慢速档）' if slow else ''}")
        except Exception as exc:
            print(f"[烘焙] {name}: 跳过（{exc}）")
    if patch:
        merge_manifest_entries(patch, manifest_path)
    return patch


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
