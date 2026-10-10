@echo off
chcp 65001 >nul
title 西安地铁站名与站徽一键矢量化工具

echo ========================================================
echo        西安地铁站名与站徽 · 全自动一键矢量化构建
echo ========================================================
echo.

cd /d "%~dp0"

set "PY_EXE=C:\Users\shouh\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "PYTHONPATH=%~dp0.deps"

if not exist "%PY_EXE%" (
    echo [错误] 未检测到配套运行环境，请确认环境完整。
    pause
    exit /b 1
)

echo [1/2] 正在提取车站站名书法与文化站徽 (透明 PNG)...
echo [2/2] 正在调用 Potrace 引擎拟合贝塞尔曲线 (矢量 SVG)...
echo.

"%PY_EXE%" export_station_vectors.py

echo.
echo ========================================================
echo 构建完成！输出目录：
echo   - 位图切片: 车站站名与站徽\
echo   - 矢量文件: 车站站名与站徽_SVG\
echo ========================================================
echo.
pause