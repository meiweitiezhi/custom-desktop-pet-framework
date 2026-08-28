"""Qt 宿主：负责渲染与输入，业务决策全部交给 Driver。

窗口只是一个"渲染器"：
  收到事件 -> dispatch() -> Driver.react() -> 命令列表
  apply() 只认三种命令：SetState / Say / Hop（bus.py 定义）
"""
import json
import math
import os
import queue
import sys
import tempfile
import threading
import time
from datetime import datetime

from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap, QTransform
from PySide6.QtWidgets import (QApplication, QLabel, QMenu, QSystemTrayIcon,
                               QWidget)

from . import bus
from . import config as cfgmod
from . import paths
from .action_player import ActionPlayer
from .animator_core import DEFAULT_FRAME_MS
from .bridge import BridgeServer
from .drivers import get_driver
from .extensions.growth import GrowthTracker, level_for
from .streaks import BuildStreak

ASSETS = paths.ASSETS            # 只读素材（frozen 时在解包目录）
RUNTIME_PATH = paths.RUNTIME_PATH  # 可写位置（frozen 时在 exe 旁边）

TICK_MS = 66

# hook 事件 -> 音效名（一条事件只挂一响，集中在这里，别处不再叠加）
HOOK_SFX = {"error": "wah", "praise": "kiss", "kiss": "kiss"}

# 托盘状态子菜单的中文名；与 bus.STATES 一一对应（tests 有防漂移检查）
STATE_ZH = {
    "idle": "发呆", "cheer": "打气", "eat": "干饭", "sleep": "睡觉",
    "laugh": "笑哭", "shock": "惊讶", "angry": "生气", "dance": "扭舞",
    "cry": "哭唧唧", "hide": "缩帽躲", "love": "比小心心",
    "alien": "外星吸人", "blushmax": "羞耻爆炸",
}

# 右键动作菜单的两组状态词条；系统组条目在构建器里现场生成。
# 只渲染 self.states 里已加载出图的状态，缺图自动缺席隐藏。
MENU_EMOTION = ("idle", "cheer", "eat", "sleep",
                "laugh", "shock", "angry", "dance")
MENU_FUN = ("cry", "hide", "love", "alien", "blushmax")


def defer_if_playing(pending, playing, wanted):
    """SetState 让路裁决（纯函数）：表演中排队最后请求，空闲立即生效。

    返回 (本轮要应用的 target 或 None, 新的候补位)：
    - 表演中 -> 不打断演出，候补位直接被后来的请求覆盖；
    - 空闲   -> 新请求立刻上屏，顺手清掉过期的候补陈账。
    """
    if playing:
        return None, wanted
    return wanted, None


