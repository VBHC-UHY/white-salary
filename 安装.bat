@echo off
rem ============================================================
rem  White Salary 一键安装脚本（2026-07-03 新手体验（批10））
rem  给完全新手：双击运行，自动检查环境、装依赖、建配置，
rem  最后弹出图形向导——粘贴一把 API Key 就能开始和白聊天。
rem
rem  重复运行是安全的：已完成的步骤会自动跳过（幂等）。
rem  编码说明：本文件必须保存为 UTF-8（无 BOM），配合下一行的
rem  chcp 65001 才能正确显示中文，请勿用记事本另存为 ANSI。
rem  自动化冒烟测试可加参数 /check：只跑环境检查，不装任何东西。
rem ============================================================
chcp 65001 >nul
title White Salary - 一键安装
color 0B
cd /d "%~dp0"

set "CHECK_ONLY=0"
if /i "%~1"=="/check" set "CHECK_ONLY=1"

echo ============================================================
echo   White Salary 一键安装
echo   只需要几分钟，装完就能和白聊天了
echo ============================================================
echo.

rem ---------- [1/5] 检查 Python ----------
echo [1/5] 检查 Python ...
where python >nul 2>&1
if errorlevel 1 (
    echo   [X] 没有找到 Python。请先到官网下载安装，选 3.11 或更新版本：
    echo       https://www.python.org/downloads/
    echo       ★ 安装第一步务必勾选 "Add Python to PATH"，不勾装完也找不到！
    echo       装好 Python 后，再回来双击本脚本继续。
    pause
    exit /b 1
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo   [X] 你的 Python 版本太旧，需要 3.10 及以上（建议 3.11+）。
    echo       请到 https://www.python.org/downloads/ 安装新版本，
    echo       同样记得勾选 "Add Python to PATH"。
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo   [OK] 检测到 %%v
echo.

rem ---------- [2/5] 检查 Node.js（可选，只影响桌宠界面） ----------
echo [2/5] 检查 Node.js ...
set "NODE_OK=1"
where node >nul 2>&1
if errorlevel 1 (
    set "NODE_OK=0"
    echo   [!] 没有找到 Node.js —— 只影响桌宠小人界面，聊天后端不受影响。
    echo       想要桌面小人的话，稍后到 https://nodejs.org 装个 LTS 版即可，
    echo       装好后重新双击本脚本会自动补装前端依赖。现在先继续。
) else (
    for /f "tokens=*" %%v in ('node --version') do echo   [OK] 检测到 Node.js %%v
)
echo.

rem /check 冒烟模式：环境检查到此为止，不执行任何安装动作
if "%CHECK_ONLY%"=="1" (
    echo [检查模式] 环境检查完成，未执行任何安装动作。
    exit /b 0
)

rem ---------- [3/5] 安装 Python 依赖 ----------
echo [3/5] 安装 Python 依赖（第一次要几分钟，请耐心等待）...
python -c "import fastapi, uvicorn, pydantic, yaml, loguru, aiohttp, numpy, multipart, ddgs, openai" >nul 2>&1
if not errorlevel 1 (
    echo   [OK] Python 依赖已经装好了，跳过。
    goto :deps_done
)
python -m pip install -e .[llm-openai]
if errorlevel 1 (
    echo.
    echo   [X] 依赖安装失败。国内网络建议改用清华镜像重试——
    echo       把下面这行整个复制到本窗口，回车执行：
    echo       python -m pip install -e .[llm-openai] -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo       装完后重新双击本脚本即可。
    pause
    exit /b 1
)
echo   [OK] Python 依赖安装完成。
:deps_done
echo.

rem ---------- [4/5] 安装桌宠界面依赖（npm） ----------
echo [4/5] 安装桌宠界面依赖 ...
if "%NODE_OK%"=="0" (
    echo   [跳过] 没装 Node.js，先跳过。装好 Node 后重新双击本脚本即可补上。
    goto :npm_done
)
if exist "frontend\node_modules" (
    echo   [OK] 前端依赖已经装好了，跳过。
    goto :npm_done
)
pushd frontend
call npm install
if errorlevel 1 (
    popd
    echo   [!] 前端依赖安装失败 —— 不影响后端聊天。
    echo       稍后可进 frontend 目录手动执行 npm install 重试。
    goto :npm_done
)
popd
echo   [OK] 前端依赖安装完成。
:npm_done
echo.

rem ---------- [5/5] 准备配置文件 ----------
echo [5/5] 准备配置文件 ...
if exist "conf.yaml" (
    echo   [OK] conf.yaml 已存在，保留你现有的配置。
) else (
    copy /y "conf.default.yaml" "conf.yaml" >nul
    echo   [OK] 已从模板生成 conf.yaml。
)
if exist "prompts\system_prompt.txt" (
    echo   [OK] 白的人格提示词已存在。
) else (
    copy /y "prompts\system_prompt.example.txt" "prompts\system_prompt.txt" >nul
    echo   [OK] 已从示例生成 prompts\system_prompt.txt —— 白的人格设定。
)
echo.

echo ============================================================
echo   安装完成！马上会弹出配置向导 ——
echo   在窗口里粘贴你的 API Key 就大功告成了。
echo ============================================================
echo.
python scripts\setup_wizard.py
if errorlevel 1 (
    echo   [!] 配置向导没有正常结束。你可以稍后手动运行：
    echo       python scripts\setup_wizard.py
    echo       或用记事本打开 conf.yaml，把密钥填到 llm 节的 api_key。
    pause
    exit /b 1
)
exit /b 0
