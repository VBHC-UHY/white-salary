@echo off
chcp 65001 >nul
title White Salary - Installer
color 0B
cd /d "%~dp0"

set "CHECK_ONLY=0"
if /i "%~1"=="/check" set "CHECK_ONLY=1"

echo ============================================================
echo   White Salary Installer
echo   This creates .venv and installs backend deps into it.
echo ============================================================
echo.

echo [1/6] Checking Python...
set "PYTHON_EXE="
set "PYTHON_SOURCE="

rem Prefer an explicit override, then versioned launchers, then uv-managed
rem interpreters.  A plain `python` is deliberately last because Windows may
rem point it at the Store shim or an unsupported global Python even when uv has
rem already installed a compatible interpreter.
if defined WS_PYTHON call :try_python "%WS_PYTHON%" "" "WS_PYTHON override"
call :try_python "py" "-3.12" "Windows py launcher (3.12)"
call :try_python "py" "-3.11" "Windows py launcher (3.11)"
call :try_python "py" "-3.10" "Windows py launcher (3.10)"
call :try_python "python3.12" "" "python3.12 command"
call :try_python "python3.11" "" "python3.11 command"
call :try_python "python3.10" "" "python3.10 command"
where uv >nul 2>&1
if not errorlevel 1 (
    call :try_uv_python 3.12
    call :try_uv_python 3.11
    call :try_uv_python 3.10
)
call :try_python "python" "" "PATH python"

if not defined PYTHON_EXE (
    echo   [ERROR] No compatible Python was found.
    echo           White Salary requires Python 3.10-3.12.
    echo           The installer checked py, python3.x, uv-managed Python, and python.
    echo           Install 3.11 or 3.12, or set WS_PYTHON to its python.exe path.
    echo           https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('"%PYTHON_EXE%" --version 2^>^&1') do echo   [OK] %%v ^(%PYTHON_SOURCE%^)
echo.

echo [2/6] Checking Node.js...
set "NODE_OK=1"
where node >nul 2>&1
if errorlevel 1 (
    set "NODE_OK=0"
    echo   [WARN] Node.js not found. Backend can be installed; desktop UI needs Node.js LTS.
) else (
    for /f "tokens=*" %%v in ('node --version') do echo   [OK] Node.js %%v
)
echo.

if "%CHECK_ONLY%"=="1" (
    call :check_python_venv_support
    if errorlevel 1 exit /b 1
    for %%f in (pyproject.toml run_server.py conf.default.yaml) do (
        if not exist "%%f" (
            echo   [ERROR] Missing project file: %%f
            exit /b 1
        )
    )
    if exist ".venv" (
        if exist ".venv\Scripts\python.exe" (
            ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if (3, 10) ^<= sys.version_info ^< (3, 13) else 1)" >nul 2>&1
            if errorlevel 1 (
                echo   [INFO] Existing .venv is incompatible and would be recreated safely.
            ) else (
                ".venv\Scripts\python.exe" -m pip --version >nul 2>&1
                if errorlevel 1 (
                    echo   [INFO] Existing .venv has no working pip and would be recreated safely.
                ) else (
                    echo   [OK] Existing project .venv is usable.
                )
            )
        ) else (
            echo   [INFO] Existing .venv is not a Windows virtualenv and would be recreated safely.
        )
    ) else (
        echo   [INFO] A new project .venv would be created.
    )
    echo [CHECK] Done. No install actions were executed.
    exit /b 0
)

echo [3/6] Creating or repairing project virtualenv...
set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
if exist "%~dp0.venv" (
    if not exist "%PROJECT_PYTHON%" (
        echo   [WARN] Existing .venv is not a Windows virtualenv. Recreating it.
        call :remove_project_venv
        if errorlevel 1 exit /b 1
    ) else (
        "%PROJECT_PYTHON%" -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info < (3, 13) else 1)" >nul 2>&1
        if errorlevel 1 (
            echo   [WARN] Existing .venv uses an unsupported Python. Recreating it.
            call :remove_project_venv
            if errorlevel 1 exit /b 1
        ) else (
            "%PROJECT_PYTHON%" -m pip --version >nul 2>&1
            if errorlevel 1 (
                echo   [WARN] Existing .venv has no working pip. Recreating it.
                call :remove_project_venv
                if errorlevel 1 exit /b 1
            )
        )
    )
)
if not exist "%PROJECT_PYTHON%" (
    "%PYTHON_EXE%" -m venv "%~dp0.venv"
    if errorlevel 1 (
        echo   [ERROR] Failed to create .venv with "%PYTHON_EXE%".
        echo           If this is an embeddable Python, install a standard Python build.
        pause
        exit /b 1
    )
)
"%PROJECT_PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] .venv was created without a working pip.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('"%PROJECT_PYTHON%" --version') do echo   [OK] .venv uses %%v
echo.

echo [4/6] Installing Python backend dependencies...
"%PROJECT_PYTHON%" -c "import white_salary, fastapi, uvicorn, websockets, aiofiles, pydantic, yaml, loguru, aiohttp, httpx, numpy, multipart, ddgs, openai, yt_dlp, PIL, mss" >nul 2>&1
if not errorlevel 1 (
    echo   [OK] Python dependencies are already installed.
    goto :deps_done
)
"%PROJECT_PYTHON%" -m pip install -e .
if errorlevel 1 (
    echo.
    echo   [ERROR] Dependency install failed. You can retry with:
    echo       "%PROJECT_PYTHON%" -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)
