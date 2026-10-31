@echo off
chcp 65001 >nul
title 清理 C 盘下载缓存
echo 只在翻唱环境安装窗口显示"全部完成"或已经关掉之后再运行！
echo.
pause
"%~dp0Applio\env\python.exe" -m uv cache clean
"%~dp0Applio\env\python.exe" -m pip cache purge
echo.
echo 清理完成，窗口可以关掉了。
pause
