@echo off
cd /d "%~dp0"
title AstrBot
rem ffmpeg extracted by pyffmpeg, needed for Edge TTS voice
set "PATH=%USERPROFILE%\.pyffmpeg\bin;%PATH%"
".venv\Scripts\python.exe" main.py
pause
