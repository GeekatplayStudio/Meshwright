@echo off
title Stop Meshwright
echo Stopping Meshwright background processes...
taskkill /F /FI "WINDOWTITLE eq Meshwright*" >nul 2>&1
echo Done.
