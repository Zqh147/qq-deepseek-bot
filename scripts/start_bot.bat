@echo off
chcp 65001 >nul
title Start QQ bot
rem 放在 AstrBot 和 NapCat 两个文件夹的上一级使用：
rem   <这里>\AstrBot\start_astrbot.bat
rem   <这里>\NapCat\NapCat.xxxxx.Shell\napcat.quick.bat
set "NAPCAT_DIR="
for /d %%D in ("%~dp0NapCat\NapCat.*.Shell") do set "NAPCAT_DIR=%%D"
if not defined NAPCAT_DIR (
  echo Cannot find NapCat\NapCat.*.Shell next to this script.
  pause
  exit /b 1
)
echo Starting AstrBot ...
start "AstrBot" /D "%~dp0AstrBot" cmd /c start_astrbot.bat
echo Waiting 10 seconds for AstrBot ...
timeout /t 10 /nobreak >nul
echo Starting NapCat (quick login) ...
start "NapCat" /D "%NAPCAT_DIR%" cmd /c napcat.quick.bat
echo Done. Keep the two new windows open.
timeout /t 5 >nul
