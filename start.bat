@echo off
@rem 从脚本所在目录启动桌面入口，并透传全部参数。
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\v3\start_desktop.ps1" %*
@rem 保存启动器退出码，避免后续暂停命令覆盖真实结果。
set "launch_result=%errorlevel%"
@rem 首参数为 -NoPause 时跳过等待，便于自动调用。
if /I "%~1"=="-NoPause" goto finish
echo.
pause
:finish
exit /b %launch_result%
