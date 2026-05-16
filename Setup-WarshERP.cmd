@echo off
setlocal
cd /d "%~dp0"

:: Strip trailing backslash from %~dp0 to avoid Resolve-Path issues
set "root=%~dp0"
if "%root:~-1%"=="\" set "root=%root:~0,-1%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%root%\installer\setup-gui.ps1" -SourceRoot "%root%"
if errorlevel 1 pause
