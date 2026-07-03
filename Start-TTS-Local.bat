@echo off
title White Salary - Local TTS Server (GPT-SoVITS)
color 0B

echo ============================================================
echo   White Salary - Local TTS Server (GPT-SoVITS)
echo ============================================================
echo.

:: Kill any existing process on port 9880
echo Checking port 9880...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":9880" ^| findstr "LISTENING"') do (
    echo   Killing existing process on port 9880 (PID: %%a)
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

cd /d "D:\AI_Tools\GPT-SoVITS"
call "venv_new\Scripts\activate.bat"

echo.
echo Starting GPT-SoVITS API on port 9880...
echo   Press Ctrl+C to stop.
echo.

python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

pause
