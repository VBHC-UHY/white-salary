@echo off
title White Salary - Voice Training
color 0B

echo ============================================================
echo   White Salary - Auto Voice Training (GPT-SoVITS v2)
echo ============================================================
echo.

cd /d "D:\AI_Tools\GPT-SoVITS"
call "venv_new\Scripts\activate.bat"

:: Kill existing GPT-SoVITS on port 9880
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":9880" ^| findstr "LISTENING"') do (
    echo Killing old TTS process (PID: %%a)
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo Starting training script...
echo.

python "D:\White Salary\scripts\train_voice.py"

echo.
pause