# ---------------------------------------------------------------- 素材加载
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
    entries = manifest["states"]
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
        # 动作点播：action 非 None 即表演期；谢幕回归 prev，pending 让路排队
        self.action = None            # ActionPlayer 实例（播放器）
        self._action_prev = None      # 表演开始前的心情（谢幕回归目标）
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
    def play_action(self, name: str, play: str | None = None) -> bool:
        """点播一段完整动作：记住来路 -> 播放器接管换帧 -> 谢幕自动回归。

        play 缺省读 manifest 的 v3 字段（play=once 完整播放一轮）；
        结算画面等需要持续演出的场合显式传 play="loop"。
        无帧的单图状态退化为直接切换（安静待机语义），不进场表演。
        """
        spec = self.states.get(name)
        if not spec:
            return False
        if not spec.get("frames"):
            self.set_state(name)
            return True
        base = self._action_prev if self.action is not None else self.current
        if self._action_prev is None:
            self._action_prev = base   # 加播不覆盖来路：谢幕仍回最初的
        mode = (play or str(spec.get("play") or "loop")).lower()
        wants = [base, str(spec.get("return_to") or "idle"), "idle"]
        finish_target = next((s for s in wants if s in self.states), "idle")
        self.set_state(name)           # 先渲染首帧，呼吸相位同步归零
        player = ActionPlayer()
        player.start(dict(spec, play=mode), on_finish_state=finish_target)
        self.action = player
        return True

    def _finish_action(self):
        """谢幕：回 来路 -> 表演者声明 -> idle 里第一个有图的；再结算排队请求。"""
        player, self.action = self.action, None
        base = self._action_prev
        self._action_prev = None
        fallbacks = [base, getattr(player, "on_finish_state", "idle"), "idle"]
        target = next((s for s in fallbacks if s in self.states), "idle")
        self.set_state(target)
        if self.pending_state is not None:
            want, self.pending_state = self.pending_state, None
            self.set_state(want)

    def _celebrating(self, now: float) -> bool:
        """双档节奏的统一判定：hop 生效期或全屏结算画面开着 = 狂欢档。"""
        return now < self.hop_until or self.settlement_open

    def _render(self, spec: dict, frame_idx=None, celebrating: bool = False):
        """把一个状态画上屏幕：呼吸/摆动 + 可选的指定帧。"""
        period, amp = spec["period"], spec["amp"]
        if celebrating:
            period = min(period, 0.35)
            amp = max(amp, 9.0)
        self.phase += TICK_MS / 1000.0 / period

        bob = -abs(math.sin(self.phase * math.pi * 2)) * amp
        angle = spec["tilt"] * math.sin(self.phase * math.pi * 4)

        if frame_idx is not None and spec.get("frames"):
            pm = spec["frames"][frame_idx]
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
            idx = self.action.tick(dt)
            if idx is None:
                self._finish_action()
                return          # 谢幕当拍先收摊，下一拍起恢复正常渲染
            self._render(self.states[self.current], frame_idx=idx)
            return
        self._render(self.states[self.current],
                     celebrating=self._celebrating(now))

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
        # 先算离开时长再刷新活动时刻：上次交互到现在就是“小主人走开多久”
        away_seconds = max(0, int(time.monotonic() - self.last_activity))
        self.last_activity = time.monotonic()
        self._save_position()
        if left and not was_drag:
            self.play("pop")
            self.dispatch({"type": "click", "away_seconds": away_seconds})

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
            # UI 不等网络：点击先蹦一下，台词回来再上屏
            if ev.get("type") == "click":
                self.apply([bus.Hop()])
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
        quiet_for = time.monotonic() - self.last_activity
        if quiet_for >= 100 and self.bubble.isHidden():
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

        # —— 情绪组：八正态直呼其字（缺图的如生气自动缺席隐藏）——
        _header("情绪")
        for st in MENU_EMOTION:
            if st in window.states:
                menu.addAction(STATE_ZH[st], lambda s=st: window.play_action(s))
        menu.addSeparator()
        # —— 整活组 ——
        _header("整活")
        for st in MENU_FUN:
            if st in window.states:
                menu.addAction(STATE_ZH[st], lambda s=st: window.play_action(s))
        menu.addSeparator()
        # —— 系统组：复用宿主既有槽方法，绝不复制逻辑 ——
        _header("系统")
        menu.addAction("今日战报", window.scan_growth)
        wx_menu = menu.addMenu("天气演示")
        for zh_w, cond in (("晴", "Clear"), ("多云", "Clouds"),
                           ("雨", "Rain"), ("雪", "Snow")):
            wx_menu.addAction(zh_w, lambda c=cond: window.dispatch(
                {"type": "weather", "condition": c}))
        # 模拟 hook 与桥接入口走同一关卡（_on_hook）
        menu.addAction("模拟hook(edit)", lambda: window._on_hook(
            {"type": "hook", "event": "edit"}))
        act_reminder = menu.addAction("健康提醒")
        act_reminder.setCheckable(True)
        act_reminder.setChecked(window.reminder_timer.isActive())
        act_reminder.toggled.connect(window._toggle_reminders)
        menu.addAction("退出", window.quit_app)

    def contextMenuEvent(self, e):
        """右键点击桌宠本体：弹出动作点播菜单（与托盘共用构建器）。"""
        menu = QMenu(self)
        self.build_actions_menu(menu, self)
        menu.exec(e.globalPos())
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
                                               fallback=True),
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
        """结算开演：记住此刻心情，扭舞改走新的动作点播（循环档直到谢幕）。"""
        self.settlement_open = True
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