echo   [OK] Python dependencies installed.
:deps_done
echo.

echo [5/6] Installing desktop frontend dependencies...
if "%NODE_OK%"=="0" (
    echo   [SKIP] Node.js is missing. Install Node.js LTS and rerun this script later.
    goto :npm_done
)
if exist "frontend\node_modules" (
    echo   [OK] Frontend dependencies are already installed.
    goto :npm_done
)
pushd frontend
call npm install
if errorlevel 1 (
    popd
    echo   [WARN] Frontend dependency install failed. Backend chat is not affected.
    echo          You can retry manually: cd frontend && npm install
    goto :npm_done
)
popd
echo   [OK] Frontend dependencies installed.
:npm_done
echo.

echo [6/6] Preparing config files...
if exist "conf.yaml" (
    echo   [OK] conf.yaml exists. Keeping your config.
) else (
    copy /y "conf.default.yaml" "conf.yaml" >nul
    echo   [OK] Created conf.yaml from template.
)
if exist "prompts\system_prompt.txt" (
    echo   [OK] prompts\system_prompt.txt exists.
) else (
    copy /y "prompts\system_prompt.example.txt" "prompts\system_prompt.txt" >nul
    echo   [OK] Created prompts\system_prompt.txt from example.
)
echo.

echo ============================================================
echo   Install complete. Opening setup wizard...
echo ============================================================
echo.
"%PROJECT_PYTHON%" scripts\setup_wizard.py
if errorlevel 1 (
    echo   [WARN] Setup wizard did not finish normally. You can rerun:
    echo       "%PROJECT_PYTHON%" scripts\setup_wizard.py
    pause
    exit /b 1
)
exit /b 0

:try_python
if defined PYTHON_EXE exit /b 0
set "WS_PY_PROBE=%TEMP%\white_salary_python_%RANDOM%_%RANDOM%.txt"
"%~1" %~2 -c "import sys; assert (3, 10) <= sys.version_info < (3, 13); print(sys.executable)" >"%WS_PY_PROBE%" 2>nul
if errorlevel 1 (
    del /q "%WS_PY_PROBE%" >nul 2>&1
    exit /b 0
)
set /p "PYTHON_EXE="<"%WS_PY_PROBE%"
del /q "%WS_PY_PROBE%" >nul 2>&1
if defined PYTHON_EXE set "PYTHON_SOURCE=%~3"
exit /b 0

:try_uv_python
if defined PYTHON_EXE exit /b 0
set "WS_UV_PROBE=%TEMP%\white_salary_uv_python_%RANDOM%_%RANDOM%.txt"
call uv python find --no-python-downloads --no-cache %~1 >"%WS_UV_PROBE%" 2>nul
if errorlevel 1 (
    del /q "%WS_UV_PROBE%" >nul 2>&1
    exit /b 0
)
set "WS_UV_PYTHON="
set /p "WS_UV_PYTHON="<"%WS_UV_PROBE%"
del /q "%WS_UV_PROBE%" >nul 2>&1
if defined WS_UV_PYTHON call :try_python "%WS_UV_PYTHON%" "" "uv-managed Python %~1"
exit /b 0

:check_python_venv_support
"%PYTHON_EXE%" -m venv --help >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Selected Python has no working venv module: "%PYTHON_EXE%"
    exit /b 1
)
"%PYTHON_EXE%" -m ensurepip --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Selected Python cannot seed pip into a virtualenv: "%PYTHON_EXE%"
    exit /b 1
)
echo   [OK] Python can create an isolated virtualenv with pip.
exit /b 0

:remove_project_venv
set "WS_VENV_TARGET=%~dp0.venv"
set "WS_PROJECT_ROOT=%~dp0"
powershell.exe -NoProfile -NonInteractive -Command "$p=[IO.Path]::GetFullPath($env:WS_VENV_TARGET); $expected=[IO.Path]::GetFullPath((Join-Path $env:WS_PROJECT_ROOT '.venv')); if($p -ne $expected){exit 10}; $item=Get-Item -LiteralPath $p -Force; if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){exit 11}; $cfg=Join-Path $p 'pyvenv.cfg'; if(Test-Path -LiteralPath $cfg){$t=Get-Content -LiteralPath $cfg -Raw; if($t -notmatch '(?m)^home\s*='){exit 12}; if($t -notmatch '(?m)^(version|version_info)\s*='){exit 13}} elseif((Get-ChildItem -LiteralPath $p -Force | Select-Object -First 1)){exit 14}"
if errorlevel 1 (
    echo   [ERROR] Refusing to remove .venv because it could not be verified as this project's virtualenv.
    echo           Move or remove it manually after checking its contents:
    echo           "%WS_VENV_TARGET%"
    pause
    exit /b 1
)
rmdir /s /q "%WS_VENV_TARGET%"
if exist "%WS_VENV_TARGET%" (
    echo   [ERROR] Could not remove the old project .venv.
    pause
    exit /b 1
)
exit /b 0
