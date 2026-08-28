"""PyInstaller 打包专用入口：保持极薄，全部逻辑在 petfw 包里。

run.py 也可以直接当入口用；独立 launcher 是为了让 spec 的 pathex 归属
更清晰（tools/ 入口 + 仓库根模块搜索路径），且不夹带开发期习惯参数。
"""
import sys

if __name__ == "__main__":
    from petfw.host import main
    sys.exit(main())
