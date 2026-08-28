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

30fps 密度烘焙管线（v2）：bake_all_smooth_v2 循环时长严格不变（±5%），
只把姿态密度加密到 30fps 载波（frame_ms=33）——k 帧渐变由旧循环时长反推，
输出 <状态>_D{idx:03d}.png，并在 manifest 写 source_frames/source_loop_ms
标记烘焙源头；重烘焙永远从 source_frames 指向的原始 _f 姿态源出发，
绝不拿 _S/_D 插帧帧再插帧（防糊成鬼影叠影）。

转场补帧管线（任务三，已被压扁回弹方案接替）：extend_return_transition 给
once 状态追加「收招回 idle」渐变帧——取序列末帧与 idle.png 生成 12 张
（含首尾端点）<状态>_T{idx:03d}.png 追加到 frames 尾部，frame_ms 不变
（33ms 载波下约 0.4 秒）；重复执行先清旧 _T 帧再生成（幂等）；末帧本就是
idle 姿态时（如 _D 烘焙自带收招定格）直接复用 idle 图免逐帧混合。
该函数保留供回滚对照，主路径已换成下面的压扁回弹转场。

压扁回弹转场管线（transition-v2，主人拍板 2026-08）：bake_squash_return
在最大压扁瞬间完成姿态切换（皮筋动画经典手法，形状连续无叠影）——表演
末姿态 A 平滑压到 (sy 0.78, sx 1.18)（锚点=底部中心，向地板压），恰在
最大压扁帧无缝换装到 idle 的同比例压扁帧，再 ease_out_back 式经 1.12
过冲回弹落定为 idle；输出 <状态>_Q{idx:03d}.png，帧数随节拍折算
（33ms→30 帧、41ms→24 帧，仪式时长都约 1 秒），幂等清 _T/_Q 两代旧帧。

打气派对管线（任务二）：bake_cheer_party 把单图 cheer 整活成 45 帧常驻
搞笑循环（play=loop）——两快两慢 ±18° 挥旗（带小跳与挥臂弧线残影）→
蓄力猛压 0.75 弹过冲 1.15 → 原地 12 帧粗转一圈 → 顶点定格 3 帧+三颗
五角星爆开 → 落地回弹收招，输出 cheer_D{idx:03d}.png。

