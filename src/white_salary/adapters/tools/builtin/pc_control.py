"""
PC控制工具 — 1个入口搞定所有电脑操作。

action参数决定执行什么：
  open        — 打开应用/网页/文件
  click       — 点击坐标
  smart_click — 智能点击（视觉定位+点击）
  type        — 输入文本
  hotkey      — 快捷键
  command     — 系统命令
  scroll      — 滚动
  screenshot  — 截屏
  complex     — 多步组合操作（用模板或代码）

低级模型只需填action+target，高级模型用complex做任意操作。
"""
import asyncio
import re
import sys
import time
from ._helpers import tool, P, S, I

# 常见多步操作模板
_TEMPLATES = {
    "搜索": [
        ("open_url", "{target}"),
        ("wait", "2"),
        ("smart_click", "搜索框"),
        ("type_submit", "{query}"),
    ],
    "复制粘贴": [
        ("hotkey", "ctrl+a"),
        ("hotkey", "ctrl+c"),
        ("click", "{target}"),
        ("hotkey", "ctrl+v"),
    ],
}


def _do_open(target: str) -> str:
    """打开应用/网页/文件。"""
    if not target:
        return "请提供要打开的目标"
    # 判断是URL还是应用
    if "." in target and "/" in target or target.startswith(("http", "www")):
        import webbrowser
        url = target if target.startswith("http") else f"https://{target}"
        webbrowser.open(url)
        return f"已打开网页: {url}"
    elif "." in target and "\\" in target or "/" in target:
        # 文件路径
        import os
        try:
            os.startfile(target) if sys.platform == "win32" else None
            return f"已打开文件: {target}"
        except Exception as e:
            return f"打开失败: {e}"
    else:
        # 应用名
        import os, subprocess
        try:
            if sys.platform == "win32":
                os.startfile(target)
            else:
                subprocess.Popen([target], start_new_session=True)
            return f"已打开: {target}"
        except Exception:
            try:
                subprocess.Popen(f'start "" "{target}"', shell=True)
                return f"已打开: {target}"
            except Exception as e:
                return f"打开失败: {e}"


def _do_click(target: str) -> str:
    """点击坐标。target格式: "x,y" 或空(点当前位置)。"""
    import pyautogui
    if target and "," in target:
        parts = target.replace(" ", "").split(",")
        try:
            x, y = int(parts[0]), int(parts[1])
            pyautogui.click(x, y)
            return f"已点击 ({x}, {y})"
        except ValueError:
            return f"坐标格式错误: {target}（应为 x,y）"
    else:
        pyautogui.click()
        pos = pyautogui.position()
        return f"已点击当前位置 ({pos.x}, {pos.y})"


async def _do_smart_click(target: str) -> str:
    """智能点击——截屏+视觉定位+点击。"""
    if not target:
        return "请描述要点击的元素"
    try:
        from white_salary.adapters.tools.pc_helpers import smart_click
        return await smart_click(target)
    except Exception as e:
        return f"智能点击失败: {e}"


def _do_type(target: str, submit: bool = False) -> str:
    """输入文本。"""
    if not target:
        return "请提供要输入的文本"
    import pyautogui
    try:
        import pyperclip
        pyperclip.copy(target)
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'v')
    except ImportError:
        pyautogui.typewrite(target, interval=0.05)
    if submit:
        time.sleep(0.3)
        pyautogui.press('enter')
        return f"已输入「{target}」并提交"
    return f"已输入「{target}」"


def _do_hotkey(target: str) -> str:
    """快捷键。target格式: "ctrl+c" 或 "alt+tab"。"""
    if not target:
        return "请提供快捷键"
    import pyautogui
    keys = [k.strip() for k in target.replace("+", " ").split()]
    pyautogui.hotkey(*keys)
    return f"已按 {'+'.join(keys)}"


def _do_scroll(target: str) -> str:
    """滚动。target: 正数向上，负数向下。"""
    try:
        import pyautogui
        amount = int(target) if target else 3
        pyautogui.scroll(amount)
        return f"已滚动 {amount} 格"
    except Exception as e:
        return f"滚动失败: {e}"


def _do_command(target: str) -> str:
    """执行系统命令。"""
    if not target:
        return "请提供命令"
    import subprocess
    try:
        r = subprocess.run(target, shell=True, capture_output=True, text=True, timeout=30)
        out = r.stdout[:500] if r.stdout else ""
        err = r.stderr[:200] if r.stderr else ""
        return out + ("\n[错误] " + err if err else "") or "[执行成功]"
    except subprocess.TimeoutExpired:
        return "[命令超时]"
    except Exception as e:
        return f"执行失败: {e}"


def _do_screenshot() -> str:
    """截屏。"""
    try:
        import pyautogui
        import base64
        from io import BytesIO
        img = pyautogui.screenshot()
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=75)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"[截屏完成 {len(b64)//1024}KB]"
    except Exception as e:
        return f"截屏失败: {e}"


async def _do_complex(target: str) -> str:
    """复杂多步操作——用模板或代码。"""
    if not target:
        return "请描述要执行的操作"

    # 尝试匹配模板
    # （暂时直接走execute_code，让AI写代码）
    from white_salary.adapters.tools.code_executor import execute_python
    # 把自然语言描述转成提示
    code_hint = (
        f"# 用户要求: {target}\n"
        f"# 请用PC控制函数完成操作\n"
    )
    return await execute_python(code_hint + target)


@tool("pc_control",
      "控制用户的电脑。可以打开应用/网页、点击、输入文字、按快捷键、执行命令、智能点击屏幕元素。"
      "当用户说'帮我打开xx'、'点一下xx'、'输入xx'、'按Ctrl+C'、'帮我搜xx'时使用。",
      P(
          action=S("操作类型: open/click/smart_click/type/hotkey/scroll/command/screenshot/complex", True),
          target=S("操作目标: 应用名/URL/坐标x,y/文本/快捷键/元素描述/命令/多步描述", True),
          submit=S("输入后是否按回车(true/false)，仅type时有效"),
      ))
async def pc_control(action: str = "", target: str = "", submit: str = "false") -> str:
    if not action:
        return "请提供action参数"

    action = action.lower().strip()

    try:
        if action == "open":
            result = _do_open(target)
        elif action == "click":
            result = _do_click(target)
        elif action == "smart_click":
            result = await _do_smart_click(target)
        elif action == "type":
            result = _do_type(target, submit=submit.lower() in ("true", "1", "yes"))
        elif action == "hotkey":
            result = _do_hotkey(target)
        elif action == "scroll":
            result = _do_scroll(target)
        elif action == "command":
            result = _do_command(target)
        elif action == "screenshot":
            result = _do_screenshot()
        elif action == "complex":
            result = await _do_complex(target)
        else:
            result = f"未知操作: {action}（支持: open/click/smart_click/type/hotkey/scroll/command/screenshot/complex）"

        # 操作完成后提示可截屏确认
        if action not in ("screenshot",) and "失败" not in result:
            result += "\n[操作已执行，如需确认结果可截屏查看]"

        return result

    except ImportError as e:
        if "pyautogui" in str(e):
            return "[需要安装pyautogui] 请运行: pip install pyautogui pyperclip"
        return f"[缺少依赖] {e}"
    except Exception as e:
        return f"[操作失败] {e}"


TOOLS = [pc_control._tool_def]
