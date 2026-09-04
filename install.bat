@echo off
setlocal
title Meshwright Installer - Geekatplay Studio
cd /d "%~dp0"

rem Find PowerShell. The full path is used first because a broken PATH is one of
rem the reasons an installation fails in the first place.
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"
where "%PS%" >nul 2>&1
if errorlevel 1 set "PS=pwsh.exe"
where "%PS%" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [ERROR] Windows PowerShell was not found on this computer.
    echo   Meshwright needs it to install. Please repair Windows or install
    echo   PowerShell from https://aka.ms/powershell
    echo.
    pause
    exit /b 1
)

rem Files extracted from a downloaded ZIP are marked "blocked" by Windows.
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0*.ps1','%~dp0scripts\*.ps1' -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo   Installation did not finish. The details are in:
    echo     %~dp0install-log.txt
    echo.
    echo   Useful options:
    echo     install.bat -Recreate       build the environment again from scratch
    echo     install.bat -Check          show what Python this machine has
    echo     install.bat -NoOptional     install only the required packages
    echo     install.bat -ComfyUI <path> specify ComfyUI install folder directly
    echo     install.bat -SkipComfyUI    skip the ComfyUI nodes step
    echo.
)
pause
exit /b %CODE%
