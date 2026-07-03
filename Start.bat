@echo off
:: 2026-07-03 新手体验（批10）：本文件保存为 UTF-8（无BOM），用 chcp 65001 正确显示下方中文提示
chcp 65001 >nul
title White Salary - Launcher
color 0B

echo ============================================================
echo   White Salary - One Click Launcher
echo ============================================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11+
    pause
    exit /b 1
)

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found. Please install Node.js
    pause
    exit /b 1
)

:: 2026-07-03 新手体验（批10）：启动前确认已完成初始化——conf.yaml 存在且主 LLM 密钥已填，
:: 缺任意一样就指引先双击 安装.bat（不改动本脚本其它逻辑）
if not exist "%~dp0conf.yaml" (
    echo [ERROR] 还没有配置文件 conf.yaml —— 请先双击 安装.bat 完成初始化。
    pause
    exit /b 1
)
python -c "import sys, yaml; c = yaml.safe_load(open(r'%~dp0conf.yaml', encoding='utf-8')) or {}; sys.exit(0 if str((c.get('llm') or {}).get('api_key') or '').strip() else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] conf.yaml 里还没填主 LLM 密钥（或依赖未装齐）—— 请先双击 安装.bat 完成初始化。
    pause
    exit /b 1
)

:: Kill any existing processes on our ports
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

:: Step 1: Start TTS (skip if already running)
echo [1/4] Starting local TTS server (GPT-SoVITS)...
netstat -aon | findstr ":9880" | findstr "LISTENING" >nul 2>&1
if %errorlevel% neq 0 (
    start "WhiteSalary-TTS" cmd /k "cd /d D:\AI_Tools\GPT-SoVITS && call venv_new\Scripts\activate.bat && python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml"
    echo       TTS loading... waiting 45s for model to load
    timeout /t 45 /nobreak >nul
) else (
    echo       TTS already running, skipping.
)

:: Step 2: Start backend
echo [2/4] Starting backend server...
cd /d "%~dp0"
start "WhiteSalary-Backend" cmd /k "cd /d "%~dp0" && set PYTHONPATH=src && python run_server.py --debug"
echo       Waiting for backend...
timeout /t 5 /nobreak >nul

:: Step 3: Check frontend deps
echo [3/4] Checking frontend dependencies...
cd /d "%~dp0frontend"
if not exist node_modules (
    echo       First run - installing npm packages...
    call npm install
)

:: Step 4: Start frontend
echo [4/4] Starting desktop app...
start "WhiteSalary-Frontend" cmd /k "cd /d "%~dp0frontend" && npx electron ."

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
