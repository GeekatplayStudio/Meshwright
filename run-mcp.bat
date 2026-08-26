@echo off
setlocal
title Meshwright MCP server
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Meshwright is not installed yet - run install.bat first.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import mcp" 2>nul
if errorlevel 1 (
    echo.
    echo   The MCP package is not installed in this environment.
    echo   Install it with:
    echo     .venv\Scripts\python -m pip install mcp
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "mcp_server.py"
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" pause
exit /b %CODE%
