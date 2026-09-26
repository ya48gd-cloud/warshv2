@echo off
chcp 65001 >nul
title WarshERP - Full Installer
setlocal
set "HERE=%~dp0"
set "RUNTIME=%HERE%..\WarshERP-runtime"
set "VENV=%RUNTIME%\.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "DATADIR=%RUNTIME%\data"

echo ==================================================
echo    WarshERP - Full Installer
echo ==================================================
echo.

REM ===== [1/5] Check Python =====
echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python not installed or not found in PATH.
    echo   Install Python 3.12 and tick "Add Python to PATH" during setup.
    echo   ^(If you just installed it, close this window and open a NEW one.^)
    pause & exit /b 1
)
REM On some Windows machines, "python" opens the Microsoft Store instead of running
REM Make sure pip actually works, not just that the command returned OK
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] "python" exists but pip does not work.
    echo   This usually means "python" is a Microsoft Store alias, not a real install.
    echo   Disable the alias from:
    echo     Settings ^> Apps ^> Advanced app settings ^> App execution aliases
    echo   Then install Python from python.org.
    pause & exit /b 1
)
echo       OK

REM ===== [2/5] Create venv =====
echo [2/5] Creating Python environment...
if not exist "%VENV_PY%" (
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo   [ERROR] Failed to create Python venv.
        pause & exit /b 1
    )
)
if not exist "%VENV_PY%" (
    echo   [ERROR] venv was created but %VENV_PY% is missing.
    echo   Your Python installation may be corrupted.
    echo   Try reinstalling Python from python.org.
    pause & exit /b 1
)
echo       OK

REM ===== [3/5] Install Python packages =====
echo [3/5] Installing Python packages...
if exist "%HERE%packages" (
    "%VENV_PY%" -m pip install --no-index --find-links "%HERE%packages" -r "%HERE%requirements-desktop.txt" >nul 2>&1
    if errorlevel 1 "%VENV_PY%" -m pip install -r "%HERE%requirements-desktop.txt"
) else (
    "%VENV_PY%" -m pip install -r "%HERE%requirements-desktop.txt"
)
if errorlevel 1 (
    echo   [ERROR] Package installation failed. Check your internet connection.
    pause & exit /b 1
)

"%VENV_PY%" -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Core packages ^(fastapi^) not found after install.
    echo   Try running manually for details:
    echo   "%VENV_PY%" -m pip install -r "%HERE%requirements-desktop.txt"
    pause & exit /b 1
)
echo       OK

REM ===== [4/5] Build frontend =====
echo [4/5] Building frontend...
if exist "%HERE%react-src\dist\index.html" (
    echo       Already built - skipping
) else (
    where npm >nul 2>&1
    if errorlevel 1 (
        echo   [ERROR] Node.js/npm not found. Install Node.js LTS first.
        echo   ^(needed once to build the interface^)
        pause & exit /b 1
    )
    pushd "%HERE%react-src"
    if not exist "node_modules" (
        echo       Installing npm packages ^(needs internet, first time only^)...
        call npm install
        if errorlevel 1 ( echo [ERROR] npm install failed. & popd & pause & exit /b 1 )
    )
    echo       Building...
    call npm run build
    if errorlevel 1 ( echo [ERROR] Frontend build failed. & popd & pause & exit /b 1 )
    popd
)
echo       OK

REM ===== [5/5] Setup database =====
echo [5/5] Setting up database...
if not exist "%DATADIR%" mkdir "%DATADIR%"
pushd "%HERE%"
set "DB_URL_PATH=%DATADIR:\=/%/heavy_erp.db"
set "DATABASE_URL=sqlite+aiosqlite:///%DB_URL_PATH%"
set "ERP_STATIC_DIR=%HERE%react-src\dist"
set "ERP_UPLOAD_DIR=%RUNTIME%\uploads"
if not exist "%RUNTIME%\uploads" mkdir "%RUNTIME%\uploads"
set "ENVIRONMENT=desktop"

"%VENV_PY%" -m app.desktop
if errorlevel 1 (
    echo   [ERROR] Database setup failed - see error message above.
    popd & pause & exit /b 1
)
popd
echo       OK

echo.
echo ==================================================
echo    Installation complete.
echo    Run Start-WarshERP.bat to launch WarshERP.
echo ==================================================
pause
