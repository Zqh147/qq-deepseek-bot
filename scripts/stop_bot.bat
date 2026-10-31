@echo off
chcp 65001 >nul
title Stop QQ bot
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_bot.ps1"
echo.
echo The bot has been stopped. You can close the leftover black windows.
pause
