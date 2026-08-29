# -*- mode: python ; coding: utf-8 -*-
"""团子单文件 exe 打包配置（入库的是这份配置，不是构建产物）。

用法：在仓库根执行 `pyinstaller tools/petfw.spec`，或直接跑 tools/build_exe.bat。

要点：
- 运行期资源路径解析统一走 petfw/paths.py：frozen 时只读素材读
  sys._MEIPASS/assets，config.ini / runtime.json 落在 exe 同目录；
- datas 只带 manifest.json 与 states/*.png 成品图——绝不带 assets/raw/ 原图
  （版权红线：原始素材不随产物扩散，也不入仓库）；
- onefile 单文件：EXE 里直接吃掉 binaries 与 datas，不写 COLLECT；
- 构建产物 build/ 与 dist/ 均已 .gitignore，永不提交。
"""
from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()      # 本文件所在目录（tools/）
ROOT = SPEC_DIR.parent                   # 仓库根
ASSETS = ROOT / "assets"

for need in ("manifest.json", "states"):
    if not (ASSETS / need).exists():
        raise SystemExit(
            f"缺少 assets/{need} —— 先放入 assets/raw/*.png "
            "并运行 python prep_assets.py 生成成品图，再打包。")

datas = [
    (str(ASSETS / "manifest.json"), "assets"),
    (str(ASSETS / "states"), "assets/states"),
    # 私有音频随 exe 分发（主人拍板：朋友版开箱即唱；仓库零音频红线不破）
    (str(ROOT / "assets" / "local" / "click.wav"), "assets/local"),
    (str(ROOT / "assets" / "local" / "bgm.mp3"), "assets/local"),
]

a = Analysis(
    [str(ROOT / "tools" / "petfw_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["petfw", "petfw.host", "petfw.drivers.rule",
                   "petfw.drivers.llm"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CustomPetFramework",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # 桌宠是无窗口程序；控制台日志用 --smoke 时经 stdout
    disable_windowed_traceback=False,
)
