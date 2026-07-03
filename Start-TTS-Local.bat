@echo off
chcp 65001 >nul
title White Salary - Local TTS Server (GPT-SoVITS)
color 0B

echo ============================================================
echo   White Salary - Local TTS Server (GPT-SoVITS)
echo ============================================================
echo.

echo Checking port 9880...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":9880" ^| findstr "LISTENING"') do (
    echo   Killing existing process on port 9880 (PID: %%a)
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

cd /d "%~dp0"
set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PROJECT_PYTHON%" set "PROJECT_PYTHON=python"

set "GPT_SOVITS_DIR="
for /f "usebackq delims=" %%p in (`"%PROJECT_PYTHON%" "%~dp0scripts\resolve_gpt_sovits_dir.py"`) do set "GPT_SOVITS_DIR=%%p"
if not defined GPT_SOVITS_DIR (
    echo [ERROR] GPT-SoVITS path is not configured.
    echo         Set conf.yaml external_tools.gpt_sovits_dir or WS_GPT_SOVITS_DIR.
    pause
    exit /b 1
)

if not exist "%GPT_SOVITS_DIR%\api_v2.py" (
    echo [ERROR] GPT-SoVITS not found: %GPT_SOVITS_DIR%
    echo         Set conf.yaml external_tools.gpt_sovits_dir.
    pause
    exit /b 1
)
if not exist "%GPT_SOVITS_DIR%\venv_new\Scripts\activate.bat" (
    echo [ERROR] GPT-SoVITS venv_new not found:
    echo         %GPT_SOVITS_DIR%\venv_new\Scripts\activate.bat
    pause
    exit /b 1
)

cd /d "%GPT_SOVITS_DIR%"
call venv_new\Scripts\activate.bat

echo.
echo Starting GPT-SoVITS API on port 9880...
echo   Press Ctrl+C to stop.
echo.

python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

pause
