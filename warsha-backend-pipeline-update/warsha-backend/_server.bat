@echo off
chcp 65001 >nul
title WarshERP Server
set "HERE=%~dp0"
set "RUNTIME=%HERE%..\WarshERP-runtime"
set "VENV_PY=%RUNTIME%\.venv\Scripts\python.exe"
cd /d "%HERE%"

if not exist "%VENV_PY%" (
    echo.
    echo ==================================================
    echo    [ERROR] WarshERP is not installed on this machine
    echo ==================================================
    echo    Python environment ^(venv^) not found on this machine.
    echo    Each PC needs its own installation ^(run once^).
    echo    You cannot copy the program folder from another PC
    echo    and expect it to work immediately.
    echo.
    echo    Solution: Run INSTALL.bat first ^(one time only^),
    echo    then use Start-WarshERP.bat normally each time.
    echo ==================================================
    pause
    exit /b 1
)

set "DB_URL_PATH=%RUNTIME:\=/%/data/heavy_erp.db"
set "DATABASE_URL=sqlite+aiosqlite:///%DB_URL_PATH%"
set "ERP_STATIC_DIR=%HERE%react-src\dist"
set "ERP_UPLOAD_DIR=%RUNTIME%\uploads"
set "ENVIRONMENT=desktop"
if not exist "%RUNTIME%\uploads" mkdir "%RUNTIME%\uploads"

"%VENV_PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
