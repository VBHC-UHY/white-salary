@echo off
chcp 65001 >nul
title White Salary - Launcher
color 0B
cd /d "%~dp0"

echo ============================================================
echo   White Salary - One Click Launcher
echo ============================================================
echo.

set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PROJECT_PYTHON%" (
    echo [ERROR] Missing project virtualenv: .venv\Scripts\python.exe
    echo         Run the installer first so dependencies are isolated in .venv.
    pause
    exit /b 1
)

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found. Please install Node.js LTS.
    pause
    exit /b 1
)

if not exist "%~dp0conf.yaml" (
    echo [ERROR] Missing conf.yaml. Run the installer first.
    pause
    exit /b 1
)
"%PROJECT_PYTHON%" -c "import sys, yaml; c = yaml.safe_load(open(r'%~dp0conf.yaml', encoding='utf-8')) or {}; sys.exit(0 if str((c.get('llm') or {}).get('api_key') or '').strip() else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Main LLM key is missing from conf.yaml, or dependencies are incomplete.
    echo         Run the installer/setup wizard first.
    pause
    exit /b 1
)

echo [0/4] Cleaning up old processes...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":12400" ^| findstr "LISTENING"') do (
    echo       Killing old backend (PID: %%a)
    taskkill /PID %%a /F >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":9880" ^| findstr "LISTENING"') do (
    echo       Killing old TTS (PID: %%a)
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo [1/4] Checking local TTS server (GPT-SoVITS)...
netstat -aon | findstr ":9880" | findstr "LISTENING" >nul 2>&1
if %errorlevel% neq 0 (
    set "GPT_SOVITS_DIR="
    for /f "usebackq delims=" %%p in (`"%PROJECT_PYTHON%" "%~dp0scripts\resolve_gpt_sovits_dir.py"`) do set "GPT_SOVITS_DIR=%%p"
    if not defined GPT_SOVITS_DIR (
        echo       GPT-SoVITS path is not configured.
        echo       Set conf.yaml external_tools.gpt_sovits_dir or WS_GPT_SOVITS_DIR.
        echo       Skipping local TTS. Cloud TTS / text fallback can still work.
    ) else (
        if exist "%GPT_SOVITS_DIR%\api_v2.py" (
            if exist "%GPT_SOVITS_DIR%\venv_new\Scripts\activate.bat" (
                echo       Starting local TTS from: %GPT_SOVITS_DIR%
                start "WhiteSalary-TTS" /D "%GPT_SOVITS_DIR%" cmd /k "call venv_new\Scripts\activate.bat && python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml"
                echo       TTS loading... waiting 45s for model to load
                timeout /t 45 /nobreak >nul
            ) else (
                echo       GPT-SoVITS venv_new not found at: %GPT_SOVITS_DIR%
                echo       Skipping local TTS. Cloud TTS / text fallback can still work.
            )
        ) else (
            echo       GPT-SoVITS not found at: %GPT_SOVITS_DIR%
            echo       Skipping local TTS. Cloud TTS / text fallback can still work.
        )
    )
) else (
    echo       TTS already running, skipping.
)

echo [2/4] Starting backend server...
start "WhiteSalary-Backend" /D "%~dp0" cmd /k "set PYTHONPATH=src && .venv\Scripts\python.exe run_server.py --debug"
echo       Waiting for backend...
timeout /t 5 /nobreak >nul

echo [3/4] Checking frontend dependencies...
cd /d "%~dp0frontend"
if not exist node_modules (
    echo       First run - installing npm packages...
    call npm install
)

echo [4/4] Starting desktop app...
start "WhiteSalary-Frontend" /D "%~dp0frontend" cmd /k "npx electron ."

echo.
echo ============================================================
echo   White Salary is running!
echo.
echo   Backend:    http://localhost:12400
echo   Local TTS:  http://localhost:9880
echo   Health:     http://localhost:12400/health
echo.
echo   Hotkeys:
echo     F12          = Dev Tools
echo     Ctrl+Q       = Quit App
echo     Ctrl+Shift+R = Reload Frontend
echo ============================================================
echo.
pause
