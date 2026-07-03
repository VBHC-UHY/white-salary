@echo off
title White Salary - Backend
color 0B

echo ============================================================
echo   White Salary - Backend Server
echo ============================================================
echo.

cd /d "%~dp0"
set PYTHONPATH=src
python run_server.py --debug

pause
