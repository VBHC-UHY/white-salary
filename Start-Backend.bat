@echo off
chcp 65001 >nul
title White Salary - Backend
color 0B

echo ============================================================
echo   White Salary - Backend Server
echo ============================================================
echo.

cd /d "%~dp0"
set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PROJECT_PYTHON%" (
    echo [ERROR] Missing project virtualenv: .venv\Scripts\python.exe
    echo         Run the one-click installer first so dependencies are isolated in .venv.
    pause
    exit /b 1
)

set PYTHONPATH=src
"%PROJECT_PYTHON%" run_server.py --debug

pause
