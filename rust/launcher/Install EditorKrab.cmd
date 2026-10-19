@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (echo. & echo Install failed. & pause & exit /b 1)
start "" wscript.exe "%LOCALAPPDATA%\Programs\EditorKrab\EditorKrab.vbs"
