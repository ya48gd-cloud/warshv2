@echo off
setlocal
cd /d "%~dp0"

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "ELECTRON_EXE=%APP_DIR%\node_modules\electron\dist\electron.exe"

if exist "%ELECTRON_EXE%" (
  start "" /D "%APP_DIR%" "%ELECTRON_EXE%" "%APP_DIR%"
  exit /b 0
)

where npm >nul 2>nul
if errorlevel 1 (
  echo Electron runtime was not found, and npm is not installed.
  echo Please run Setup-WarshERP.cmd from the original package, or install Node.js and try again.
  pause
  exit /b 1
)

if not exist node_modules\electron (
  echo Installing desktop runtime...
  npm install
  if errorlevel 1 (
    echo Failed to install desktop runtime.
    pause
    exit /b 1
  )
)

npm run desktop
