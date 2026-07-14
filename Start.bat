@echo off
setlocal EnableExtensions EnableDelayedExpansion
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
    echo         Run the installer first so dependencies stay isolated in .venv.
    pause
    exit /b 1
)

where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js was not found. The desktop app needs Node.js LTS.
    echo         Install Node.js LTS, then run this launcher again.
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
    echo [ERROR] The main LLM key is missing from conf.yaml, or dependencies are incomplete.
    echo         Run the installer/setup wizard first.
    pause
    exit /b 1
)
set "BACKEND_PORT="
for /f "usebackq delims=" %%p in (`"%PROJECT_PYTHON%" -c "import yaml; c=yaml.safe_load(open(r'%~dp0conf.yaml', encoding='utf-8')) or {}; print(int((c.get('server') or {}).get('port') or 12400))"`) do set "BACKEND_PORT=%%p"
if not defined BACKEND_PORT (
    echo [ERROR] Could not read server.port from conf.yaml.
    pause
    exit /b 1
)

echo [1/4] Checking local TTS server (GPT-SoVITS)...
set "TTS_STATUS=not configured"
call :port_listening 9880
if not errorlevel 1 (
    set "TTS_STATUS=already running"
    echo       A service is already listening on port 9880. Reusing it.
) else (
    set "GPT_SOVITS_DIR="
    for /f "usebackq delims=" %%p in (`"%PROJECT_PYTHON%" "%~dp0scripts\resolve_gpt_sovits_dir.py"`) do set "GPT_SOVITS_DIR=%%p"
    if not defined GPT_SOVITS_DIR (
        echo       GPT-SoVITS path is not configured.
        echo       Set conf.yaml external_tools.gpt_sovits_dir or WS_GPT_SOVITS_DIR.
        echo       Skipping local TTS. Cloud TTS or text fallback can still work.
    ) else if not exist "!GPT_SOVITS_DIR!\api_v2.py" (
        echo       GPT-SoVITS api_v2.py was not found at: !GPT_SOVITS_DIR!
        echo       Skipping local TTS. Check the configured directory.
    ) else if not exist "!GPT_SOVITS_DIR!\venv_new\Scripts\activate.bat" (
        echo       GPT-SoVITS venv_new was not found at: !GPT_SOVITS_DIR!
        echo       Skipping local TTS. Check the GPT-SoVITS installation.
    ) else (
        echo       Starting local TTS from: !GPT_SOVITS_DIR!
        start "WhiteSalary-TTS" /D "!GPT_SOVITS_DIR!" cmd /k "call venv_new\Scripts\activate.bat && python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml"
        echo       Waiting for the TTS port to become ready...
        call :wait_port 9880 90
        if errorlevel 1 (
            set "TTS_STATUS=start timed out"
            echo       [WARN] Local TTS did not become ready within 90 seconds.
            echo              White Salary will continue with cloud TTS or text fallback.
        ) else (
            set "TTS_STATUS=running"
            echo       Local TTS is ready.
        )
    )
)

echo [2/4] Checking backend server...
call :white_salary_health !BACKEND_PORT!
if not errorlevel 1 (
    echo       White Salary backend is already healthy. Reusing it.
) else (
    call :port_listening !BACKEND_PORT!
    if not errorlevel 1 (
        echo [ERROR] Port !BACKEND_PORT! is occupied by another service.
        echo         This launcher will not kill it. Stop that service or change server.port.
        pause
        exit /b 1
    )

    echo       Starting backend server...
    start "WhiteSalary-Backend" /D "%~dp0" cmd /k "set PYTHONPATH=src&& .venv\Scripts\python.exe run_server.py --debug"
    echo       Waiting for a real /health response...
    call :wait_health 45 !BACKEND_PORT!
    if errorlevel 1 (
        echo [ERROR] Backend did not become healthy within 45 seconds.
        echo         Check the WhiteSalary-Backend window for the real error.
        pause
        exit /b 1
    )
    echo       Backend is healthy.
)

echo [3/4] Checking frontend dependencies...
pushd "%~dp0frontend" >nul
if not exist "node_modules\electron\package.json" (
    echo       Frontend dependencies are missing. Running npm install...
    call npm install
    if errorlevel 1 (
        popd >nul
        echo [ERROR] Frontend dependency installation failed.
        echo         Run npm install in the frontend folder and try again.
        pause
        exit /b 1
    )
)
call npx --no-install electron --version >nul 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] Electron is not available in frontend\node_modules.
    echo         Run npm install in the frontend folder and try again.
    pause
    exit /b 1
)
popd >nul
echo       Frontend dependencies are ready.

echo [4/4] Starting desktop app...
start "WhiteSalary-Frontend" /D "%~dp0frontend" cmd /k "npx --no-install electron ."

echo.
echo ============================================================
echo   White Salary launch completed
echo.
echo   Backend:    http://localhost:!BACKEND_PORT!
echo   Health:     http://localhost:!BACKEND_PORT!/health
echo   Local TTS:  !TTS_STATUS!
echo.
echo   Hotkeys:
echo     F12          = Dev Tools
echo     Ctrl+Q       = Quit App
echo     Ctrl+Shift+R = Reload Frontend
echo ============================================================
echo.
pause
exit /b 0

:port_listening
powershell.exe -NoProfile -NonInteractive -Command "$p=[int]'%~1'; if (Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue) { exit 0 }; exit 1" >nul 2>&1
exit /b %errorlevel%

:white_salary_health
"%PROJECT_PYTHON%" -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:%~1/health',timeout=2); d=json.load(r); raise SystemExit(0 if d.get('status') == 'ok' and d.get('name') == 'White Salary' else 1)" >nul 2>&1
exit /b %errorlevel%

:wait_health
for /L %%i in (1,1,%~1) do (
    call :white_salary_health %~2
    if not errorlevel 1 exit /b 0
    timeout /t 1 /nobreak >nul
)
exit /b 1

:wait_port
for /L %%i in (1,1,%~2) do (
    call :port_listening %~1
    if not errorlevel 1 exit /b 0
    timeout /t 1 /nobreak >nul
)
exit /b 1