一键重烤入口：rebuild_all_animation_assets 按序执行压扁回弹转场
（shock/cry/dance）+ cheer 派对 + sleep 播放提速并写回 manifest；
python prep_assets.py --rebuild 直跑（幂等，可反复执行）。
"""
import json
import math
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

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


# ---------------------------------------------------------------- 30fps 密度烘焙管线（v2）
# 目标载波：30fps —— frame_ms 固定 33ms，循环时长不变、只加密姿态密度
TARGET_FPS = 30
V2_FRAME_MS = int(round(1000 / TARGET_FPS))          # = 33
# v2 产物命名标签：大写 D 与旧 _f（原始姿态）/_S（v1 插帧）/_F（全帧）区分
SMOOTH_TAG_V2 = "D"
# 收招余韵固定 2 帧（沿用 v1 参数：末帧与 idle 50% 融合 1 帧 + idle 定格 1 帧）
V2_TAIL_FRAMES = 2


def v2_transitions(old_loop_ms, source_count, tail: int = V2_TAIL_FRAMES) -> int:
    """反推相邻源帧之间要插的渐变帧数 k（整数化，下限 2）。

    预算：目标总帧数 = 旧循环时长(ms) / 1000 * TARGET_FPS；扣除 source_count
    个源帧与 tail 帧收招余韵后，均摊到 (source_count-1) 个段间四舍五入。
    保证 新序列帧数 x 33ms ≈ 旧循环时长 ±5% —— 时长不变，只提高密度。
    垃圾 old_loop_ms / 源帧不足 2 时一律回落 k=2。
    """
    n = int(source_count)
    if n < 2:
        return 2
    try:
        budget = float(old_loop_ms) / 1000.0 * TARGET_FPS - n - tail
    except (TypeError, ValueError):
        return 2
    return max(2, int(round(budget / (n - 1))))


def find_pose_sources(states_dir, name: str) -> list | None:
    """从磁盘找 <name>_f{i}.png 原始姿态源（从 0 连续编号）。

    这是旧 v1 插帧条目唯一的合法重烘源头；不足 2 帧返回 None。
    """
    rels, i = [], 0
    while (Path(states_dir) / f"{name}_f{i}.png").exists():
        rels.append(f"states/{name}_f{i}.png")
        i += 1
    return rels if len(rels) >= 2 else None


def resolve_bake_sources_v2(name: str, entry, states_dir) -> list | None:
    """解析一个状态的 v2 烘焙源（原始姿态 rel 路径列表）；不可烘返回 None。

    优先级：
    1. entry["source_frames"]（上一轮 v2 写下的源头标记）——幂等重跑；
    2. entry["frames"] 本身 <=8 帧（新鲜 GIF 抽稀 _f 档）——它就是源；
    3. frames 是旧插帧产物（_S/_D 系列）——回退磁盘原始 _f 姿态源，
       绝不拿插帧帧再插帧；
    其余（dance 等 _F 全帧档、帧数超限）明确不管。
    """
    declared = entry.get("source_frames")
    if isinstance(declared, (list, tuple)) and len(declared) >= 2:
        return [str(s).replace("\\", "/") for s in declared]
    frames = [str(f).replace("\\", "/") for f in entry.get("frames") or []]
    n = len(frames)
    if 0 < n <= SMOOTH_MAX_SOURCE_FRAMES:
        return frames
    tags = {Path(f).stem.rsplit("_", 1)[-1][:1] for f in frames}
    if n > SMOOTH_MAX_SOURCE_FRAMES and tags <= {"S", "D"}:
        return find_pose_sources(states_dir, name)
    return None


def bake_smooth_v2(state_entry, source_frames: list, states_dir,
                   old_loop_ms, tail_to=None) -> dict:
    """30fps 密度烘焙：循环时长严格不变，只把姿态密度加密到 30fps 载波。

    1. source_frames 必须是原始姿态的 rel 路径列表（_f 系列），逐张读入；
    2. k = v2_transitions(old_loop_ms, 帧数)，相邻源帧间插 k 张渐变帧；
    3. tail_to 给了 idle 图时末尾追加 2 帧收招余韵（50% 融合 + idle 定格，
       沿用 v1 参数）；
    4. 新帧逐张存 <state>_D{idx:03d}.png；
    5. 返回 manifest 片段：frames / frame_ms=33 / pingpong=True / play=once /
       return_to=idle / source_frames（标记烘焙源头，防二次插帧）/
       source_loop_ms（固定旧循环时长，保证幂等重跑 k 不漂移）。
    """
    src_rels = [str(s).replace("\\", "/") for s in source_frames or ()]
    if len(src_rels) < 2:
        raise ValueError("烘焙源至少要 2 帧原始姿态，拒绝插帧帧再插帧")
    src = [Image.open(Path(states_dir) / Path(rel).name).convert("RGBA")
           for rel in src_rels]
    k = v2_transitions(old_loop_ms, len(src))
    seq = [src[0]]
    for prev, nxt in zip(src, src[1:]):
        seq.extend(interpolate(prev, nxt, k))
        seq.append(nxt)
    # 收招余韵（沿用 v1 参数）：末帧与 idle 50% 融合 1 帧 + idle 原图定格
    if tail_to is not None:
        idle = Image.open(tail_to).convert("RGBA")
        last = seq[-1]
        seq.extend(interpolate(last, idle, V2_TAIL_FRAMES - 1))
        seq.append(idle.resize(last.size) if idle.size != last.size else idle)
    states_dir = Path(states_dir)
    states_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(str(state_entry.get("file") or "state")).stem
    rels = []
    for i, fr in enumerate(seq):
        name = f"{stem}_{SMOOTH_TAG_V2}{i:03d}.png"
        fr.save(states_dir / name)
        rels.append(f"states/{name}")
    try:
        loop_ms = int(round(float(old_loop_ms)))
    except (TypeError, ValueError):
        loop_ms = len(seq) * V2_FRAME_MS
    return {
        "frames": rels,
        "frame_ms": V2_FRAME_MS,
        "pingpong": True,
        "play": "once",
        "return_to": "idle",
        "source_frames": src_rels,
        "source_loop_ms": loop_ms,
    }


def bake_all_smooth_v2(manifest_path: Path = MANIFEST, states_dir: Path = OUT,
                       idle_img=None) -> dict:
    """批量 30fps 密度烘焙并写回 manifest（幂等：重跑字节级一致）。

    只处理能解析出原始姿态源（<=8 帧）的多帧状态；dance 等 _F 全帧档
    （长度 >8 且非插帧产物）明确跳过。old_loop_ms 优先取上一轮写下的
    source_loop_ms，否则按现条目 frames x frame_ms 结算——循环时长不变
    原则自动保留 eat/sleep 的慢速语义。单状态素材缺失只警告跳过，
    绝不拖垮整批。返回 {"状态名": 补丁片段}。
    """
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    patch = {}
    for name, entry in (data.get("states") or {}).items():
        frames = entry.get("frames")
        if not isinstance(frames, (list, tuple)) or not frames:
            continue
        sources = resolve_bake_sources_v2(name, entry, states_dir)
        if not sources or len(sources) > SMOOTH_MAX_SOURCE_FRAMES:
            print(f"[v2烘焙] {name}: 跳过（无 <=8 帧原始姿态源，全帧档不归 v2 管）")
            continue
        old_loop = entry.get("source_loop_ms")
        if not isinstance(old_loop, (int, float)) or old_loop <= 0:
            try:
                old_loop = len(frames) * int(entry.get("frame_ms")
                                             or DEFAULT_FRAME_MS)
            except (TypeError, ValueError):
                old_loop = len(frames) * DEFAULT_FRAME_MS
        try:
            patch[name] = bake_smooth_v2(entry, sources, states_dir,
                                         old_loop, tail_to=idle_img)
        except Exception as exc:
            print(f"[v2烘焙] {name}: 跳过（{exc}）")
            continue
        frag = patch[name]
        print(f"[v2烘焙] {name}: 源 {len(sources)} 帧 k="
              f"{v2_transitions(old_loop, len(sources))} -> "
              f"{len(frag['frames'])} 帧 x {frag['frame_ms']}ms="
              f"{len(frag['frames']) * frag['frame_ms']}ms"
              f"（旧循环 {int(old_loop)}ms）")
    if patch:
        merge_manifest_entries(patch, manifest_path)
    return patch


# ---------------------------------------------------------------- 转场补帧管线
# 转场帧命名标签：大写 T 与旧 _f（抽稀）/_F（全帧）/_S（v1）/_D（v2）区分
TRANSITION_TAG = "T"
# 缺省转场帧数：12 张（含首尾端点），30fps 载波 33ms 下约 0.4 秒
RETURN_TRANSITION_FRAMES = 12


def clear_transition_frames(states_dir, name: str) -> int:
    """清掉某状态旧的 _T 转场帧文件，返回删除张数（幂等重跑的前置工序）。"""
    n = 0
    for old in sorted(Path(states_dir).glob(
            f"{name}_{TRANSITION_TAG}[0-9][0-9][0-9].png")):
        old.unlink()
        n += 1
    return n


def extend_return_transition(states_dir, manifest_path, targets,
                             frames: int = RETURN_TRANSITION_FRAMES) -> dict:
    """给 once 状态追加「收招回 idle」转场帧，同步写回 manifest。

    对 targets 里每个条目：
    1. 只处理 manifest["states"] 里 play=once 且带帧序列的多帧状态——
       禁用区条目 / 缺条目 / 常驻态（loop，如 sleep）一律警告跳过；
    2. 幂等清场：先删磁盘旧 _T 帧、再把 frames 里的旧 _T 引用剔除；
    3. 取序列末帧与 idle.png，生成 frames 张渐变帧（含首尾端点）追加到
       frames 列表尾部，命名 <状态>_T{idx:03d}.png，frame_ms 不变——
       33ms 载波下 12 帧约 0.4 秒，表演收尾不再硬切回待机；
    4. 末帧本就是 idle 姿态时（_D 烘焙自带收招定格的 shock/cry 等）
       直接复用 idle 图，不做无谓的逐帧混合；
    5. 返回 {"状态名": 新frames列表}；没有任何目标命中就不写 manifest。
    """
    states_dir = Path(states_dir)
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    states = data.get("states") or {}
    try:
        n_frames = max(2, int(frames))   # 少于 2 张无法「含首尾」，钳回 2
    except (TypeError, ValueError):
        n_frames = RETURN_TRANSITION_FRAMES
    idle = Image.open(states_dir / "idle.png").convert("RGBA")
    done: dict[str, list] = {}
    for name in targets:
        entry = states.get(name)
        rels = (entry or {}).get("frames")
        if not isinstance(rels, (list, tuple)) or not rels:
            print(f"[转场] {name}: 跳过（条目缺失/已禁用/没有帧序列）")
            continue
        if str(entry.get("play") or "loop").strip().lower() != "once":
            print(f"[转场] {name}: 跳过（非 once 常驻态无收尾）")
            continue
        base_rels = [str(r).replace("\\", "/") for r in rels
                     if not Path(str(r)).name.startswith(
                         f"{name}_{TRANSITION_TAG}")]
        if not base_rels:
            print(f"[转场] {name}: 跳过（清掉旧 _T 引用后没有实质帧）")
            continue
        clear_transition_frames(states_dir, name)
        last = Image.open(states_dir / Path(base_rels[-1]).name).convert("RGBA")
        target = idle.resize(last.size) if idle.size != last.size else idle
        # 注意：RGBA 的 difference().getbbox() 会被全零 alpha 通道掩盖
        # （RGB 明明不同也返回 None），判定「末帧==idle」必须比 RGB 通道
        if ImageChops.difference(
                last.convert("RGB"), target.convert("RGB")).getbbox() is None:
            # 末帧就是 idle 姿态：直接复用 idle 图，免逐帧混合
            seq = [target.copy() for _ in range(n_frames)]
        else:
            seq = [Image.blend(last, target, i / (n_frames - 1))
                   for i in range(n_frames)]
        new_rels = list(base_rels)
        for i, fr in enumerate(seq):
            fname = f"{name}_{TRANSITION_TAG}{i:03d}.png"
            fr.save(states_dir / fname)
            new_rels.append(f"states/{fname}")
        entry["frames"] = new_rels
        done[name] = new_rels
        try:
            ms = int(entry.get("frame_ms") or 0)
        except (TypeError, ValueError):
            ms = 0
        print(f"[转场] {name}: 追加 {len(seq)} 张 _T 帧 "
              f"(+{len(seq) * ms}ms) -> {len(new_rels)} 帧")
    if done:
        manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    return done


# ---------------------------------------------------------------- 压扁回弹转场（transition-v2 主路径）
# 转场帧命名标签：大写 Q 与旧 _f/_F/_S/_D/_T 区分；_T 与旧 _Q 都是历史遗留
TRANSITION_TAG_V2 = "Q"
LEGACY_TRANSITION_TAGS = ("T", TRANSITION_TAG_V2)
SQUASH_TARGETS = ("shock", "cry", "dance")   # 主人拍板的三态
SQUASH_SY_MIN = 0.78       # 第一幕终点：压到 78%（两图轮廓差异最小处）
SQUASH_SX_MAX = 1.18       # 压扁同时横向鼓出，保体积
SQUASH_OVERSHOOT = 1.12    # 第二幕过冲顶点（>1.08 可测）
SQUASH_ACT1_SHARE = 0.4    # 第一幕帧数占比（其余归回弹幕）
SQUASH_MIN_FRAMES = 8      # 帧数下限：再短就没有仪式感了
# sleep 播放提速（主人拍板）：帧图不动，纯节拍 140→90ms
SLEEP_SPEED_MS = 90


def _ease_out_back_peak(peak: float) -> float:
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


def _squash_pose(im: Image.Image, sx: float, sy: float) -> Image.Image:
    """整幅画布按 (sx, sy) 缩放，锚点=底部中心——压扁向地板方向，不向中心缩。"""
    if abs(sx - 1.0) < 1e-9 and abs(sy - 1.0) < 1e-9:
        return im.copy()   # 恒等姿态直接原样拷贝（末帧==idle 的字节级保证）
    w, h = im.size
    nw = max(1, int(round(w * sx)))
    nh = max(1, int(round(h * sy)))
    resized = im.resize((nw, nh), Image.BICUBIC)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(resized, ((w - nw) // 2, h - nh))
    return canvas


def bake_squash_return(state_entry, states_dir, idle_img,
                       total_frames: int = 30, name: str | None = None) -> dict:
    """压扁回弹转场：在最大压扁瞬间完成 A(表演末姿态)→B(idle) 姿态切换。

    三幕（total_frames 默认 30 @33ms ≈ 1 秒仪式）：
    1. 前 40% 帧数把 A 从 1.0 平滑压到 (sy 0.78, sx 1.18)，锚点=底部中心；
    2. 恰在最大压扁帧无缝换装到 B 的同比例压扁帧——两图都压到 78% 时
       轮廓差异最小，切换无感、零叠影；
    3. 后 60% 帧数 ease_out_back 式从 0.78 经 1.12 过冲回弹落定为 B 原图。
    幂等：先清该状态旧转场帧（_T 与 _Q 两代都清）再生成 _Q 序列；
    frame_ms 沿用条目原值（dance 的 41ms 档传 total_frames=24 同样约 1 秒）。
    返回 manifest 补丁片段（frames=表演帧+新转场帧的完整列表）。
    """
    states_dir = Path(states_dir)
    # 状态名：显式传入优先；否则从 file 去掉姿态标签后缀派生（shock_D000→shock）
    stem = name or Path(str(state_entry.get("file") or "state")).stem \
        .rsplit("_", 1)[0]
    legacy_prefixes = tuple(f"{stem}_{t}" for t in LEGACY_TRANSITION_TAGS)
    base_rels = [str(r).replace("\\", "/")
                 for r in state_entry.get("frames") or ()
                 if not Path(str(r)).name.startswith(legacy_prefixes)]
    if not base_rels:
        raise ValueError("清掉旧转场帧引用后没有实质表演帧，无法烘焙转场")
    a = Image.open(states_dir / Path(base_rels[-1]).name).convert("RGBA")
    idle = idle_img if isinstance(idle_img, Image.Image) else Image.open(idle_img)
    idle = idle.convert("RGBA")
    b = idle.resize(a.size, Image.BICUBIC) if idle.size != a.size \
        else idle.copy()
    n = max(SQUASH_MIN_FRAMES, int(total_frames))
    n1 = max(2, int(round(n * SQUASH_ACT1_SHARE)))
    n2 = n - n1
    # 归一化过冲峰值：(1.12-0.78)/(1.0-0.78) ≈ 1.545，sy 峰值恰为 1.12
    norm_sx = (SQUASH_OVERSHOOT - SQUASH_SY_MIN) / (1.0 - SQUASH_SY_MIN)
    s_sx = _ease_out_back_peak(norm_sx)
    # 竖向余量自适应：B 顶上有余量才放行完整 1.12 过冲；满画布紧裁剪的
    # 素材把竖向过冲封顶到 1.0（绝不裁头），富余的回弹能量转由横向
    # 细拉（sx 依旧按完整峰值回弹）表达——橡胶手感不丢、形状永不越界
    b_box = b.getchannel("A").getbbox() or (0, 0, *b.size)
    headroom = max(0.0, b_box[1] / max(1, b.size[1]))
    peak_sy = min(SQUASH_OVERSHOOT, 1.0 + headroom)
    s_sy = _ease_out_back_peak((peak_sy - SQUASH_SY_MIN)
                               / (1.0 - SQUASH_SY_MIN))
    seq = []
    for i in range(n1):            # 第一幕：A 蓄力平滑压扁（ease-in-quad，
        u = i / (n1 - 1)           # 末帧精确压满 0.78，让换装两侧零比例错位）
        seq.append(_squash_pose(
            a, 1.0 + (SQUASH_SX_MAX - 1.0) * u * u,
            1.0 + (SQUASH_SY_MIN - 1.0) * u * u))
    seq.append(_squash_pose(b, SQUASH_SX_MAX, SQUASH_SY_MIN))  # 换装点
    for j in range(1, n2):         # 第二幕：B 过冲回弹落定（ease_out_back）
        u = j / (n2 - 1)
        seq.append(_squash_pose(
            b, SQUASH_SX_MAX + (1.0 - SQUASH_SX_MAX) * _back_ease(u, s_sx),
            SQUASH_SY_MIN + (1.0 - SQUASH_SY_MIN) * _back_ease(u, s_sy)))
    # 幂等清场：_T（渐变转场）与 _Q（上一轮压扁转场）两种历史命名都清
    for tag in LEGACY_TRANSITION_TAGS:
        for old in sorted(states_dir.glob(f"{stem}_{tag}[0-9][0-9][0-9].png")):
            old.unlink()
    rels = list(base_rels)
    for i, fr in enumerate(seq):
        name = f"{stem}_{TRANSITION_TAG_V2}{i:03d}.png"
        fr.save(states_dir / name)
        rels.append(f"states/{name}")
    try:
        ms = int(state_entry.get("frame_ms") or V2_FRAME_MS)
    except (TypeError, ValueError):
        ms = V2_FRAME_MS
    return {"frames": rels, "frame_ms": ms, "play": "once",
            "return_to": "idle", "transition": "squash_return"}


def bake_all_squash_returns(states_dir: Path = OUT,
                            manifest_path: Path = MANIFEST,
                            targets=SQUASH_TARGETS) -> dict:
    """批量压扁回弹转场并写回 manifest（幂等，替换旧 _T 渐变转场主路径）。

    只处理活动区 play=once 且带帧序列的目标状态；转场帧数随条目节拍折算
    （33ms→30 帧、41ms→24 帧，仪式时长都约 1 秒）。单状态素材缺失只警告
    跳过。返回 {"状态名": 完整 frames 列表}。
    """
    states_dir = Path(states_dir)
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    states = data.get("states") or {}
    patch = {}
    for name in targets:
        entry = states.get(name)
        rels = (entry or {}).get("frames")
        if not isinstance(rels, (list, tuple)) or not rels:
            print(f"[压扁转场] {name}: 跳过（条目缺失/已禁用/没有帧序列）")
            continue
        if str(entry.get("play") or "loop").strip().lower() != "once":
            print(f"[压扁转场] {name}: 跳过（非 once 常驻态无收招仪式）")
            continue
        try:
            ms = int(entry.get("frame_ms") or V2_FRAME_MS)
        except (TypeError, ValueError):
            ms = V2_FRAME_MS
        total = max(SQUASH_MIN_FRAMES, int(round(1000.0 / max(1, ms))))
        try:
            patch[name] = bake_squash_return(entry, states_dir,
                                             states_dir / "idle.png",
                                             total_frames=total, name=name)
        except Exception as exc:
            print(f"[压扁转场] {name}: 跳过（{exc}）")
            continue
        base_n = len(rels) - sum(
            1 for r in rels if Path(str(r)).name.startswith(
                (f"{name}_T", f"{name}_Q")))
        print(f"[压扁转场] {name}: 表演 {base_n} 帧 + 追加 "
              f"{len(patch[name]['frames']) - base_n} 张 _Q 帧 @ {ms}ms")
    if patch:
        merge_manifest_entries(patch, manifest_path)
    return {name: p["frames"] for name, p in patch.items()}


# ---------------------------------------------------------------- 打气派对管线（cheer 常驻搞笑循环）
CHEER_PARTY_FRAMES = 45        # 45 帧 @33ms ≈ 1.5 秒循环
CHEER_WAVE_FRAMES = 18         # 第一幕：两快(3+3)两慢(6+6)挥旗
CHEER_TILT_DEG = 18.0          # 挥旗幅度（主人验收加码 ±14°→±18°）
CHEER_SQUASH_SY = 0.75         # 蓄力猛压
CHEER_OVERSHOOT_SY = 1.15      # 弹起过冲（蓄力搞笑）
CHEER_SPIN_FRAMES = 12         # 原地粗转一圈（卡顿感反而喜感）
CHEER_APEX_FRAMES = 3          # 顶点定格帧数（星星向外爆开）
CHEER_HOP_PX = 10              # 挥旗/顶点伴随的底部小跳幅度
# 星星配色复用 cutout_anim 的五角星风格（就地实现，不 import）
CHEER_STAR_FILL = (255, 216, 77, 235)
CHEER_STAR_LINE = (214, 158, 0, 255)


def _cheer_poses(fps_ms: int = 33) -> list:
    """45 帧连招的纯姿态表（无 IO，测试直测）：五幕整活连招。

    每帧 pose: angle 倾角 / sx, sy 缩放 / dy 垂直位移（负=跳起）/
    arc 挥臂弧线残影方向（R/L/None）/ arc_alpha 残影透明度（100~180）/
    stars 星星爆开进度（None 或 1..3）。
    """
    poses = []
    # 第一幕：两快两慢挥旗（±18°，卡点喜感），伴随底部小跳 + 挥臂弧线残影
    for side, count in (("R", 3), ("L", 3), ("R", 6), ("L", 6)):
        for i in range(count):
            poses.append({
                "angle": CHEER_TILT_DEG if side == "R" else -CHEER_TILT_DEG,
                "sx": 1.0, "sy": 1.0,
                "dy": -CHEER_HOP_PX if i % 2 == 1 else 0,
                "arc": side,
                "arc_alpha": 180 - round(80 * i / max(1, count - 1)),
                "stars": None,
            })
    # 第二幕：蓄力猛压 0.75 → 弹过冲 1.15 → 回弹落定（sx 镜像保体积）
    for sy in (0.92, 0.82, CHEER_SQUASH_SY,
               CHEER_OVERSHOOT_SY, 1.06, 1.0):
        poses.append({"angle": 0.0, "sx": round(1.0 + (1.0 - sy) * 0.9, 4),
                      "sy": sy, "dy": 0, "arc": None, "arc_alpha": 0,
                      "stars": None})
    # 第三幕：原地自转一圈（30°/帧 粗转，卡顿感反而喜感）
    for k in range(1, CHEER_SPIN_FRAMES + 1):
        poses.append({"angle": 30.0 * k, "sx": 1.0, "sy": 1.0, "dy": 0,
                      "arc": None, "arc_alpha": 0, "stars": None})
    # 第四幕：顶点定格 3 帧 + 三颗五角星向外爆开
    for k in range(1, CHEER_APEX_FRAMES + 1):
        poses.append({"angle": 0.0,
                      "sx": round(1.0 + (1.0 - CHEER_OVERSHOOT_SY) * 0.9, 4),
                      "sy": CHEER_OVERSHOOT_SY, "dy": -CHEER_HOP_PX,
                      "arc": None, "arc_alpha": 0, "stars": k})
    # 第五幕：落地回弹收招，落定中性姿态后循环回第一帧
    for sy in (1.08, 1.0, 0.94, 1.02, 1.0, 1.0):
        poses.append({"angle": 0.0, "sx": round(1.0 + (1.0 - sy) * 0.9, 4),
                      "sy": sy, "dy": 0, "arc": None, "arc_alpha": 0,
                      "stars": None})
    assert len(poses) == CHEER_PARTY_FRAMES
    return poses


def _cheer_star_geom(stage: int, base_w: int) -> tuple:
    """星星爆开进度（1..3）-> (离心距离, 星星半径)，随进度向外长大。"""
    return int((0.08 + 0.05 * stage) * base_w), 8 + 5 * stage


def _star_points(cx: float, cy: float, r: float,
                 rot: float = -math.pi / 2) -> list:
    """五角星十顶点（与 cutout_anim 同风格，就地实现简版）。"""
    pts = []
    for i in range(10):
        ang = rot + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.42
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    return pts


def _draw_star_burst(frame: Image.Image, cx: float, top_y: float,
                     stage: int, base_w: int) -> None:
    """三颗五角星在头顶扇形（左上/正上/右上）向外爆开。"""
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    dist, r = _cheer_star_geom(stage, base_w)
    for deg in (152, 90, 28):
        a = math.radians(deg)
        d.polygon(_star_points(cx + dist * math.cos(a),
                               top_y - dist * math.sin(a), r),
                  fill=CHEER_STAR_FILL, outline=CHEER_STAR_LINE)
    frame.alpha_composite(overlay)


def _draw_swing_arc(frame: Image.Image, cx: float, top_y: float,
                    sprite_h: float, side: str, alpha: int) -> None:
    """旗区挥臂弧线残影：弧角跟随倾摆方向，双层弧制造挥出轨迹（100~180 透明度）。

    圆心取头顶、半径半身——弧线扫在身体上方的透明区；再用「帧透明区反转」
    做遮罩，残影只落在体外，不糊脸不掉色。
    """
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    # PIL 角度口径：0°=3 点钟方向、顺时针（y 向下）；-90°=正上方
    center = -70 if side == "R" else -110
    cy = top_y + int(sprite_h * 0.06)
    spread = 28
    for radius_k, width, ang_off, alpha_off in ((0.55, 9, 0, 0),
                                                (0.47, 6, -10 if side == "R"
                                                 else 10, 60)):
        r = int(sprite_h * radius_k)
        box = [cx - r, cy - r, cx + r, cy + r]
        d.arc(box, center - spread + ang_off, center + spread + ang_off,
              fill=(255, 255, 255, max(100, int(alpha) - alpha_off)),
              width=width)
    # 遮掉身体不透明区：残影 alpha 与「帧透明区的反转」相乘，弧线只在体外显形
    outside = frame.getchannel("A").point(lambda v: 255 - v)
    overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), outside))
    frame.alpha_composite(overlay)


def _cheer_canvas(base_size, poses) -> tuple:
    """按连招最大形变反推统一画布：(宽, 高, 地板 y)。所有帧同画布防抖动。"""
    w, h = base_size
    max_w = max_h = 0.0
    max_hop = 0
    star_head = 0
    for p in poses:
        a = math.radians(abs(p["angle"]))
        rw = (w * math.cos(a) + h * math.sin(a)) * p["sx"]
        rh = (w * math.sin(a) + h * math.cos(a)) * p["sy"]
        max_w, max_h = max(max_w, rw), max(max_h, rh)
        max_hop = max(max_hop, -p["dy"])
        if p["stars"]:
            dist, r = _cheer_star_geom(p["stars"], w)
            star_head = max(star_head, dist + r + 10)
    margin = 6
    cw = int(math.ceil(max_w)) + margin * 2
    ch = int(math.ceil(max_h)) + max_hop + star_head + margin
    return cw, ch, ch - margin


def _render_cheer_frame(base: Image.Image, pose: dict, canvas: tuple) -> Image.Image:
    """按姿态表渲染一帧：缩放->旋转->底部中心上画布->弧线残影->星星爆开。"""
    cw, ch, floor_y = canvas
    frame = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    w, h = base.size
    sprite = base
    if abs(pose["sx"] - 1.0) > 1e-9 or abs(pose["sy"] - 1.0) > 1e-9:
        sprite = base.resize((max(1, round(w * pose["sx"])),
                              max(1, round(h * pose["sy"]))), Image.BICUBIC)
    if abs(pose["angle"]) > 1e-9:
        sprite = sprite.rotate(pose["angle"], expand=True,
                               resample=Image.BICUBIC)
    sw, sh = sprite.size
    px = (cw - sw) // 2
    py = floor_y - sh + int(pose["dy"])
    frame.paste(sprite, (px, py), sprite)
    if pose["arc"]:
        _draw_swing_arc(frame, px + sw // 2, py, sh,
                        pose["arc"], pose["arc_alpha"])
    if pose["stars"]:
        _draw_star_burst(frame, px + sw // 2, py - 6, pose["stars"], w)
    return frame


def bake_cheer_party(base: Image.Image, fps_ms: int = 33,
                     out_dir: Path = OUT) -> dict:
    """把 cheer 底图整活成 45 帧常驻搞笑循环，返回 manifest 补丁片段。

    连招：两快两慢 ±18° 挥旗（小跳+挥臂弧线残影）→ 蓄力猛压 0.75 弹
    过冲 1.15 → 原地 12 帧粗转一圈 → 顶点定格 3 帧+三颗五角星爆开 →
    落地回弹收招，循环回第 1 帧。输出 cheer_D{idx:03d}.png（先清旧帧，
    幂等重烤字节一致）；条目改 frames+frame_ms=33+play=loop+pingpong=False。
    """
    base = base.convert("RGBA")
    poses = _cheer_poses(fps_ms)
    try:
        ms = max(10, int(fps_ms))
    except (TypeError, ValueError):
        ms = 33
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in sorted(out_dir.glob(
            f"cheer_{SMOOTH_TAG_V2}[0-9][0-9][0-9].png")):
        old.unlink()
    canvas = _cheer_canvas(base.size, poses)
    rels = []
    for i, pose in enumerate(poses):
        name = f"cheer_{SMOOTH_TAG_V2}{i:03d}.png"
        _render_cheer_frame(base, pose, canvas).save(out_dir / name)
        rels.append(f"states/{name}")
    print(f"[打气派对] cheer: {len(rels)} 帧 @ {ms}ms 常驻循环 "
          f"画布 {canvas[0]}x{canvas[1]}")
    return {"frames": rels, "frame_ms": ms, "play": "loop",
            "pingpong": False}


def rebuild_all_animation_assets(manifest_path: Path = MANIFEST,
                                 states_dir: Path = OUT,
                                 idle_img=None) -> dict:
    """一键重烤全部程序合成动画资产并写回 manifest（幂等，可反复执行）。

    按序执行：压扁回弹转场（shock/cry/dance，替换旧 _T/_Q 两代转场）→
    cheer 打气派对 45 帧循环 → sleep 播放提速（frame_ms→90，帧图不动）。
    全部产物从源帧确定性重建；主代理合并后即可在主仓一键重烤。
    """
    manifest_path = Path(manifest_path)
    states_dir = Path(states_dir)
    idle_img = idle_img or (states_dir / "idle.png")
    squash = bake_all_squash_returns(states_dir, manifest_path)
    patch = {}
    cheer_src = states_dir / "cheer.png"
    if cheer_src.exists():
        patch["cheer"] = bake_cheer_party(Image.open(cheer_src),
                                          out_dir=states_dir)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    sleep_entry = (data.get("states") or {}).get("sleep")
    if isinstance(sleep_entry, dict) and sleep_entry.get("frames"):
        patch["sleep"] = {"frame_ms": SLEEP_SPEED_MS}
    if patch:
        merge_manifest_entries(patch, manifest_path)
    return {
        "squash": {k: len(v) for k, v in squash.items()},
        "cheer_frames": len(patch.get("cheer", {}).get("frames") or ()),
        "sleep_frame_ms": patch.get("sleep", {}).get("frame_ms"),
    }


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


def _run_legacy_raw_pipeline():
    """旧默认管线：assets/raw 全量抠图/切片并合并 manifest（--rebuild 之外的路径）。"""
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


if __name__ == "__main__":
    # 一键重烤入口：python prep_assets.py --rebuild
    # （压扁回弹转场 + cheer 派对 + sleep 提速，从源帧确定性重建，幂等）
    if "--rebuild" in sys.argv[1:]:
        print("一键重烤动画资产:", rebuild_all_animation_assets())
    else:
        _run_legacy_raw_pipeline()
