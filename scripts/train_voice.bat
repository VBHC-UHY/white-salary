@echo off
chcp 65001 >nul
title White Salary - Voice Training
color 0B

echo ============================================================
echo   White Salary - Auto Voice Training (GPT-SoVITS v2)
echo ============================================================
echo.

cd /d "%~dp0.."
set "PROJECT_ROOT=%CD%"
set "PROJECT_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PROJECT_PYTHON%" set "PROJECT_PYTHON=python"

set "GPT_SOVITS_DIR="
for /f "usebackq delims=" %%p in (`"%PROJECT_PYTHON%" "%PROJECT_ROOT%\scripts\resolve_gpt_sovits_dir.py"`) do set "GPT_SOVITS_DIR=%%p"
if not defined GPT_SOVITS_DIR (
    echo [ERROR] GPT-SoVITS path is not configured.
    echo         Set conf.yaml external_tools.gpt_sovits_dir or WS_GPT_SOVITS_DIR first.
    pause
    exit /b 1
)

if not exist "%GPT_SOVITS_DIR%\venv_new\Scripts\activate.bat" (
    echo [ERROR] GPT-SoVITS venv_new not found:
    echo         %GPT_SOVITS_DIR%\venv_new\Scripts\activate.bat
    echo         Set conf.yaml external_tools.gpt_sovits_dir first.
    pause
    exit /b 1
)

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":9880" ^| findstr "LISTENING"') do (
    echo Killing old TTS process (PID: %%a)
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

cd /d "%GPT_SOVITS_DIR%"
call venv_new\Scripts\activate.bat

echo Starting training script...
echo.

python "%PROJECT_ROOT%\scripts\train_voice.py"

echo.
pause
