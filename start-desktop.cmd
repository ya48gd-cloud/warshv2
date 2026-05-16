@echo off
setlocal
cd /d "%~dp0"

where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found. Please install Node.js from https://nodejs.org, then run this file again.
  pause
  exit /b 1
)

if not exist node_modules\electron (
  echo Installing Electron dependencies...
  npm install
  if errorlevel 1 (
    echo npm install failed.
    pause
    exit /b 1
  )
)

npm run desktop
