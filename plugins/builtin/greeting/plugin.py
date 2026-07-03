"""
greeting 插件 — 示例插件，展示插件API的用法。

功能：当用户发送特定关键词时，触发特殊回复。
"""

from white_salary.core.plugins.base import Plugin, PluginMeta


class GreetingPlugin(Plugin):
    meta = PluginMeta(
        name="greeting",
        description="示例插件 — 特殊问候回复",
        version="1.0.0",
        author="White Salary",
    )

    async def on_load(self):
        print(f"[Plugin:{self.meta.name}] 加载完成")

    async def on_message(self, text: str, user_id: str = ""):
        """示例：不拦截任何消息。"""
        # 如果要拦截，return 一个字符串作为回复
        # 例如: if "彩蛋" in text: return "你发现了一个彩蛋！"
        return None

    async def on_reply(self, text: str) -> str:
        """示例：不修改回复，直接返回。"""
        return text
