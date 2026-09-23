@echo off
cd /d "%~dp0"
title hoplite-pc-bridge
echo Starting MCP proxy + ngrok tunnel...
echo Add --verify to prove the public endpoint answers, e.g.:  start_bridge.bat --verify
python bridge.py %*
echo.
echo Bridge stopped. Press any key to close.
pause >nul
