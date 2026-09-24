@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\ConnMap.exe" (
    start "" "dist\ConnMap.exe"
    exit /b
)
echo 未找到 dist\ConnMap.exe，改用源码启动（需已安装 Python 3.10+）...
python app.py
if errorlevel 1 (
    echo.
    echo 启动失败。请确认已安装 Python，或先运行 build_exe.py 生成 exe。
    pause
)
