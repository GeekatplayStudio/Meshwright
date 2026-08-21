@echo off
title Meshwright - Installer
echo ========================================================
echo   Meshwright Installer - Geekatplay Studio
echo ========================================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10 or higher from https://python.org
    pause
    exit /b 1
)

echo [1/3] Upgrading Pip...
python -m pip install --upgrade pip

echo.
echo [2/3] Installing Python 3D Mesh Processing Libraries...
python -m pip install -r requirements.txt

echo.
echo [3/3] Installing Frontend UI Dependencies...
call npm install

echo.
echo ========================================================
echo   INSTALLATION COMPLETE SUCCESSFUL!
echo   Run start.bat to launch the application.
echo ========================================================
pause
