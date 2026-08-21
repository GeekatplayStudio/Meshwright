@echo off
title Meshwright MCP server
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0mcp_server.py"
) else (
    python "%~dp0mcp_server.py"
)
