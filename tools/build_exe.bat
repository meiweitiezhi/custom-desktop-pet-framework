@echo off
rem 一键打包团子：产出 dist\CustomPetFramework.exe（单文件、已含 states 成品图与 manifest）。
rem 打包前请确认 assets\states\ 下有成品图（放入原图后运行 python prep_assets.py）。
rem build\ 与 dist\ 是构建产物，已在 .gitignore，不要提交。
cd /d "%~dp0.."
python -m PyInstaller --noconfirm --clean tools/petfw.spec
if errorlevel 1 (
  echo.
  echo [失败] 请先安装打包依赖：pip install pyinstaller
  exit /b 1
)
echo.
echo 完成。产物在 dist\CustomPetFramework.exe；首次运行会在 exe 旁边生成 config.ini 和 runtime.json（属预期）。
echo 补充新表情时：把 assets\raw\<状态名>.png 放好 -^> python prep_assets.py -^> 重新打包。
