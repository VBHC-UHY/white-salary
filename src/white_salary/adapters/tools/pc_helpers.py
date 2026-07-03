"""
white_salary/adapters/tools/pc_helpers.py

PC控制函数库 — 预置30+个高级封装函数，AI通过execute_code调用。

AI只需要写：
  click(500, 300)
  type_text("你好")
  open_app("notepad")
  smart_click("确定按钮")

不需要写import和底层逻辑。

所有函数自动注入到execute_code的执行环境中。
"""

import subprocess
import sys
import time
import os
import webbrowser
from typing import Optional, Tuple

from loguru import logger


# ================================================================
# 鼠标操作
# ================================================================

def click(x: int = None, y: int = None, button: str = "left", clicks: int = 1) -> str:
    """
    鼠标点击。
    - click() — 点击当前位置
    - click(500, 300) — 点击坐标(500,300)
    - click(500, 300, button="right") — 右键点击
    - click(500, 300, clicks=2) — 双击
    """
    import pyautogui
    pyautogui.click(x=x, y=y, button=button, clicks=clicks)
    pos = pyautogui.position()
    return f"已点击 ({pos.x}, {pos.y}) {button}键 {clicks}次"


def double_click(x: int = None, y: int = None) -> str:
    """双击。"""
    return click(x, y, clicks=2)


def right_click(x: int = None, y: int = None) -> str:
    """右键点击。"""
    return click(x, y, button="right")


def move_to(x: int, y: int, duration: float = 0.3) -> str:
    """移动鼠标到指定位置。"""
    import pyautogui
    pyautogui.moveTo(x, y, duration=duration)
    return f"鼠标已移到 ({x}, {y})"


def drag_to(x: int, y: int, duration: float = 0.5) -> str:
    """从当前位置拖拽到指定位置。"""
    import pyautogui
    pyautogui.dragTo(x, y, duration=duration)
    return f"已拖拽到 ({x}, {y})"


def scroll(amount: int, x: int = None, y: int = None) -> str:
    """
    滚动鼠标滚轮。
    - scroll(3) — 向上滚3格
    - scroll(-3) — 向下滚3格
    """
    import pyautogui
    pyautogui.scroll(amount, x=x, y=y)
    return f"已滚动 {amount} 格"


def get_mouse_pos() -> Tuple[int, int]:
    """获取当前鼠标位置。"""
    import pyautogui
    pos = pyautogui.position()
    return (pos.x, pos.y)


# ================================================================
# 键盘操作
# ================================================================

def type_text(text: str, submit: bool = False) -> str:
    """
    输入文本（通过剪贴板粘贴，支持中文）。
    - type_text("你好") — 输入文本
    - type_text("搜索内容", submit=True) — 输入后按回车
    """
    import pyautogui
    try:
        import pyperclip
        pyperclip.copy(text)
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'v')
    except ImportError:
        # 没有pyperclip，用pyautogui直接输入（不支持中文）
        pyautogui.typewrite(text, interval=0.05)

    if submit:
        time.sleep(0.3)
        pyautogui.press('enter')
        return f"已输入「{text}」并提交"
    return f"已输入「{text}」"


def hotkey(*keys) -> str:
    """
    按快捷键。
    - hotkey("ctrl", "c") — 复制
    - hotkey("ctrl", "v") — 粘贴
    - hotkey("alt", "tab") — 切换窗口
    - hotkey("ctrl", "shift", "esc") — 任务管理器
    - hotkey("win", "d") — 显示桌面
    """
    import pyautogui
    pyautogui.hotkey(*keys)
    return f"已按 {'+'.join(keys)}"


def press(key: str, times: int = 1) -> str:
    """
    按单个键。
    - press("enter") — 回车
    - press("tab") — Tab
    - press("escape") — Esc
    - press("space") — 空格
    - press("backspace") — 退格
    - press("delete") — 删除
    - press("up") / press("down") / press("left") / press("right") — 方向键
    - press("f5") — F5
    """
    import pyautogui
    for _ in range(times):
        pyautogui.press(key)
    return f"已按 {key}" + (f" {times}次" if times > 1 else "")


