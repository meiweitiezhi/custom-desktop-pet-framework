"""Qt 宿主：负责渲染与输入，业务决策全部交给 Driver。

窗口只是一个"渲染器"：
  收到事件 -> dispatch() -> Driver.react() -> 命令列表
  apply() 只认三种命令：SetState / Say / Hop（bus.py 定义）
"""
import json
import math
import configparser
import os
import queue
import sys
import tempfile
import threading
import time
import pathlib
from datetime import datetime

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap, QTransform
from PySide6.QtWidgets import (QApplication, QLabel, QMenu, QSystemTrayIcon,
                               QWidget)

from . import bus
from . import config as cfgmod
from . import idle_policy
from . import paths
from .action_player import ActionPlayer
from .animator_core import DEFAULT_FRAME_MS
from .bridge import BridgeServer
from .click_flow import (DOUBLE_CLICK_MS, ClickResolver, resolve_click_sfx,
                         should_perform)
from .drivers import get_driver
from .extensions.growth import GrowthTracker, level_for
from .music_player import MusicPlayer, resolve_music
from .song_flow import dance_loop_spec, resolve_dance6_bgm, should_ignore_click
from .streaks import BuildStreak

ASSETS = paths.ASSETS            # 只读素材（frozen 时在解包目录）
RUNTIME_PATH = paths.RUNTIME_PATH  # 可写位置（frozen 时在 exe 旁边）

TICK_MS = 33          # 渲染节拍缺省 30fps 载波；实际由 [pet] tick_ms 决定
TICK_MS_MIN, TICK_MS_MAX = 16, 100


def resolve_tick_ms(cp) -> int:
    """渲染节拍收编：config.ini [pet] tick_ms，缺省 33（30fps 载波）。

    合法区间钳制 16~100（省电可改 66）；缺段/缺键/乱码一律回落缺省 33。
    """
    try:
        value = int(str(cp.get("pet", "tick_ms", fallback="33")).strip())
    except (configparser.Error, TypeError, ValueError):
        return 33
    return max(TICK_MS_MIN, min(TICK_MS_MAX, value))

# hook 事件 -> 音效名（一条事件只挂一响，集中在这里，别处不再叠加）
HOOK_SFX = {"error": "wah", "praise": "kiss", "kiss": "kiss"}

# 托盘状态子菜单的中文名；与 bus.STATES 一一对应（tests 有防漂移检查）
STATE_ZH = {
    "idle": "发呆", "cheer": "打气", "eat": "干饭", "sleep": "睡觉",
    "laugh": "笑哭", "shock": "惊讶", "angry": "生气", "dance": "扭舞",
    "cry": "哭唧唧", "hide": "缩帽躲", "love": "比小心心",
    "alien": "外星吸人", "blushmax": "羞耻爆炸",
    "vroom": "骑摩托",
}

# 右键动作菜单的两组状态词条；系统组条目在构建器里现场生成。
# 只渲染 self.states 里已加载出图的状态，缺图自动缺席隐藏。
# 五态精简（主人拍板 2026-08）：laugh/eat/angry、hide/love/alien/blushmax
# 已随 manifest["_disabled_states"] 入禁用区，词条注释保留、随时可恢复。
MENU_EMOTION = ("idle", "cheer", "sleep", "shock", "dance")
# MENU_EMOTION = ("idle", "cheer", "eat", "sleep",
#                 "laugh", "shock", "angry", "dance")
MENU_FUN = ("vroom",)
# MENU_FUN = ("cry", "hide", "love", "alien", "blushmax", "alien_suck")

# 专属演出动作：只在 manifest 登记与动作菜单出现，不进 bus.STATES 词表
# （不是表情状态，SetState 不认；演出一律走 play_action 点播）。
# alien_suck 现居 manifest 禁用区，声明保留以维持 manifest 防漂移检查。
ACTION_ONLY = ("alien_suck",)
ACTION_ZH = {"alien_suck": "UFO 吸入"}

# 程序剪纸六拍舞（dance6）：情绪组常驻点播词条。同属「非表情状态」，
# 走专属 play_six_beat（循环舞 + 配乐放一遍），不进 STATE_ZH/bus.STATES。
SIX_BEAT_STATE = "dance6"
SIX_BEAT_ZH = "六拍舞"

# 左键单击专属演出参数：固定句、shock 尾部定格时长（宿主接管，不走驱动）
CLICK_TEASE = "不要戳我！！！！"
SHOCK_HOLD_TAIL_MS = 1200   # 与 manifest v4 shock.hold_seconds=1.2 同源同值；
                            # 显式传参只是沿用旧接口，正主是 manifest 字段
# 【禁用区】旧单击 hide 定格参数随 hide 态下线，注释保留：
# HIDE_HOLD_TAIL_MS = 1500


def defer_if_playing(pending, playing, wanted):
    """SetState 让路裁决（纯函数）：表演中排队最后请求，空闲立即生效。

    返回 (本轮要应用的 target 或 None, 新的候补位)：
    - 表演中 -> 不打断演出，候补位直接被后来的请求覆盖；
    - 空闲   -> 新请求立刻上屏，顺手清掉过期的候补陈账。
    """
    if playing:
        return None, wanted
    return wanted, None


def action_overtime(elapsed, max_seconds) -> bool:
    """动作保险丝的纯判定（宿主独立秒表的第二道闸）：超时了吗？

    max_seconds<=0、缺字段或乱码一律视为不设防（False）——loop 常驻档
    和没写 v4 字段的旧条目绝不能被误杀。判定只有一行直白的比较。
    """
    try:
        limit = float(max_seconds)
    except (TypeError, ValueError):
        return False
    return limit > 0 and float(elapsed) > limit


# ---------------------------------------------------------------- 素材加载
def active_states(manifest: dict) -> dict:
    """manifest 的活动状态表：只认 "states" 键，顶层下划线保留键显式忽略。

    下划线开头的顶层键（如 "_disabled_states" 禁用区、未来的元数据键）是
    数据搁架，绝不参与加载与触发——把条目搬回 "states" 即视为恢复上线。
    """
    return {
        k: v for k, v in manifest.items() if not str(k).startswith("_")
    }.get("states") or {}


