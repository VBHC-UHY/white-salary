"""基础工具 — 时间/计算器/随机数/提醒/骰子。"""
import math
import time
import random
import re
from ._helpers import tool, P, S, I, NONE_PARAMS


@tool("get_current_time", "获取当前的日期和时间")
async def get_current_time() -> str:
    now = time.strftime("%Y年%m月%d日 %H:%M:%S (%A)")
    weekdays = {"Monday": "星期一", "Tuesday": "星期二", "Wednesday": "星期三",
                "Thursday": "星期四", "Friday": "星期五", "Saturday": "星期六", "Sunday": "星期日"}
    for en, zh in weekdays.items():
        now = now.replace(en, zh)
    return f"当前时间: {now}"


@tool("calculator", "进行数学计算（加减乘除、开方、幂、三角函数）",
      P(expression=S("数学表达式，如 2+3*4 或 sqrt(144)", True)))
async def calculator(expression: str = "") -> str:
    if not expression:
        return "请提供表达式"
    if re.search(r'[a-zA-Z_]{4,}', expression.replace("sqrt","").replace("sin","").replace("cos","").replace("tan","").replace("log","").replace("abs","").replace("pow","").replace("round","").replace("pi","")):
        return f"不安全的表达式: {expression}"
    if any(kw in expression for kw in ["import", "__", "exec", "eval", "open", "os.", "sys."]):
        return f"不安全的表达式: {expression}"
    try:
        safe_env = {"__builtins__": {}, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
                    "tan": math.tan, "log": math.log, "abs": abs, "pow": pow, "round": round,
                    "pi": math.pi, "e": math.e}
        result = eval(expression, safe_env)
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算错误: {e}"


@tool("random_number", "生成指定范围内的随机整数",
      P(min=I("最小值"), max=I("最大值")))
async def random_number(min: int = 1, max: int = 100) -> str:
    return f"随机数({min}~{max}): {random.randint(min, max)}"


# 2026-07-03 工具实现（批9）：提醒三件套真实现——批2下架时是「开发中」空壳，
# 现接入 ReminderService（存储data/reminders.json + 后台调度 + 桌面/QQ双通道到点通知）。
def _get_reminder_service():
    """取进程级提醒服务单例（run_server 装配注入；未装配时懒创建默认实例）。"""
    from white_salary.core.services.reminder_service import ReminderService
    return ReminderService.get_instance()


@tool("set_reminder",
      "设置定时提醒，到点后白会主动开口/发QQ消息通知用户。"
      "用户说「提醒我三点开会」「10分钟后叫我收快递」「每天8点提醒我吃药」时使用",
      P(content=S("要提醒的事情，如「开会」", True),
        when=S("什么时候提醒，照抄用户的时间原话，如「10分钟后」「明天早上8点」「每天8点」", True)))
async def set_reminder(content: str = "", when: str = "") -> str:
    try:
        _ok, message = _get_reminder_service().add(content, when)
        return message
    except Exception as e:
        return f"设提醒时出了点问题: {e}"


@tool("cancel_reminder",
      "取消一条已设置的提醒。用户说「取消开会的提醒」「不用提醒我了」时使用",
      P(keyword=S("提醒内容的关键词或提醒ID，如「开会」", True)))
async def cancel_reminder(keyword: str = "") -> str:
    try:
        return _get_reminder_service().cancel(keyword)
    except Exception as e:
        return f"取消提醒时出了点问题: {e}"


@tool("list_reminders",
      "查看当前所有待提醒事项及时间。用户问「我设了哪些提醒」「有什么要提醒的」时使用")
async def list_reminders() -> str:
    try:
        return _get_reminder_service().describe_pending()
    except Exception as e:
        return f"查提醒列表时出了点问题: {e}"


@tool("dice_roller", "掷骰子（NdM格式，如1d6/2d20+5）",
      P(expression=S("骰子表达式", True)))
async def dice_roller(expression: str = "1d6") -> str:
    match = re.match(r"(\d+)?d(\d+)([+-]\d+)?", expression.lower().strip())
    if not match:
        return f"无法解析: {expression}（格式: 1d6, 2d20+5）"
    count, sides, mod = int(match.group(1) or 1), int(match.group(2)), int(match.group(3) or 0)
    if count > 100 or sides > 1000:
        return "数值太大"
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + mod
    return f"🎲 {expression}: {rolls} = {total}" if count > 1 else f"🎲 {expression}: {total}"


# 导出
# 2026-07-02 审计修复（批2）：下架提醒三件套（set_reminder/cancel_reminder/list_reminders）——
# 均为「开发中」空壳，注册后模型会向用户答应设提醒但实际什么都不发生
# （依据 docs/audit-2026-07-02/tools-media.json）。函数体保留，待真实现后再加回 TOOLS。
# 2026-07-03 工具实现（批9）：提醒三件套已重写为真实现（接入 ReminderService，
# 到点真通知：桌面桥让白开口 + QQ私聊兜底），加回 TOOLS。
TOOLS = [fn._tool_def for fn in [
    get_current_time, calculator, random_number, dice_roller,
    set_reminder, cancel_reminder, list_reminders,
]]