# ================================================================
# 应用和文件操作
# ================================================================

def open_app(name: str) -> str:
    """
    打开应用程序。
    - open_app("notepad") — 记事本
    - open_app("calc") — 计算器
    - open_app("cmd") — 命令提示符
    - open_app("explorer") — 文件资源管理器
    - open_app("taskmgr") — 任务管理器
    - open_app("mspaint") — 画图
    - open_app("D:\\\\path\\\\to\\\\app.exe") — 指定路径
    """
    try:
        if sys.platform == "win32":
            os.startfile(name)
        else:
            subprocess.Popen([name], start_new_session=True)
        return f"已打开 {name}"
    except Exception as e:
        # 尝试用start命令
        try:
            subprocess.Popen(f'start "" "{name}"', shell=True)
            return f"已打开 {name}"
        except Exception as e2:
            return f"打开失败: {e2}"


def open_url(url: str) -> str:
    """
    用默认浏览器打开网页。
    - open_url("https://www.baidu.com")
    - open_url("bilibili.com") — 自动加https
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"已打开 {url}"


def open_file(path: str) -> str:
    """用默认程序打开文件。"""
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
        return f"已打开 {path}"
    except Exception as e:
        return f"打开失败: {e}"


def open_folder(path: str) -> str:
    """打开文件夹。"""
    try:
        if sys.platform == "win32":
            subprocess.Popen(f'explorer "{path}"', shell=True)
        else:
            subprocess.Popen(["xdg-open", path])
        return f"已打开文件夹 {path}"
    except Exception as e:
        return f"打开失败: {e}"


# ================================================================
# 屏幕操作
# ================================================================

def screenshot(region: tuple = None) -> str:
    """
    截屏。
    - screenshot() — 全屏截图
    - screenshot((100, 100, 500, 400)) — 截取指定区域
    返回截图的base64编码。
    """
    import pyautogui
    import base64
    from io import BytesIO

    img = pyautogui.screenshot(region=region)
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=75)
    b64 = base64.b64encode(buffer.getvalue()).decode()
    return f"[截屏完成，{len(b64)//1024}KB]"


def get_screen_size() -> Tuple[int, int]:
    """获取屏幕分辨率。"""
    import pyautogui
    size = pyautogui.size()
    return (size.width, size.height)


def find_on_screen(image_path: str) -> Optional[Tuple[int, int]]:
    """
    在屏幕上找图片位置。
    - 返回(x, y)中心坐标，或None（没找到）
    """
    import pyautogui
    try:
        loc = pyautogui.locateCenterOnScreen(image_path, confidence=0.8)
        if loc:
            return (loc.x, loc.y)
    except Exception:
        pass
    return None


# ================================================================
# 智能操作（需要视觉模型配合）
# ================================================================

async def smart_click(element_description: str) -> str:
    """
    智能点击 — 截屏后用视觉模型找到元素位置并点击。
    - smart_click("确定按钮")
    - smart_click("搜索框")
    - smart_click("关闭按钮")
    - smart_click("地址栏")

    需要vision_llm支持。
    """
    import pyautogui
    import base64
    from io import BytesIO

    # 1. 截屏
    img = pyautogui.screenshot()
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=75)
    img_b64 = base64.b64encode(buffer.getvalue()).decode()

    # 2. 调视觉模型定位元素
    try:
        from white_salary.adapters.vision.multimodal_adapter import MultimodalVisionAdapter
        from white_salary.adapters.tools.cloud_config import resolve_vision_channel

        vc = resolve_vision_channel()
        if not vc.configured:
            return f"[智能点击失败] 视觉模型未配置"

        vision = MultimodalVisionAdapter(
            api_key=vc.api_key, base_url=vc.base_url,
            model=vc.model,
        )

        prompt = (
            f"请在图片中找到「{element_description}」的位置。"
            f"只返回坐标，格式：x,y（像素坐标，图片左上角为0,0）。"
            f"如果找不到，返回：not_found"
        )

        result = await vision.describe_image(img_b64, prompt, max_tokens=50)

        if "not_found" in result.lower():
            return f"[智能点击] 没找到「{element_description}」"

        # 解析坐标
        import re
        coords = re.findall(r'(\d+)\s*[,，]\s*(\d+)', result)
        if not coords:
            return f"[智能点击] 无法解析坐标: {result}"

        x, y = int(coords[0][0]), int(coords[0][1])

        # 3. 点击
        pyautogui.moveTo(x, y, duration=0.25)
        time.sleep(0.1)
        pyautogui.click(x, y)

        return f"已点击「{element_description}」({x}, {y})"

    except Exception as e:
        return f"[智能点击失败] {e}"


# ================================================================
# 等待
# ================================================================

def wait(seconds: float = 1.0) -> str:
    """等待指定秒数（用于等页面加载等）。最多10秒。"""
    seconds = min(seconds, 10.0)
    time.sleep(seconds)
    return f"已等待 {seconds} 秒"


# ================================================================
# 系统操作
# ================================================================

def run_command(cmd: str) -> str:
    """
    执行系统命令。
    - run_command("ipconfig")
    - run_command("dir C:\\")
    """
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30,
        )
        output = result.stdout[:500] if result.stdout else ""
        error = result.stderr[:200] if result.stderr else ""
        return output + ("\n[错误] " + error if error else "")
    except subprocess.TimeoutExpired:
        return "[命令超时]"
    except Exception as e:
        return f"[执行失败] {e}"


def get_window_list() -> str:
    """获取当前打开的窗口列表。"""
    try:
        import pyautogui
        if sys.platform == "win32":
            result = subprocess.run(
                'powershell "Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object ProcessName, MainWindowTitle | Format-Table -AutoSize"',
                shell=True, capture_output=True, text=True, timeout=5,
            )
            return result.stdout[:1000] if result.stdout else "无法获取窗口列表"
    except Exception as e:
        return f"获取失败: {e}"


# ================================================================
# 导出 — 所有函数名（注入到execute_code环境）
# ================================================================

PC_FUNCTIONS = {
    # 鼠标
    "click": click,
    "double_click": double_click,
    "right_click": right_click,
    "move_to": move_to,
    "drag_to": drag_to,
    "scroll": scroll,
    "get_mouse_pos": get_mouse_pos,
    # 键盘
    "type_text": type_text,
    "hotkey": hotkey,
    "press": press,
    # 应用
    "open_app": open_app,
    "open_url": open_url,
    "open_file": open_file,
    "open_folder": open_folder,
    # 屏幕
    "screenshot": screenshot,
    "get_screen_size": get_screen_size,
    "find_on_screen": find_on_screen,
    # 智能
    "smart_click": smart_click,
    # 等待
    "wait": wait,
    # 系统
    "run_command": run_command,
    "get_window_list": get_window_list,
}

# 生成函数说明（给AI看的）
PC_FUNCTIONS_HELP = """
可用的PC控制函数：

鼠标：
  click(x, y)              — 点击坐标（不传参=点当前位置）
  double_click(x, y)       — 双击
  right_click(x, y)        — 右键
  move_to(x, y)            — 移动鼠标
  drag_to(x, y)            — 拖拽到
  scroll(3) / scroll(-3)   — 滚轮（正=上，负=下）
  get_mouse_pos()           — 获取鼠标位置

键盘：
  type_text("文本")          — 输入文本（支持中文）
  type_text("内容", submit=True)  — 输入后按回车
  hotkey("ctrl", "c")       — 快捷键
  press("enter")            — 按键（enter/tab/escape/space/backspace/f5...）

应用：
  open_app("notepad")       — 打开应用
  open_url("baidu.com")     — 打开网页
  open_file("C:\\\\test.txt")  — 打开文件
  open_folder("C:\\\\Users")   — 打开文件夹

屏幕：
  screenshot()              — 全屏截图
  get_screen_size()         — 获取屏幕分辨率
  smart_click("确定按钮")    — 智能点击（视觉模型定位+点击）

系统：
  wait(2)                   — 等待2秒
  run_command("ipconfig")   — 执行系统命令
  get_window_list()         — 获取窗口列表
""".strip()
