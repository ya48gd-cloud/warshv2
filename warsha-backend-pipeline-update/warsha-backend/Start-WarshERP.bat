@echo off
chcp 65001 >nul
title WarshERP
setlocal
set "HERE=%~dp0"
set "URL=http://localhost:8000"
set "VENV_PY=%HERE%..\WarshERP-runtime\.venv\Scripts\python.exe"

echo.
echo   WarshERP - Starting...
echo.

netstat -an | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo   Already running - opening browser...
    start "" "%URL%"
    exit /b 0
)

if not exist "%VENV_PY%" (
    echo.
    echo   [ERROR] WarshERP is not installed on this machine.
    echo   Please run INSTALL.bat first ^(only needed once^),
    echo   then use this file normally after that.
    echo.
    pause
    exit /b 1
)

echo   Starting server...
start "WarshERP Server" "%HERE%_server.bat"

echo   Waiting for server...
set /a TRIES=0
:WAIT
ping -n 2 127.0.0.1 >nul
set /a TRIES+=1
netstat -an | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 goto READY
if %TRIES% GEQ 30 goto TIMEOUT
goto WAIT

:TIMEOUT
echo.
echo   [ERROR] Server did not start within a reasonable time.
echo   Check the "WarshERP Server" window for error details.
echo.
pause
exit /b 1

:READY
ping -n 2 127.0.0.1 >nul
echo   Opening browser...
start "" "%URL%"
exit /b 0
