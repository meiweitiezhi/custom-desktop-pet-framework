"""运行期换装转场（transition-swap）：谢幕目标非发呆时尾段实时缩放目标立绘。

烘焙侧（prep_assets.bake_squash_return）把压扁回弹的终点烘死成 idle；
宿主 play_action 发现谢幕目标不是 idle（如常驻睡觉的宠物单击惊讶后要回
睡觉）时，改用 petfw.transition_swap 按同一套三幕数学实时缩放目标状态
立绘替换尾段——换装点之前的蓄力压扁帧照用烘焙产物（与目标无关）。
本文件锁三件事：两边常量同源不漂移、包络关键点位精确、宿主尾段构建
的拼接契约（头段原样 + 尾段换装 + 目标是发呆时不启用）。全程 offscreen。
"""
import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from PySide6.QtGui import QPixmap  # noqa: E402

import prep_assets  # noqa: E402
from petfw import transition_swap as tswap  # noqa: E402
from petfw.host import PetWindow  # noqa: E402

# 与 test_actions_menu 同款 QApplication（同进程单例，别用 QGuiApplication
# 混型——那会让后续模块的 QApplication 创建直接崩掉）
_APP = QApplication.instance() or QApplication([])


class TestConstantsParity(unittest.TestCase):
    """防漂移锁：运行期包络常量必须与烘焙侧逐个同源同值。"""

    def test_runtime_constants_mirror_bake_side(self):
        self.assertEqual(tswap.SY_MIN, prep_assets.SQUASH_SY_MIN)
        self.assertEqual(tswap.SX_MAX, prep_assets.SQUASH_SX_MAX)
        self.assertEqual(tswap.OVERSHOOT, prep_assets.SQUASH_OVERSHOOT)
        self.assertEqual(tswap.ACT1_SHARE, prep_assets.SQUASH_ACT1_SHARE)


class TestSwapEnvelope(unittest.TestCase):
    """三幕包络的纯数学：换装点位、落定值、过冲封顶。"""

    def test_swap_index_matches_bake_split(self):
        # bake: n1 = max(2, round(n*0.4))；shock 30 帧切在 12，dance 8 帧切在 3
        self.assertEqual(tswap.swap_index(30), 12)
        self.assertEqual(tswap.swap_index(8), 3)
        self.assertEqual(tswap.swap_index(4), 2)   # 下限兜底

    def test_swap_frame_sits_at_max_squash(self):
        # 换装帧必须恰在最大压扁 (sx 1.18, sy 0.78)：与烘焙侧第一幕末帧
        # 姿态连续，切换零比例错位
        for n in (8, 24, 30):
            k = tswap.swap_index(n)
            self.assertEqual(tswap.runtime_pose(k, n, headroom=0.2),
                             (tswap.SX_MAX, tswap.SY_MIN))

    def test_tail_settles_at_identity(self):
        # 末帧必须回到 (1.0, 1.0)：随后 set_state(target) 渲染原图无缝
        for n in (8, 30):
            sx, sy = tswap.runtime_pose(n - 1, n, headroom=0.2)
            self.assertAlmostEqual(sx, 1.0, places=9)
            self.assertAlmostEqual(sy, 1.0, places=9)

    def test_headroom_caps_vertical_overshoot(self):
        # 头顶没有透明余量（headroom=0）时竖向过冲封顶 1.0，绝不裁头
        n = 30
        peak = max(tswap.runtime_pose(k, n, headroom=0.0)[1]
                   for k in range(tswap.swap_index(n), n))
        self.assertLessEqual(peak, 1.0 + 1e-9)
        # 余量充足时过冲顶点约 1.12（ease_out_back 峰值，离散帧略低于连续峰）
        peak2 = max(tswap.runtime_pose(k, n, headroom=0.5)[1]
                    for k in range(tswap.swap_index(n), n))
        self.assertGreater(peak2, 1.10)
        self.assertLessEqual(peak2, tswap.OVERSHOOT + 1e-9)

    def test_pose_is_none_before_swap(self):
        # 换装点之前的帧不归运行期管（用烘焙帧），必须返回 None 示意
        self.assertIsNone(tswap.runtime_pose(0, 30, headroom=0.2))
        self.assertIsNone(
            tswap.runtime_pose(tswap.swap_index(30) - 1, 30, headroom=0.2))


def _solid_pixmap(size=96, top_margin=12):
    """不透明白方块立绘：顶部留 top_margin 透明（供过冲生长）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt_transparent())
    from PySide6.QtGui import QPainter, QColor, QBrush
    p = QPainter(pm)
    p.fillRect(0, top_margin, size, size - top_margin,
               QBrush(QColor(255, 255, 255, 255)))
    p.end()
    return pm


def Qt_transparent():
    from PySide6.QtCore import Qt
    return Qt.transparent


class TestBuildSwapTail(unittest.TestCase):
    """宿主尾段构建的拼接契约（PetWindow.__new__ 借方法，不跑 __init__）。"""

    def _win(self, target="sleep"):
        win = PetWindow.__new__(PetWindow)
        baked = [_solid_pixmap() for _ in range(8)]
        win.states = {
            "shock": {"pixmap": _solid_pixmap(),
                      "frames": [_solid_pixmap() for _ in range(6)],
                      "frame_ms": 33, "transition_pics": baked,
                      "transition": "squash_return", "play": "once"},
            "idle": {"pixmap": _solid_pixmap()},
            "sleep": {"pixmap": _solid_pixmap(top_margin=20)},
        }
        win._swap_tail_pics = None
        return win, baked

    def test_idle_target_keeps_baked_frames(self):
        # 谢幕目标是发呆：烘焙尾段本来就是 idle，不启用运行期换装
        win, _ = self._win(target="idle")
        self.assertIsNone(win._build_swap_tail(win.states["shock"], "idle"))

    def test_no_transition_entry_opted_out(self):
        # 没有换装标记/转场帧的条目（如 vroom）不参与
        win, _ = self._win()
        entry = {"pixmap": _solid_pixmap(), "frames": [], "play": "once"}
        self.assertIsNone(win._build_swap_tail(entry, "sleep"))

    def test_tail_replaces_from_swap_point(self):
        # 头段（换装点之前）逐帧原样保留；尾段换成目标立绘的缩放帧，
        # 总帧数不变（时间线与保险丝口径不动）
        win, baked = self._win()
        tail = win._build_swap_tail(win.states["shock"], "sleep")
        self.assertEqual(len(tail), len(baked))
        swap = tswap.swap_index(len(baked))
        for i in range(swap):
            self.assertIs(tail[i], baked[i], "头段必须原样引用烘焙帧")
        for i in range(swap, len(baked)):
            self.assertIsNot(tail[i], baked[i], "尾段必须是运行期换装帧")
        # 画布纪律（治「转场把桌宠撑大」）：尾段每帧画布恒等于立绘原尺寸，
        # 鼓出在画布缘裁掉——与烘焙侧 _squash_pose 同语义，footprint 永不超标
        base = win.states["sleep"]["pixmap"]
        for i in range(swap, len(baked)):
            self.assertEqual((tail[i].width(), tail[i].height()),
                             (base.width(), base.height()),
                             f"尾段第 {i} 帧画布必须钉死在立绘原尺寸")


if __name__ == "__main__":
    unittest.main()
