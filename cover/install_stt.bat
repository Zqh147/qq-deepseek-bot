@echo off
chcp 65001 >nul
title 语音识别安装
cd /d "%~dp0"
rem 找一个 Python 3.10+ 来跑安装脚本：优先 conda 自带的，其次 PATH 里的 python
set "PY="
if defined CONDA_PYTHON_EXE if exist "%CONDA_PYTHON_EXE%" set "PY=%CONDA_PYTHON_EXE%"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set "PY=%USERPROFILE%\miniconda3\python.exe"
if not defined PY set "PY=python"
"%PY%" -u install_stt.py
echo.
echo 窗口可以关掉了。
pause
