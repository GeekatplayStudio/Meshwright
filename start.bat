@echo off
setlocal
title Meshwright
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Meshwright is not installed yet.
    echo   Run install.bat first - it takes a couple of minutes and puts
    echo   everything in a .venv folder inside this directory.
    echo.
    pause
    exit /b 1
)

echo ============================================================
echo   Meshwright - Geekatplay Studio
echo.
echo   This window is the activity log. Keep it open while you
echo   work; closing it closes Meshwright.
echo.
echo   Meshwright has no model of its own - open one of your own
echo   files, or click "Load demo model" in the empty viewport.
echo ============================================================
echo.

".venv\Scripts\python.exe" "app.py"
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo   Meshwright closed with error code %CODE%.
    echo   Read the message above; if it mentions a missing module, run:
    echo     install.bat -Recreate
    echo.
    pause
)
exit /b %CODE%
