@echo off
cd /d "%~dp0"
python bridge.py --stop
echo Press any key to close.
pause >nul
