@echo off
title White Salary - GPT-SoVITS TTS Server
color 0B

echo ============================================================
echo   White Salary - TTS Voice Server (GPT-SoVITS)
echo ============================================================
echo.

cd /d "D:\AI_Tools\GPT-SoVITS"

echo Loading Neuro V2 voice model...
echo.

call "venv_new\Scripts\activate.bat"

python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

pause
