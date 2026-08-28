"""统一的资源与可写文件路径解析（开发态 / PyInstaller frozen 态通吃）。

PyInstaller onefile 下 sys.__file__ 指向临时解包目录，自己算 ROOT 会算错：
- 只读素材（assets/）随包分发：frozen 时从 sys._MEIPASS/assets 读；
- config.ini / runtime.json 必须可写且重启后仍在：frozen 时放 exe 同目录
  （Path(sys.executable).parent），这样用户双击 exe 也能持久记住配置与位置。
开发态两者都指向仓库根。
"""
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = Path(sys.executable).parent      # exe 所在目录（可写）
else:
    BUNDLE_DIR = Path(__file__).resolve().parents[1]   # 仓库根
    APP_DIR = BUNDLE_DIR

ASSETS = BUNDLE_DIR / "assets"          # 只读、随包分发
CONFIG_PATH = APP_DIR / "config.ini"    # 可写、放用户能看到的地方
RUNTIME_PATH = APP_DIR / "runtime.json"
