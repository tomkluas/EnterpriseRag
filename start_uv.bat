@echo off
setlocal
cd /d "%~dp0"
if /i "%~1"=="--backend" goto backend
if /i "%~1"=="--frontend" goto frontend
where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Install Node.js 18+ and reopen this script.
    goto fail
)
where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Install uv and reopen this script.
    goto fail
)
if not exist "backend\.venv\Scripts\python.exe" (
    call uv venv --python 3.11 "backend\.venv"
    if errorlevel 1 goto fail
)
"backend\.venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)"
if errorlevel 1 (
    echo [ERROR] backend\.venv must use Python 3.11. Rename it and rerun.
    goto fail
)
echo Installing Python dependencies...
call uv pip install --python "backend\.venv\Scripts\python.exe" -r "backend\requirements.txt"
if errorlevel 1 goto fail
pushd frontend
call npm.cmd install
if errorlevel 1 (
    popd
    goto fail
)
popd
echo Starting backend and frontend in separate windows...
echo Open http://localhost:5173 after Vite is ready.
start "RAG Backend" "%ComSpec%" /d /k call "%~f0" --backend
start "RAG Frontend" "%ComSpec%" /d /k call "%~f0" --frontend
exit /b 0

:fail
echo.
echo [ERROR] Operation failed. See the error above.
pause
exit /b 1

:backend
cd /d "%~dp0backend"
echo Backend: http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
exit /b %errorlevel%

:frontend
cd /d "%~dp0frontend"
call npm.cmd run dev -- --host localhost --port 5173 --strictPort --open
exit /b %errorlevel%