@echo off
chcp 65001 >nul
title 西安地铁站名书法字体 · 全套字库一键构建工具

echo ========================================================
echo        西安地铁站名书法字体 · 全套字库一键构建
echo ========================================================
echo.

cd /d "%~dp0"

set "ENV_EXE=C:\Users\shouh\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "PYTHONPATH=%~dp0.deps"

if not exist "%ENV_EXE%" (
    echo [错误] 未检测到配套构建环境，请确认环境完整。
    pause
    exit /b 1
)

echo [1/3] 正在扫描母牌图样并自动切分单字...
"%ENV_EXE%" auto_slice_new_line_stations.py
if errorlevel 1 (
    echo [错误] 母牌切单字处理中断！
    pause
    exit /b 1
)

echo.
echo [2/3] 正在增量同步元数据并提取矢量字模...
"%ENV_EXE%" sync_and_rebuild_cache.py
if errorlevel 1 (
    echo [错误] 字模缓存处理中断！
    pause
    exit /b 1
)

echo.
echo [3/3] 正在自适应编译各线路字重、全线合集 TTC 与综合字库...
"%ENV_EXE%" build_metro_font.py
if errorlevel 1 (
    echo [错误] 字体编译中断！
    pause
    exit /b 1
)

echo.
echo ========================================================
echo 构建完成！全套产物已输出至 fonts 目录：
echo   - 综合字库: fonts\XianMetro_StationNames.ttf
echo   - 全线合集: fonts\XianMetro_StationNames.ttc
echo   - 分线字重: fonts\family_weights\ (涵盖所有线路 TTF 与 OTF)
echo ========================================================
echo.
pause