def entry_paths(name: str, spec: dict) -> list:
    """manifest 条目涉及的图片相对路径清单（双 schema）。

    多帧模式优先：有非空 "frames" 列表就整组返回；否则回落单图
    "file" 字段（缺省补 states/<名>.png，保持旧 manifest 兼容）。
    """
    frames = spec.get("frames")
    if isinstance(frames, (list, tuple)) and frames:
        return [str(f).replace("\\", "/") for f in frames]
    return [str(spec.get("file") or f"states/{name}.png").replace("\\", "/")]


def collect_missing(states: dict, available_files) -> tuple:
    """纯函数：算出 manifest 里哪些状态缺图，供 load_states 决定降级策略。

    参数：
      states          manifest["states"] 形如 {"idle": {"file": "states/idle.png"}}
                      或多帧 {"dance": {"frames": [...], "frame_ms": 120}}
      available_files 可迭代的相对 assets/ 的现存文件路径
    返回：
      (missing_core, missing_optional) —— 核心四态缺图必须退出，
      可选新态缺图只警告跳过（用户稍后补图即可解锁）。多帧条目按整组
      frames 全部到位才算不缺。
    """
    have = {str(f).replace("\\", "/") for f in available_files}
    core, optional = [], []
    for name, spec in states.items():
        rels = [r for r in entry_paths(name, spec) if r]
        if rels and all(r in have for r in rels):
            continue
        (core if name in bus.CORE_STATES else optional).append(name)
    return core, optional


def load_states(display_size: int) -> dict:
    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    entries = active_states(manifest)   # 只读 states；"_disabled_states" 等下划线键一律忽略
    available = {p.relative_to(ASSETS).as_posix()
                 for p in ASSETS.rglob("*") if p.is_file()}
    missing_core, missing_optional = collect_missing(entries, available)
    if missing_core:
        raise SystemExit(
            f"缺核心素材: {', '.join(missing_core)}\n"
            "角色表情包图片受版权保护，仓库里不放图（红线）。\n"
            f"请把你的原图按状态名放到 assets/raw/<状态名>.png，"
            "然后运行 python prep_assets.py 自动抠图。")
    if missing_optional:
        print(f"[团子] 可选表情暂缺图片，先跳过（补好 assets/raw/<名>.png "
              f"再跑 prep_assets.py 即可解锁）：{', '.join(missing_optional)}")

    states = {}
    for name, spec in entries.items():
        rels = [r for r in entry_paths(name, spec) if r]
        if not rels or any(r not in available for r in rels):
            continue  # 缺图项上面已统一警告过，这里安静跳过
        scaled = []
        broken_rel = None
        for rel in rels:
            pm = QPixmap(str(ASSETS / rel))
            if pm.isNull():
                # 文件存在但解码失败等异常情况：可选态同样只降级不崩
                broken_rel = rel
                break
            scaled.append(pm.scaled(display_size, display_size,
                                    Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation))
        if broken_rel is not None:
            if name in bus.CORE_STATES:
                raise SystemExit(f"素材损坏: assets/{broken_rel}\n"
                                 "请重新运行 python prep_assets.py 生成。")
            print(f"[团子] 表情 {name} 的图片读取失败，已跳过")
            continue
        entry = {
            "pixmap": scaled[0],
            "amp": float(spec.get("bob_amp", 3)),
            "period": max(0.3, spec.get("period_ms", 2000) / 1000.0),
            "tilt": float(spec.get("tilt_deg", 0)),
        }
        if len(scaled) > 1 or "frames" in spec:
            # 多帧模式：frames 为换帧序列，frame_ms 为节拍基准（双档换算）
            try:
                frame_ms = max(30, int(spec.get("frame_ms",
                                                DEFAULT_FRAME_MS)))
            except (TypeError, ValueError):
                frame_ms = DEFAULT_FRAME_MS
            entry["frames"] = scaled
            entry["frame_ms"] = frame_ms
            if spec.get("pingpong"):
                # 乒乓档：交给 ActionPlayer（loop 往返；once 播到尾即止）
                entry["pingpong"] = True
            # v4/v5 动作字段透传：play_action / ActionPlayer / 保险丝都吃这份拷贝
            for key in ("play", "return_to", "hold_seconds", "max_seconds",
                        "transition_frames", "rounds", "perform_seconds"):
                if key in spec:
                    entry[key] = spec[key]
            # 转场段帧图独立加载成自己的列表（不与表演帧混槽）；
            # 缺图/坏图只降级（转场段退回亮表演末帧），绝不拦启动
            trans_rels = [str(r).replace("\\", "/")
                          for r in (spec.get("transition_frames") or ())]
            if trans_rels and all(r in available for r in trans_rels):
                pics = []
                for rel in trans_rels:
                    pm = QPixmap(str(ASSETS / rel))
                    if pm.isNull():
                        pics = []
                        break
                    pics.append(pm.scaled(display_size, display_size,
                                          Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation))
                if pics:
                    entry["transition_pics"] = pics
        states[name] = entry
    return states


