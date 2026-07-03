"""
white_salary/core/plugins/official_plugins.py

官方内置插件库 — GitHub不可用时的离线安装源。

内置3个基础插件，新装项目第一次打开就有。
"""

OFFICIAL_PLUGINS = {
    "dice": {
        "cn_name": "骰子游戏",
        "description": "掷骰子，支持自定义面数",
        "version": "1.0.0",
        "author": "官方",
        "category": "游戏",
        "code": '''
from white_salary.core.plugins.base import Plugin, PluginMeta
import random

class DicePlugin(Plugin):
    meta = PluginMeta(name="dice", description="骰子游戏", version="1.0.0", author="官方")

    def get_tools(self):
        return [{
            "name": "roll_dice",
            "description": "掷骰子，可指定面数(默认6面)",
            "parameters": {"type":"object","properties":{"sides":{"type":"integer","description":"骰子面数","default":6}}},
            "handler": self.roll,
        }]

    async def roll(self, sides=6):
        result = random.randint(1, max(2, int(sides)))
        return f"🎲 掷出了 {result}（{sides}面骰）"
''',
    },
    "daily_fortune": {
        "cn_name": "每日运势",
        "description": "每天给你一个运势，同一天结果不变",
        "version": "1.0.0",
        "author": "官方",
        "category": "娱乐",
        "code": '''
from white_salary.core.plugins.base import Plugin, PluginMeta
import hashlib, time

class FortunePlugin(Plugin):
    meta = PluginMeta(name="daily_fortune", description="每日运势", version="1.0.0", author="官方")

    async def on_message(self, text, user_id=""):
        if "运势" not in text and "fortune" not in text.lower():
            return None
        seed = hashlib.md5(f"{user_id}_{time.strftime('%Y%m%d')}".encode()).hexdigest()
        score = int(seed[:2], 16) % 100
        levels = ["大凶", "凶", "小凶", "平", "小吉", "吉", "大吉"]
        level = levels[min(score * len(levels) // 100, len(levels) - 1)]
        lucky_num = int(seed[2:4], 16) % 10
        colors = ["红色", "蓝色", "绿色", "紫色", "金色", "白色", "橙色"]
        lucky_color = colors[int(seed[4:6], 16) % len(colors)]
        return f"🔮 今日运势: {level}（{score}分）\\n幸运数字: {lucky_num}\\n幸运色: {lucky_color}"
''',
    },
    "coin_flip": {
        "cn_name": "抛硬币",
        "description": "帮你做选择，正面或反面",
        "version": "1.0.0",
        "author": "官方",
        "category": "工具",
        "code": '''
from white_salary.core.plugins.base import Plugin, PluginMeta
import random

class CoinPlugin(Plugin):
    meta = PluginMeta(name="coin_flip", description="抛硬币", version="1.0.0", author="官方")

    async def on_message(self, text, user_id=""):
        if "抛硬币" not in text and "coin" not in text.lower():
            return None
        result = random.choice(["正面 ⭐", "反面 🌙"])
        return f"🪙 抛硬币结果: {result}"
''',
    },
}


def get_official_list() -> list[dict]:
    """获取官方插件列表（用于市场展示）。"""
    return [
        {
            "id": pid,
            "cn_name": info["cn_name"],
            "description": info["description"],
            "version": info["version"],
            "author": info["author"],
            "category": info["category"],
            "featured": True,
            "official": True,
        }
        for pid, info in OFFICIAL_PLUGINS.items()
    ]


def get_plugin_code(plugin_id: str) -> str:
    """获取官方插件代码。"""
    info = OFFICIAL_PLUGINS.get(plugin_id)
    return info["code"].strip() if info else ""
