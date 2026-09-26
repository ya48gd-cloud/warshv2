@echo off
chcp 65001 >nul
title WarshERP - Update
setlocal
set "HERE=%~dp0"
set "RUNTIME=%HERE%..\WarshERP-runtime"
set "OLD_RUNTIME=C:\WarshERP-runtime"
set "VENV=%RUNTIME%\.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "DATADIR=%RUNTIME%\data"

echo ==================================================
echo    WarshERP - Update (keeps your existing data)
echo ==================================================
echo.
echo [!] Make sure the WarshERP backend window (uvicorn) is closed before continuing.
pause

REM ===== Move old runtime data from C:\WarshERP-runtime if found =====
if not exist "%RUNTIME%" if exist "%OLD_RUNTIME%" (
    echo [MOVE] Found old runtime data on C:\ - moving it next to the program...
    move "%OLD_RUNTIME%" "%RUNTIME%" >nul
    echo       Moved to: %RUNTIME%
)

if not exist "%VENV_PY%" (
    echo [ERROR] Base installation not found - run INSTALL.bat first.
    pause & exit /b 1
)

REM ===== [1/4] Update Python packages =====
echo [1/4] Updating Python packages...
if exist "%HERE%packages" (
    "%VENV_PY%" -m pip install --no-index --find-links "%HERE%packages" -r "%HERE%requirements-desktop.txt" >nul 2>&1
    if errorlevel 1 "%VENV_PY%" -m pip install -r "%HERE%requirements-desktop.txt"
) else (
    "%VENV_PY%" -m pip install -r "%HERE%requirements-desktop.txt"
)
echo       OK

REM ===== [2/4] Rebuild frontend =====
echo [2/4] Building frontend...
where npm >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Node.js/npm not found.
    pause & exit /b 1
)
pushd "%HERE%react-src"
if not exist "node_modules" call npm install
call npm run build
if errorlevel 1 ( echo [ERROR] Frontend build failed. & popd & pause & exit /b 1 )
popd
echo       OK

REM ===== [3/4] Update database schema =====
echo [3/4] Updating database (new tables/columns only)...
pushd "%HERE%"
set "DB_URL_PATH=%DATADIR:\=/%/heavy_erp.db"
set "DATABASE_URL=sqlite+aiosqlite:///%DB_URL_PATH%"
set "ERP_STATIC_DIR=%HERE%react-src\dist"
set "ERP_UPLOAD_DIR=%RUNTIME%\uploads"
set "ENVIRONMENT=desktop"
echo       Database file: %DATADIR%\heavy_erp.db
if not exist "%DATADIR%" mkdir "%DATADIR%"

"%VENV_PY%" -m app.desktop
if errorlevel 1 (
    echo   [ERROR] Database update failed - see the message above.
    popd & pause & exit /b 1
)
popd
echo       OK

REM ===== [4/4] Done =====
echo [4/4] Update complete - your data is intact, all new tables/columns were added.
echo Launch WarshERP from the desktop shortcut.
pause
