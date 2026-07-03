@echo off
title White Salary - Desktop App
color 0B

echo ============================================================
echo   White Salary - Desktop App
echo ============================================================
echo.

cd /d "%~dp0frontend"

if not exist node_modules (
    echo First run - installing dependencies...
    echo This may take a few minutes, please wait...
    echo.
    call npm install
    echo.
    if %errorlevel% neq 0 (
        echo [ERROR] npm install failed!
        echo Please check your Node.js installation.
        pause
        exit /b 1
    )
    echo Dependencies installed successfully!
    echo.
)

echo Starting Electron...
echo.
call npx electron .
echo.
echo ============================================================
echo   Electron has exited. Check above for any errors.
echo ============================================================
pause
