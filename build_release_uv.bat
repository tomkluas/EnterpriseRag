@echo off
setlocal
cd /d "%~dp0"
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
echo Building release. This may take several minutes...
"backend\.venv\Scripts\python.exe" backend\scripts\build_exe.py --output-dir "%~dp0release"
if errorlevel 1 goto fail
echo.
echo Release is ready in: "%~dp0release"
echo Double-click the executable in that folder to start the system.
pause
exit /b 0

:fail
echo.
echo [ERROR] Operation failed. See the error above.
pause
exit /b 1
