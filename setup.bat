@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\v3\deploy.ps1" %*
set "setup_result=%errorlevel%"
exit /b %setup_result%