# ---------------------------------------------------------------- 气泡窗
class Bubble(QLabel):
    """独立的置顶小窗，不挤占宠物窗口矩形，也不挡宠物周围桌面的点击。"""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWordWrap(True)
        self.setMaximumWidth(250)
        self.setStyleSheet(
            "QLabel{background:rgba(255,255,255,242);color:#4a3b32;"
            "border:2px solid rgb(214,178,166);border-radius:12px;"
            "padding:8px 12px;font-size:14px;font-weight:bold;}")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def pop(self, text: str, seconds: float, anchor_x: int, anchor_y: int):
        self.setText(text)
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = min(max(anchor_x - self.width() // 2, screen.left() + 4),
                screen.right() - self.width() - 4)
        y = min(max(anchor_y - self.height() - 10, screen.top() + 4),
                screen.bottom() - self.height() - 4)
        self.move(x, y)
        self.show()
        self.raise_()
        self._timer.start(int(max(2.0, seconds) * 1000))


# ---------------------------------------------------------------- 宠物窗口
class PetWindow(QWidget):
    cmds_ready = Signal(list)

    def __init__(self, cp):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool)
        self.cp = cp
        global TICK_MS
        TICK_MS = resolve_tick_ms(cp)   # 渲染节拍可配置（30fps 载波，省电改 66）
        self.pet_name = cp.get("pet", "name", fallback="团子")
        display = int(cp.get("pet", "display_size", fallback="128"))
        self.states = load_states(display)

        pad = 26
        w = max(s["pixmap"].width() for s in self.states.values()) + pad * 2
        h = max(s["pixmap"].height() for s in self.states.values()) + pad + 10
        self.resize(w, h)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.label = QLabel(self)
        self.label.setScaledContents(False)
        self.bubble = Bubble()

        # 动画状态
        self.current = "idle"
        self.phase = 0.0
        self.hop_until = 0.0
        self.last_activity = time.monotonic()
        self._sleep_probe_ms = 0.0    # 闲置入睡探测的独立轻量计数器
        # 动作点播：action 非 None 即表演期；谢幕回归 prev，pending 让路排队
        self.action = None            # ActionPlayer 实例（播放器）
        self._action_prev = None      # 表演开始前的心情（谢幕回归目标）
        self._action_started = 0.0    # 保险丝秒表：动作上场时刻（monotonic）
        self._action_max = 0.0        # 保险丝上限（秒）；0 = 不设防
        self.pending_state = None     # 表演期间收到的最后一条 SetState 请求
        self._prev_state = None       # 结算画面打开前的心情，关窗时恢复
        self.settlement_open = False  # 全屏结算画面是否正在放

        # 大脑（LLM 的网络调用在别的线程，结果用信号排队回主线程）
        self.mode = cfgmod.brain_mode(cp)
        self.set_driver(self.mode, remember=False)

        # 鼠标交互
        self._press_global = None
        self._press_win_pos = None
        self._dragged = False
        # 左键单/双击判定：280ms 窗口纯逻辑在 click_flow，宿主只搬运 QTimer
        self._clicks = ClickResolver()
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_click_window_timeout)
        self._menu_guard_timer = QTimer(self)
        self._menu_guard_timer.setSingleShot(True)
        self._menu_guard_timer.timeout.connect(self._expire_menu_guard)
        self._skip_next_release = False   # 双击自带的第二次 release 要跳过
        self._click_sfx_eff = None        # click.wav 专属实例（独立于合成节流）

        # 渲染循环
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick)
        self.anim_timer.start(TICK_MS)

        # 健康提醒
        self.reminder_idx = 0
        interval_min = int(cp.get("reminders", "interval_minutes", fallback="45"))
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self._fire_reminder)
        if cp.getboolean("reminders", "enabled", fallback=True):
            self.reminder_timer.start(interval_min * 60 * 1000)

        # 程序合成音效：effect 对象按名惰性缓存复用；enabled=false 整体静默。
        try:
            volume = float(cp.get("sound", "volume", fallback="0.6"))
        except ValueError:
            volume = 0.6
        self.snd = {}                       # name -> QSoundEffect 惰性缓存
        self._sound_enabled = cp.getboolean("sound", "enabled", fallback=True)
        self._sound_volume = max(0.0, min(1.0, volume))
        self._last_sfx_at = 0.0             # 礼貌节流用：上一响的时刻

        # 点歌整首（单击触发）：QMediaPlayer 薄封装；曲目与音量来自
        # [sound]，文件缺失或多媒体后端不可用时单击回落「戳我」演出，
        # 全程静默不崩。相对路径先 exe/仓库根、后解包目录（frozen 兜底）。
        self.music = MusicPlayer(self)
        try:
            music_vol = float(cp.get("sound", "music_volume",
                                     fallback="0.6"))
        except ValueError:
            music_vol = 0.6
        self._music_volume = max(0.0, min(1.0, music_vol))
        # 主人拍板：BGM 全量下线（朋友嫌吵）——music 总开关默认关，
        # config [settlement] bgm=false；想恢复把 enabled_music 改 true 即可
        self.music.music_disabled = not cp.getboolean(
            "sound", "enabled_music", fallback=False)
        self._music_path = resolve_music(
            cp.get("sound", "music_file", fallback=""),
            (paths.APP_DIR, paths.BUNDLE_DIR))

        # 无聊闲聊
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self._maybe_idle_chat)
        self._schedule_idle_chat()

        # Git 成长扩展（纯本地：扫本仓库当日提交；frozen 时扫 exe 旁边）
        self.growth = GrowthTracker(repo_dir=str(paths.APP_DIR))
        self._last_level = 0

        # 编译兴衰军师：hook 的 error/success 序列在 _on_hook 统一过账
        self.streaks = BuildStreak()

        # 全屏结算画面（同一时间至多一张，避免连按 hook 叠黑幕）
        self._settlement_win = None
        self._setup_settlement_timer()

        # 外部桥接（ZCode hook 入口）；token 每次启动轮换
        self.bridge = None
        self.bridge_port = None
        if cp.getboolean("bridge", "enabled", fallback=True):
            try:
                token = cfgmod.rotate_token()
                self.bridge = BridgeServer(
                    int(cp.get("bridge", "port", fallback="8321")),
                    token)
                self.bridge_port = self.bridge.start()
                btimer = QTimer(self)
                btimer.timeout.connect(self._drain_bridge)
                btimer.start(150)
            except OSError as e:
                print(f"[团子] 桥接端口被占用({e})，本次启动禁用 hook 联动")

        self.cmds_ready.connect(self.apply)   # 工作线程 -> 主线程
        self._place_initial()

        key_set = bool((cp.get("brain", "api_key", fallback="") or "").strip())
        print(f"[团子] 大脑: {self.mode}" +
              (f"(model={cfgmod.llm_kwargs(cp)['model']}, key已配置={key_set})"
               if self.mode == "llm" else "(离线兜底)"))
        if self.bridge_port:
            print(f"[团子] 桥接: http://127.0.0.1:{self.bridge_port}/react "
                  f"(token 在 config.ini)")
        print(f"[团子] 提醒: {'开' if self.reminder_timer.isActive() else '关'}"
              f" 每 {interval_min} 分钟")

    # ---------------- 布局与动画 ----------------
    def _place_initial(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        pos = None
        try:
            data = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
            pos = QPoint(int(data["x"]), int(data["y"]))
        except Exception:
            pos = None
        if pos is None or not screen.contains(pos):
            pos = QPoint(screen.right() - self.width() - 40,
                         screen.bottom() - self.height() - 20)
        self.move(pos)

    def _save_position(self):
        RUNTIME_PATH.write_text(json.dumps({"x": self.x(), "y": self.y()}),
                                encoding="utf-8")

    def set_state(self, state: str):
        # 缺图的可选表情被事件点到时保持当前画面，绝不让 KeyError 崩掉渲染循环
        if state not in self.states:
            print(f"[团子] 表情 {state} 还没有图片素材，维持现状")
            return
        self.current = state
        self.phase = 0.0

    # ---------------- 动作点播（ActionPlayer 接管换帧）----------------
    def play_action(self, name: str, play: str | None = None,
                    hold_tail_ms: int | None = None,
                    spec: dict | None = None) -> bool:
        """点播一段完整动作：记住来路 -> 播放器接管换帧 -> 谢幕自动回归。

        play 缺省读 manifest 的 v4 字段（play=once 完整播放一轮）；
        结算画面等需要持续演出的场合显式传 play="loop"。
        hold_tail_ms：once 定格段毫秒数；None 时回落 manifest 的
        hold_seconds 字段（如单击 shock 演出传 1200，与 manifest 现值同源）。
        spec：可选的条目覆盖（点歌伴舞传 song_flow.dance_loop_spec 的
        循环规格）；缺省照旧读 self.states[name]。
        无帧的单图状态退化为直接切换（安静待机语义），不进场表演。
        """
        entry = spec if isinstance(spec, dict) and spec \
            else self.states.get(name)
        if not entry:
            return False
        if not entry.get("frames"):
            self.set_state(name)
            return True
        base = self._action_prev if self.action is not None else self.current
        if self._action_prev is None:
            self._action_prev = base   # 加播不覆盖来路：谢幕仍回最初的
        mode = (play or str(entry.get("play") or "loop")).lower()
        wants = [base, str(entry.get("return_to") or "idle"), "idle"]
        finish_target = next((s for s in wants if s in self.states), "idle")
        self.set_state(name)           # 先渲染首帧，呼吸相位同步归零
        player = ActionPlayer()
        player.start(dict(entry, play=mode), on_finish_state=finish_target,
                     hold_tail_ms=hold_tail_ms or 0)
        self.action = player
        # 独立秒表保险丝上弦：与 ActionPlayer 内部计时互不相干，双保险
        try:
            max_s = float(entry.get("max_seconds") or 0)
        except (TypeError, ValueError):
            max_s = 0.0
        self._action_started = time.monotonic()
        self._action_max = max_s if mode == "once" else 0.0
        return True

    def _finish_action(self):
        """谢幕：回 来路 -> 表演者声明 -> idle 里第一个有图的；再结算排队请求。

        候补去重：谢幕目标与候补请求相同时候补作废——「回发呆」不该被
        排队的同目标请求二次触发（表演中提醒塞进来的 idle 就是这种）。"""
        player, self.action = self.action, None
        base = self._action_prev
        self._action_prev = None
        self._action_started = 0.0     # 谢幕即撤防：保险丝秒表一并清零
        self._action_max = 0.0
        fallbacks = [base, getattr(player, "on_finish_state", "idle"), "idle"]
        target = next((s for s in fallbacks if s in self.states), "idle")
        self.set_state(target)
        if self.pending_state is not None:
            want, self.pending_state = self.pending_state, None
            if want != target:
                self.set_state(want)

    def _celebrating(self, now: float) -> bool:
        """双档节奏的统一判定：hop 生效期或全屏结算画面开着 = 狂欢档。"""
        return now < self.hop_until or self.settlement_open

    def _render(self, spec: dict, frame_idx=None, celebrating: bool = False,
                use_transition: bool = False):
        """把一个状态画上屏幕：呼吸/摆动 + 可选的指定帧。

        use_transition=True 时亮转场段帧列表（transition_pics），下标是
        ActionPlayer 转场段给出的 transition_frames 下标。
        """
        period, amp = spec["period"], spec["amp"]
        if celebrating:
            period = min(period, 0.35)
            amp = max(amp, 9.0)
        self.phase += TICK_MS / 1000.0 / period

        bob = -abs(math.sin(self.phase * math.pi * 2)) * amp
        angle = spec["tilt"] * math.sin(self.phase * math.pi * 4)

        if frame_idx is not None and spec.get("frames"):
            pics = spec["frames"]
            if use_transition and spec.get("transition_pics"):
                pics = spec["transition_pics"]   # 转场段亮转场帧列表
            pm = pics[min(frame_idx, len(pics) - 1)]
        else:
            # 安静待机：多帧状态静立首帧，不再 ambient 轮播（治"定格闪跳"）
            pm = spec["pixmap"]

        if abs(angle) > 0.4:
            pm = pm.transformed(QTransform().rotate(angle),
                                Qt.SmoothTransformation)
        self.label.setPixmap(pm)
        self.label.adjustSize()
        self.label.move((self.width() - pm.width()) // 2,
                        self.height() - pm.height() - 6 + int(bob))

    def _tick(self):
        dt = TICK_MS / 1000.0
        now = time.monotonic()
        # 动作播放期：换帧交给 ActionPlayer 裁决；bob/tilt 保持平时微幅
        if self.action is not None:
            # 第二道秒表保险丝（独立于 ActionPlayer 内部计时）：超时强制
            # 谢幕。实现取最简单的「直接切」——不补播转场帧，宁可硬切也
            # 绝不卡死在表演里；正常路径的转场段仍由播放器自己播完。
            if action_overtime(now - self._action_started, self._action_max):
                self._finish_action()
                return
            idx = self.action.tick(dt)
            if idx is None:
                self._finish_action()
                return          # 谢幕当拍先收摊，下一拍起恢复正常渲染
            self._render(self.states[self.current], frame_idx=idx,
                         use_transition=self.action.segment == "transition")
            return
        # 走到这里必然空闲（无 action 播放中）：闲置久了悄然入睡
        self._maybe_auto_sleep(now)
        self._render(self.states[self.current],
                     celebrating=self._celebrating(now))

    def _maybe_auto_sleep(self, now: float):
        """闲置自动入睡的宿主接缝：约 1.5 秒探一次，判定全在纯函数里。

        独立轻量计数器，不动动画时钟；命中就 set_state("sleep")——不发
        台词不弹气泡。用户点击/提醒等交互会刷新 last_activity 并把状态
        自然切走，无需额外唤醒逻辑。
        """
        self._sleep_probe_ms += TICK_MS
        if self._sleep_probe_ms < 1500:
            return
        self._sleep_probe_ms = 0.0
        quiet = now - self.last_activity
        if idle_policy.should_auto_sleep(quiet, self.current,
                                         self.bubble.isVisible()):
            self.set_state("sleep")

    # ---------------- 鼠标：拖拽 vs 点击 ----------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._press_win_pos = self.pos()
            self._dragged = False

    def mouseMoveEvent(self, e):
        if self._press_global is None:
            return
        delta = e.globalPosition().toPoint() - self._press_global
        if abs(delta.x()) + abs(delta.y()) > 8:
            self._dragged = True
            self.move(self._press_win_pos + delta)

    def mouseReleaseEvent(self, e):
        left = e.button() == Qt.LeftButton
        was_drag = self._dragged
        self._press_global = None
        self._dragged = False
        self.last_activity = time.monotonic()
        self._save_position()
        if not left or was_drag:
            return
        if self._skip_next_release:
            # 这是双击事件自带的第二次 release：不许再开新判定窗口
            self._skip_next_release = False
            return
        if self._clicks.press() == "pending":
            # 第一击：挂起 280ms 单发定时器等第二击；等到就进双击事件
            self._click_timer.start(DOUBLE_CLICK_MS)

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        # 双击成交：取消挂起的单击，放外星吸入全套
        self._clicks.cancel()
        self._click_timer.stop()
        self._skip_next_release = True
        self.last_activity = time.monotonic()
        self._perform_double_click()

    def _expire_menu_guard(self):
        """菜单关闭后的单击屏蔽窗到期：恢复常规点击判定。"""
        self._skip_next_release = False

    def _on_click_window_timeout(self):
        """280ms 到点仍只有一击：裁决为单击专属演出。"""
        if self._clicks.timeout() == "single":
            self._perform_single_click()

    # ---------------- 左键单/双击专属演出（宿主接管，不再 dispatch 驱动）----------------
    def _perform_single_click(self):
        """单击=点歌：整首播放 bgm.mp3 + dance 循环伴舞到歌完，歌完回发呆。

        三条出路：
        - 歌播着 -> 忽略（不重播、不重置、不抢戏）；
        - 曲目在且开播成功 -> 循环伴舞，登记歌完回调收舞；
        - mp3 缺失 / 多媒体后端坏 -> 回落现状：固定句气泡 + click.wav +
          shock 演出尾部定格 1.2 秒（转场帧融回 idle）。
        结算画面打开期间照旧一律忽略。
        """
        if not should_perform(self.settlement_open):
            return
        if should_ignore_click(self.music.is_playing()):
            return
        if self._music_path is not None \
                and self.music.play(self._music_path, self._music_volume):
            self._start_song_dance()
            return
        # —— 降级回落：现状的「戳我」定格演出 ——
        self.apply([bus.Say(CLICK_TEASE)])
        self._play_click_sfx()
        self.play_action("shock", hold_tail_ms=SHOCK_HOLD_TAIL_MS)
        # 【禁用区】旧单击 hide 定格演出（1500ms），主人拍板暂时下线，可随时恢复：
        # self.play_action("hide", hold_tail_ms=HIDE_HOLD_TAIL_MS)

    def _start_song_dance(self):
        """伴舞：dance 以 loop 档只循环表演帧（剔转场尾），歌完回调收舞。

        循环规格由 song_flow.dance_loop_spec 纯函数裁决；dance 缺图等
        极端情况退化走标准点播路径（单图直接切换），歌照播不受牵连。
        """
        self.music.on_finished(self._on_song_finished)
        entry = self.states.get("dance")
        if entry and entry.get("frames"):
            spec = dance_loop_spec(entry, self.music.duration_seconds(),
                                   entry.get("frame_ms") or 0)
            if self.play_action("dance", play="loop", spec=spec):
                return
        self.play_action("dance", play="loop")

    def _on_song_finished(self):
        """歌完收舞：谢幕回发呆；期间被结算等接管的演出不重复收拾。"""
        if self.action is not None:
            self._finish_action()

    def _perform_double_click(self):
        """双击=点歌开跳：click.wav（「时间来不及了」原声）+ dance 扭舞一段。

        dance 是 once+return_to=idle，序列尾部经转场补帧融回 idle，
        跳完自然谢幕；歌播着的时候与结算画面打开期间一律忽略。
        """
        if not should_perform(self.settlement_open):
            return
        if should_ignore_click(self.music.is_playing()):
            return
        self._play_click_sfx()
        self.play_action("dance")
        # 【禁用区】旧 UFO 吸入演出整段注释保留（alien_suck 已入禁用区）：
        # self.play("suck")
        # self.play_action("alien_suck")

    def play_six_beat(self) -> bool:
        """六拍舞点播（菜单「情绪」组词条）：常驻循环舞 + 配乐放一遍。

        舞走 loop 档永续循环——once 的表演窗口/定格/谢幕逻辑一概不适用；
        配乐放烘焙抽好的音轨（缺文件回落源视频），放完一遍即停、不循环，
        团子继续循环跳，直到用户点别的动作。结算画面打开期间一律忽略；
        配乐缺失或多媒体后端坏时静默开跳，绝不拦舞。
        """
        if not should_perform(self.settlement_open):
            return False
        if not self.play_action(SIX_BEAT_STATE, play="loop"):
            return False
        self.music.stop()   # 顶掉可能在播的点歌，顺手清掉旧歌完回调
        bgm = resolve_dance6_bgm((paths.APP_DIR, paths.BUNDLE_DIR))
        if bgm is not None:
            self.music.play(bgm, self._music_volume)
        return True

    def _on_menu_action(self, state: str):
        """菜单点播统一入口：vroom 长途骑行带配乐，其余直接点播。"""
        if state == "vroom":
            self._play_vroom_bgm()
        self.play_action(state)

    def _play_vroom_bgm(self):
        """骑摩托长途配乐：assets/local/vroom.wav（bgm 30s 起的 20 秒段）。

        缺文件/后端坏静默骑行不拦车；与点歌互斥（顶掉在播曲目）。
        """
        try:
            for base in (paths.APP_DIR, paths.BUNDLE_DIR):
                cand = pathlib.Path(base) / "assets" / "local" / "vroom.wav"
                if cand.is_file():
                    self.music.stop()
                    self.music.play(cand, self._music_volume)
                    return
        except Exception:
            pass

    def _play_click_sfx(self):
        """播 [sound] click_sfx 指向的本地 wav（独立 QSoundEffect 实例）。

        与 suck/pop 等合成音效的 0.18s 节流互不影响；未配置、文件缺失、
        无声环境、缺多媒体后端一律静默，回落内置合成 pop。
        """
        if not self._sound_enabled:
            return
        path = resolve_click_sfx(
            self.cp.get("sound", "click_sfx", fallback=""),
            (paths.APP_DIR, paths.BUNDLE_DIR))
        if path is None:
            self.play("pop")
            return
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
            eff = self._click_sfx_eff
            if eff is None:
                eff = QSoundEffect(self)
                eff.setSource(QUrl.fromLocalFile(path))
                self._click_sfx_eff = eff
            eff.setVolume(self._sound_volume)
            eff.play()
        except Exception:
            self.play("pop")   # 后端失败：回落合成 pop，最多无声不会炸

    # ---------------- 事件分发（harness 核心）----------------
    def set_driver(self, mode: str, remember: bool = True):
        self.mode = mode
        self.driver = get_driver(mode, self.cp, self.pet_name)
        if remember:
            self.cp.set("brain", "mode", mode)
            with open(cfgmod.CONFIG_PATH, "w", encoding="utf-8") as f:
                self.cp.write(f)
        if hasattr(self, "act_rule"):
            self.act_rule.setChecked(mode == "rule")
            self.act_llm.setChecked(mode == "llm")

    def dispatch(self, ev: dict):
        self.last_activity = time.monotonic()
        if ev.get("type") == "growth" and ev.get("leveled_up"):
            self.play("tada")
        if ev.get("type") == "hook" and ev.get("event") == "done":
            # 收工 hook 升级为全屏结算画面：保留表情庆祝、压掉同义台词——
            # 黑幕已经是最强“收工”表达，背后再冒一条气泡属于双重打扰。
            self.apply([bus.SetState("cheer"), bus.Hop()])
            self.scan_growth()
            return
        if self.mode == "llm":
            # UI 不等网络：事件先下发后台问大脑，台词回来再上屏
            threading.Thread(target=self._llm_run, args=(ev,),
                             daemon=True, name="petfw-brain").start()
        else:
            self.apply(self.driver.react(ev))

    def _llm_run(self, ev):
        try:
            cmds = self.driver.react(ev)
        except Exception as exc:  # 驱动内部已兜底，这里防线程崩
            print(f"[团子] 大脑开小差了: {exc}")
            cmds = []
        self.cmds_ready.emit(cmds or [bus.Hop()])

    def apply(self, cmds):
        for c in cmds:
            if isinstance(c, bus.SetState):
                # 表演中不打断：请求进候补位排队（后来覆盖先来），谢幕再应用
                target, self.pending_state = defer_if_playing(
                    self.pending_state, self.action is not None, c.state)
                if target is not None:
                    self.set_state(target)
            elif isinstance(c, bus.Say):
                top_right = self.mapToGlobal(QPoint(self.width() - 10, 0))
                self.bubble.pop(c.text, c.seconds, top_right.x(), top_right.y())
            elif isinstance(c, bus.Hop):
                self.play("boing")
                self.hop_until = time.monotonic() + 0.7

    # ---------------- 程序合成音效（零素材，无声不崩）----------------
    def play(self, name: str):
        """按名播一个运行期合成的短音效；任何失败都静默跳过。

        首次使用才 import QtMultimedia、合成 wav 落系统临时目录并建
        QSoundEffect；同名第二次起直接复用缓存对象 play()。相邻两响
        限流 0.18s，保证一条事件只挂一响、多个钩子不会叠音。
        """
        if not (self._sound_enabled and name):
            return
        now = time.monotonic()
        if now - self._last_sfx_at < 0.18:
            return
        try:
            from . import sound_core
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
            eff = self.snd.get(name)
            if eff is None:
                path = os.path.join(tempfile.gettempdir(),
                                    f"petfw_sfx_{name}.wav")
                data = sound_core.synthesize(name)
                if not data:
                    return                      # 未知名等兜底：安静跳过
                if not os.path.exists(path):    # 已存在就复用，不再落盘
                    with open(path, "wb") as f:
                        f.write(data)
                eff = QSoundEffect(self)
                eff.setSource(QUrl.fromLocalFile(path))
                eff.setVolume(self._sound_volume)
                self.snd[name] = eff
            self._last_sfx_at = now
            eff.play()
        except Exception:
            pass    # 无声环境 / 缺多媒体后端 / 写盘失败：一概当没这回事

    # ---------------- 提醒与闲聊 ----------------
    def _fire_reminder(self):
        kind = ["drink", "stretch"][self.reminder_idx % 2]
        self.reminder_idx += 1
        self.play("ding" if kind == "drink" else "chime")
        self.dispatch({"type": "reminder", "kind": kind})

    def _schedule_idle_chat(self):
        import random
        self.idle_timer.start(random.randint(45_000, 105_000))

    def _maybe_idle_chat(self):
        # 表演中/气泡开着：本轮闲聊整段跳过（哭戏上盖闲聊气泡也算打扰）
        if self.action is not None or not self.bubble.isHidden():
            self._schedule_idle_chat()
            return
        quiet_for = time.monotonic() - self.last_activity
        if quiet_for >= 100:
            self.dispatch({"type": "idle", "seconds": int(quiet_for)})
        self._schedule_idle_chat()

    def _on_hook(self, ev: dict):
        """所有外部 hook 入口的统一关卡：先过一遍兴衰军师再分发。

        BuildStreak 只认 error/success；判定结果（flourish/streak）合并进
        事件原样下发，规则脑读 flourish 演出，LLM 脑靠 describe_event 受益。
        """
        try:
            verdict = self.streaks.update(ev.get("event"))
        except Exception:
            verdict = {}
        if verdict:
            ev = {**ev, **verdict}
        self.dispatch(ev)

    def _drain_bridge(self):
        q = self.bridge.sink
        while True:
            try:
                ev = q.get_nowait()
            except queue.Empty:
                return
            self.play(HOOK_SFX.get(ev.get("event")))
            self._on_hook(ev)

    # ---------------- 动作菜单：本体右键与托盘共用同一构建器 ----------------
    @staticmethod
    def build_actions_menu(menu, window):
        """统一生成动作点播菜单（QMenu 就地填充）。

        三段分组（情绪/整活/系统），状态词条只列 window.states 里已加载出图
        的；点播词条触发 window.play_action，系统词条直接复用宿主现有槽，
        一处维护、本体右键与托盘两处永不漂移。
        """
        menu.clear()

        def _header(text):
            head = menu.addAction(text)
            head.setEnabled(False)      # 分组标题只当看板，不可点

        def _label(st):
            # 表情状态用 STATE_ZH；专属演出动作（alien_suck）用 ACTION_ZH
            return STATE_ZH.get(st) or ACTION_ZH.get(st) or st

        # —— 情绪组：八正态直呼其字（缺图的如生气自动缺席隐藏）——
        _header("情绪")
        for st in MENU_EMOTION:
            if st in window.states:
                menu.addAction(_label(st),
                            lambda s=st: window._on_menu_action(s))
        # —— 六拍舞：程序剪纸常驻循环舞，词条固定在情绪组末尾 ——
        if SIX_BEAT_STATE in window.states:
            menu.addAction(SIX_BEAT_ZH, window.play_six_beat)
        menu.addSeparator()
        # —— 整活组 ——
        _header("整活")
        for st in MENU_FUN:
            if st in window.states:
                menu.addAction(_label(st),
                            lambda s=st: window._on_menu_action(s))
        menu.addSeparator()
        # —— 系统组：复用宿主既有槽方法，绝不复制逻辑 ——
        _header("系统")
        menu.addAction("今日战报", window.scan_growth)
        # 【主人拍板暂时下线】天气演示词条整体注释保留，weather 扩展本体与
        # dispatch 事件通路不动，随时可恢复菜单入口：
        # wx_menu = menu.addMenu("天气演示")
        # for zh_w, cond in (("晴", "Clear"), ("多云", "Clouds"),
        #                    ("雨", "Rain"), ("雪", "Snow")):
        #     wx_menu.addAction(zh_w, lambda c=cond: window.dispatch(
        #         {"type": "weather", "condition": c}))
        # 【主人拍板暂时下线】模拟 hook 词条注释保留；桥接入口仍走同一关卡
        # （_on_hook），外部程序照常可以投喂 edit 事件：
        # menu.addAction("模拟hook(edit)", lambda: window._on_hook(
        #     {"type": "hook", "event": "edit"}))
        act_reminder = menu.addAction("健康提醒")
        act_reminder.setCheckable(True)
        act_reminder.setChecked(window.reminder_timer.isActive())
        act_reminder.toggled.connect(window._toggle_reminders)
        menu.addAction("退出", window.quit_app)

    def contextMenuEvent(self, e):
        """右键点击桌宠本体：弹出动作点播菜单（与托盘共用构建器）。"""
        menu = QMenu(self)
        self.build_actions_menu(menu, self)
        # 菜单交互期 + 关闭后短暂冷却：词条点击的 release 不许被裁决成
        # 单击点歌（否则刚点播的哭唧唧会被伴舞当场顶掉——中途切走元凶）
        self._skip_next_release = True
        menu.exec(e.globalPos())
        self._menu_guard_timer.start(600)
        menu.deleteLater()

    # ---------------- 托盘 ----------------
    def make_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(QIcon(self.states["cheer"]["pixmap"]), self)
        tray.setToolTip(f"{self.pet_name} — 本地桌宠")
        menu = QMenu()
        # 与本体右键共用同一份构建器：消除两份菜单清单的漂移
        self.build_actions_menu(menu, self)
        tray.setContextMenu(menu)
        tray.show()
        return tray

    def scan_growth(self):
        commits = self.growth.scan_today()
        if commits is None:
            self.apply([bus.Say("这里好像不是 git 仓库哦…"), bus.SetState("idle")])
            return
        level, title = level_for(commits)
        leveled_up = level > self._last_level
        self._last_level = max(self._last_level, level)
        # 拿到数据就升级为全屏结算画面；表现层失败再退回旧气泡战报
        if not self._open_settlement(commits, title, leveled_up):
            self.dispatch({"type": "growth", "commits": commits,
                           "level": level, "title": title,
                           "leveled_up": leveled_up})

    def _open_settlement(self, commits: int, title: str, leveled_up: bool) -> bool:
        """打开全屏走马灯结算；同一时间只开一张。成功返回 True。

        文案全部由 settlement_core 纯函数生成，本方法只做搬运与兜底。
        结算期间本体切扭舞并保持蹦跶（opened 信号驱动），关窗后恢复原状态
        （closed 信号驱动）；BGM 开关与变速倍率同样来自 [settlement] 配置。
        """
        if self._settlement_win is not None and self._settlement_win.isVisible():
            return True  # 已经在放回放了，不重复抢屏
        try:
            from .settlement_core import build_settlement_lines, find_bgm
            from .settlement_window import SettlementWindow
            lines = build_settlement_lines(
                commits, title, leveled_up,
                datetime.now().strftime("%Y-%m-%d"))
            extra = (paths.APP_DIR,) if paths.FROZEN else ()
            win = SettlementWindow(
                lines,
                bgm_path=find_bgm(paths.ASSETS, extra_dirs=extra),
                bgm_enabled=self.cp.getboolean("settlement", "bgm",
                                               fallback=False),
                bgm_rate=self.cp.get("settlement", "bgm_rate",
                                     fallback="2.5"),
            )
            win.opened.connect(self._on_settlement_opened)
            win.closed.connect(self._on_settlement_closed)
            win.showFullScreen()
            win.activateWindow()
            self._settlement_win = win
            return True
        except Exception as exc:
            print(f"[团子] 结算画面打不开，退回气泡战报：{exc}")
            return False

    # ---------------- 结算画面 <-> 本体表情联动 ----------------
    def _on_settlement_opened(self):
        """结算开演：记住此刻心情，扭舞改走新的动作点播（循环档直到谢幕）。

        点歌中的整首 BGM 先停：结算画面自带 2.5 倍速 BGM，两首叠播
        属于双重打扰；停歌顺手清掉歌完回调，收舞交给结算关窗流程。
        """
        self.settlement_open = True
        self.music.stop()
        if self._prev_state is None:      # 重复 opened 只记第一次，幂等
            self._prev_state = self.current
        target = "dance" if "dance" in self.states else "cheer"
        if not self.play_action(target, play="loop"):
            # 连图都没有的极端情况：退回普通 SetState，至少表情还在
            self.apply([bus.SetState(target)])
        self.apply([bus.Hop()])

    def _on_settlement_closed(self):
        """谢幕：终止演出并撤掉 celebrate 标志，恢复打开前的表情。"""
        self.settlement_open = False
        if self.action is not None:
            # 循环档扭舞没有自然终点，这里直接叫停——回归逻辑与 once 相同
            self.action = None
            self._action_prev = None
            self._action_started = 0.0   # 保险丝一并撤防
            self._action_max = 0.0
        prev, self._prev_state = self._prev_state, None
        if prev and prev in self.states:
            self.set_state(prev)
        elif self.pending_state is not None:
            want, self.pending_state = self.pending_state, None
            self.set_state(want)

    # ---------------- 每日定时结算（单发 QTimer 跨天自续）----------------
    def _daily_time(self) -> str:
        return self.cp.get("settlement", "daily_time", fallback="18:00")

    def _setup_settlement_timer(self):
        from .settlement_core import next_delay_ms
        self.daily_timer = QTimer(self)
        self.daily_timer.setSingleShot(True)
        self.daily_timer.timeout.connect(self._fire_daily_settlement)
        if not self.cp.getboolean("settlement", "enabled", fallback=True):
            return
        self.daily_timer.start(next_delay_ms(datetime.now(), self._daily_time()))

    def _fire_daily_settlement(self):
        print("[团子] 到点了，播今日回合结算")
        self.scan_growth()
        # 触发后再算下一次：今天已过点，next_delay_ms 自动排到明天
        try:
            from .settlement_core import next_delay_ms
            self.daily_timer.start(next_delay_ms(datetime.now(),
                                                 self._daily_time()))
        except Exception as exc:
            print(f"[团子] 结算定时重排失败（今日不再自动播）：{exc}")

    def _toggle_reminders(self, on: bool):
        self.cp.set("reminders", "enabled", "true" if on else "false")
        try:
            with open(cfgmod.CONFIG_PATH, "w", encoding="utf-8") as f:
                self.cp.write(f)
        except OSError:
            pass
        if on:
            self.reminder_timer.start(int(self.cp.get(
                "reminders", "interval_minutes", fallback="45")) * 60 * 1000)
            self.apply([bus.Say("好！我会盯着你喝水的！", 5)])
        else:
            self.reminder_timer.stop()

    def toggle_visible(self):
        self.setVisible(not self.isVisible())

    def quit_app(self):
        self.music.stop()   # 退出前先停歌，别让音乐拖尾
        if self.bridge:
            self.bridge.stop()
        self._save_position()
        QApplication.quit()

    def closeEvent(self, e):
        self.quit_app()


# ---------------------------------------------------------------- 入口
def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    app = QApplication(argv)
    app.setApplicationName("petfw")
    app.setQuitOnLastWindowClosed(False)  # 关窗不退出，从托盘退

    cp = cfgmod.load()
    win = PetWindow(cp)
    win.show()
    win.set_state("idle")
    win.make_tray()

    if "--smoke" in argv:
        print("[smoke] 1.6 秒后自动退出")
        QTimer.singleShot(1600, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
